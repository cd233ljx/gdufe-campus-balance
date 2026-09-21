# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CardsClaim contributors
"""Real Edge driver, intercepted synthetic campus pages: no school traffic."""
import json
import asyncio
import copy
import unittest
from unittest.mock import AsyncMock, patch

from cardsclaim.desktop.controller import default_config
from cardsclaim.desktop.login import local_login


class GuiLoginTests(unittest.IsolatedAsyncioTestCase):
    async def test_auth_only_closes_browser_without_room_queries(self):
        cfg = default_config()
        campus = AsyncMock()
        saved = []
        browsers = []
        async def factory(playwright):
            browser = await playwright.chromium.launch(channel='msedge', headless=True)
            browsers.append(browser)
            original = browser.new_context
            async def new_context():
                context = await original()
                async def route(request_route):
                    if '/oauth/token' in request_route.request.url:
                        await request_route.fulfill(content_type='application/json', body=json.dumps({'access_token':'login-only-test-token'}))
                    else:
                        await request_route.fulfill(content_type='text/html',body="<script>fetch('/berserker-auth/oauth/token',{method:'POST'});</script>")
                await context.route('**/*',route)
                return context
            browser.new_context = new_context
            return browser
        result = await local_login(cfg,campus,direct=True,discover=True,auth_only=True,
            checkpoint=lambda draft: saved.append(copy.deepcopy(draft)),progress=lambda _:None,
            browser_factory=factory,ttl=15)
        self.assertEqual(result[1],'login-only-test-token')
        self.assertIsNone(result[2])
        self.assertFalse(browsers[0].is_connected())
        self.assertEqual(saved[-1]['token'],'login-only-test-token')
        self.assertEqual(saved[-1]['config']['params'],{})
        campus.query.assert_not_awaited()

    async def test_close_after_login_preserves_draft_and_resumes_without_login_page(self):
        cfg = default_config()
        campus = AsyncMock()
        campus.query.side_effect = [{'amount': '24.5', 'unit': '度'}, {'amount': '5.5', 'unit': '元'}]
        snapshots = []
        saved = asyncio.Event()
        browsers = []
        resume_requests = []

        def checkpoint(data):
            snapshots.append(copy.deepcopy(data))
            saved.set()

        async def factory(playwright, resume=False):
            browser = await playwright.chromium.launch(channel='msedge', headless=True)
            browsers.append(browser)
            original_context = browser.new_context
            async def new_context():
                context = await original_context()
                if not resume:
                    await context.add_cookies([
                        {'name': 'campus-session', 'value': 'campus-only', 'url': 'https://cardsp.gdufe.edu.cn/'},
                        {'name': 'school-login', 'value': 'must-not-be-saved', 'url': 'https://authserver.gdufe.edu.cn/'}])
                async def route(request_route):
                    path = request_route.request.url
                    if resume:
                        resume_requests.append(path)
                    if '/oauth/token' in path:
                        await request_route.fulfill(content_type='application/json', body=json.dumps({'access_token': 'saved-test-token'}))
                    elif '/getThirdData' in path:
                        self.assertEqual(request_route.request.headers.get('x-test-token'), 'saved-test-token')
                        await request_route.fulfill(content_type='application/json', body=json.dumps({'map': {'surplusCharge': '24.5'}}))
                    else:
                        script = "await fetch('/berserker-auth/oauth/token',{method:'POST'});" if not resume else """
                        for(const fee of ['1','5']) await fetch('/charge/feeitem/getThirdData', {
                          method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded','X-Test-Token':sessionStorage.getItem('access_token')},
                          body:'campus=test&building=test&room=test&type=IEC&level=3&feeitemid='+fee});
                        """
                        await request_route.fulfill(content_type='text/html', body='<script>(async()=>{' + script + '})();</script>')
                await context.route('**/*', route)
                return context
            browser.new_context = new_context
            return browser

        pending = asyncio.create_task(local_login(cfg, campus, discover=True, direct=True,
            browser_factory=factory, checkpoint=checkpoint, progress=lambda _: None, ttl=20))
        await asyncio.wait_for(saved.wait(), 10)
        await asyncio.sleep(.2)
        await browsers[0].close()
        result = await pending
        self.assertIsNone(result[2])
        self.assertEqual(snapshots[-1]['token'], 'saved-test-token')
        self.assertEqual(snapshots[-1]['config']['params'], {})
        self.assertTrue(snapshots[-1]['cookies'])
        self.assertTrue(all(cookie['domain'] == 'cardsp.gdufe.edu.cn' for cookie in snapshots[-1]['cookies']))
        self.assertNotIn('must-not-be-saved', json.dumps(snapshots))
        campus.query.assert_not_awaited()

        restored = await local_login(cfg, campus, discover=True, direct=True, draft=snapshots[-1],
            browser_factory=lambda p: factory(p, True), checkpoint=checkpoint, progress=lambda _: None, ttl=20)
        self.assertEqual(set(restored[2]), set(cfg['items']))
        self.assertEqual(resume_requests[0], 'https://cardsp.gdufe.edu.cn/plat/')
        self.assertFalse(any('/oauth/token' in url or '/cas/login' in url for url in resume_requests))

    async def test_direct_browser_discovers_rooms_without_link_or_console(self):
        messages = []
        stages = []
        created = []
        cfg = default_config()
        campus = AsyncMock()
        campus.query.side_effect = [{'amount': '24.5', 'unit': '度'}, {'amount': '5.5', 'unit': '元'}]

        async def factory(playwright):
            browser = await playwright.chromium.launch(channel='msedge', headless=True)
            created.append(browser)
            new_context = browser.new_context
            async def configured_context():
                context = await new_context()
                async def route(request_route):
                    path = request_route.request.url
                    if '/oauth/token' in path:
                        body = {'access_token': 'synthetic-test-token'}
                    elif '/getThirdData' in path:
                        body = {'map': {'surplusCharge': '24.5'}}
                    else:
                        await request_route.fulfill(content_type='text/html', body='''<script>
                        (async()=>{
                          await fetch('/berserker-auth/oauth/token',{method:'POST'});
                          for(const fee of ['1','5']) await fetch('/charge/feeitem/getThirdData',{
                            method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},
                            body:'campus=test-campus&building=test-building&room=test-room&type=IEC&level=3&feeitemid='+fee});
                        })();</script>''')
                        return
                    await request_route.fulfill(content_type='application/json', body=json.dumps(body))
                await context.route('**/*', route)
                return context
            browser.new_context = configured_context
            return browser

        with patch('cardsclaim.desktop.login.webbrowser.open') as open_link, \
             patch('cardsclaim.desktop.login.web.AppRunner') as runner:
            result, token, balances = await local_login(cfg, campus, direct=True, discover=True,
                browser_factory=factory, progress=messages.append, diagnostic=stages.append, ttl=20)
        open_link.assert_not_called()
        runner.assert_not_called()
        self.assertEqual(token, 'synthetic-test-token')
        self.assertEqual(set(result['params']), set(cfg['items']))
        self.assertEqual(result['params']['tap_water']['room'], 'test-room')
        self.assertEqual(set(balances), set(cfg['items']))
        self.assertFalse(created[0].is_connected())
        self.assertTrue(any('已识别' in text for text in messages))
        self.assertTrue(any('已识别学校登录' in text for text in messages))
        self.assertNotIn('synthetic-test-token', '\n'.join(messages))
        self.assertEqual(stages[-1]['phase'], 'verified')
        self.assertNotIn('synthetic-test-token', json.dumps(stages))
