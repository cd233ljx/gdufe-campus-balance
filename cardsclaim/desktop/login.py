# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CardsClaim contributors
"""Short-lived localhost link opens a fresh, visible local Edge/Chrome context."""
import asyncio
import copy
import hmac
import json
import secrets
import sys
import time
import webbrowser
from urllib.parse import parse_qs, urlsplit

from aiohttp import web
from playwright.async_api import async_playwright
from ..api import BALANCE_URL, LOGIN_URL, QueryError, parse_balance
from ..common import LABELS
from .model import FEE_ITEMS, REQUIRED, query_all

TOKEN_PATH = '/berserker-auth/oauth/token'
PORTAL_URL = 'https://cardsp.gdufe.edu.cn/plat/'


def campus_url(url, path):
    try:
        parts = urlsplit(url)
        return parts.scheme == 'https' and parts.hostname == 'cardsp.gdufe.edu.cn' and parts.port in (None, 443) and parts.path == path
    except ValueError:
        return False


def capture_params(url, form, body):
    if not campus_url(url, urlsplit(BALANCE_URL).path):
        return None
    values = parse_qs(form or '', max_num_fields=40)
    if any(len(v) != 1 for v in values.values()):
        return None
    values = {k: v[0] for k, v in values.items()}
    item = FEE_ITEMS.get(values.get('feeitemid'))
    if not item or any(not values.get(key, '').strip() for key in REQUIRED[item]):
        return None
    parse_balance(item, body)
    return item, {key: values[key] for key in REQUIRED[item]}


PAGE = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>GDUFE Campus Balance 本机登录</title>
<style>body{font:18px system-ui;max-width:650px;margin:10vh auto;padding:24px;line-height:1.8}button{padding:14px;font:inherit}#status{white-space:pre-line}</style>
<h1>GDUFE Campus Balance 本机登录</h1><p>点击后会打开独立的 Edge / Chrome 窗口。请在那个窗口完成学校登录；初次设置时，再进入需要监控的缴费项目，选择自己的房间并查询余额。</p><p>学校要求邮箱验证时正常完成；出现确认页面时可选择“仅一次”。密码和验证码不由本程序保存。</p>
<button id="start">打开学校登录窗口</button><p id="status"></p><script src="/app.js"></script></html>'''
JS = '''const key=location.hash.slice(1);history.replaceState(null,'',location.pathname);const s=document.getElementById('status');
document.getElementById('start').onclick=async()=>{try{const r=await fetch('/login',{method:'POST',headers:{'X-Login-Key':key}});s.textContent=r.ok?'请在新打开的学校窗口操作。完成后回到 GDUFE Campus Balance 终端确认。':'链接已失效，请回到终端重新登录。';}catch{s.textContent='本次登录已结束，请返回终端。';}};'''


def login_app(capability, deadline, started, authority):
    @web.middleware
    async def protect(request, handler):
        host = authority()
        if request.host != host or request.remote != '127.0.0.1' or time.monotonic() >= deadline:
            raise web.HTTPForbidden()
        if request.method == 'POST':
            if request.headers.get('Origin') != f'http://{host}' or not hmac.compare_digest(request.headers.get('X-Login-Key', ''), capability):
                raise web.HTTPForbidden()
        response = await handler(request)
        response.headers.update({'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
                                 'Content-Security-Policy': "default-src 'self'; style-src 'unsafe-inline'; frame-ancestors 'none'"})
        return response

    async def start(request):
        started.set()  # idempotent: multiple clicks never spawn extra browsers
        return web.Response(text='started')

    app = web.Application(middlewares=[protect], client_max_size=1024)
    async def index(request):
        return web.Response(text=PAGE, content_type='text/html')
    async def script(request):
        return web.Response(text=JS, content_type='application/javascript')
    app.router.add_get('/', index)
    app.router.add_get('/app.js', script)
    app.router.add_post('/login', start)
    return app


async def local_login(cfg, campus, *, discover=False, browser_factory=None, open_link=True, ttl=900,
                      direct=False, progress=None, diagnostic=None, draft=None, checkpoint=None, auth_only=False):
    def report(text):
        if progress:
            progress(text)
        elif sys.stdout:
            print(text, flush=True)
    config = copy.deepcopy(cfg)
    if discover:
        config['params'] = {k: copy.deepcopy(v) for k, v in (draft or {}).get('config', {}).get('params', {}).items()
                            if k in config['items']}
    capability = secrets.token_urlsafe(32)
    deadline = time.monotonic() + ttl
    started = asyncio.Event()
    port = 0

    runner = None
    if direct:
        started.set()
    else:
        app = login_app(capability, deadline, started, lambda: f'127.0.0.1:{port}')
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        link = f'http://127.0.0.1:{port}/#{capability}'
        report('请在本机浏览器打开（15 分钟内有效）：\n' + link)
        if open_link:
            await asyncio.to_thread(webbrowser.open, link)
    browser = None
    tasks = set()
    candidate = (draft or {}).get('token')
    rejected_tokens = set()
    saved_cookies = (draft or {}).get('cookies', [])
    def persist():
        if checkpoint and candidate:
            checkpoint({'token': candidate, 'config': copy.deepcopy(config), 'cookies': saved_cookies})
    async def remember(context):
        nonlocal saved_cookies
        # Save the token first: closing the browser must not race cookie reading.
        persist()
        try:
            saved_cookies = [cookie for cookie in await context.cookies(PORTAL_URL)
                             if cookie.get('domain', '').lstrip('.') == 'cardsp.gdufe.edu.cn']
        except Exception:
            return
        persist()
    def trace(phase, error=None):
        if diagnostic:
            diagnostic({'phase': phase, 'token_seen': bool(candidate),
                        'identified_items': list(config.get('params', {})),
                        'requested_items': list(config['items']), 'error_type': error})
    def login_progress():
        trace('login_detected')
        remaining = [LABELS[k] for k in config['items'] if k not in config.get('params', {})]
        if auth_only:
            report('登录成功，正在返回软件…')
        elif remaining:
            report(('登录成功，已加密保存。' if checkpoint else '已识别学校登录。') + '下一步：在学校窗口进入 ' + '、'.join(remaining) + '，选择你的房间并查询余额。')
        else:
            report('已识别学校登录，正在验证余额…')
    try:
        trace('opening_browser')
        async with asyncio.timeout(ttl):
            await started.wait()
            async with async_playwright() as playwright:
                if browser_factory:
                    browser = await browser_factory(playwright)
                else:
                    for channel in ('msedge', 'chrome'):
                        try:
                            browser = await playwright.chromium.launch(channel=channel, headless=False)
                            break
                        except Exception:
                            continue
                    if browser is None:
                        raise ValueError('无法启动浏览器，请安装或更新 Microsoft Edge / Google Chrome 后重试')
                try:
                    context = await browser.new_context()
                    if candidate:
                        cookies = [cookie for cookie in saved_cookies
                                   if cookie.get('domain', '').lstrip('.') == 'cardsp.gdufe.edu.cn']
                        if cookies:
                            await context.add_cookies(cookies)
                        # Restore only the campus-card token, never school passwords
                        # or another origin's storage. Revalidate before monitoring.
                        await context.add_init_script(script="if (location.origin === 'https://cardsp.gdufe.edu.cn' && !sessionStorage.getItem('access_token')) { sessionStorage.setItem('access_token', " + json.dumps(candidate) + "); }")
                    async def capture(response):
                        nonlocal candidate
                        try:
                            if response.status != 200:
                                return
                            if campus_url(response.url, TOKEN_PATH):
                                body = await response.json()
                                token = body.get('access_token') if isinstance(body, dict) else None
                                if isinstance(token, str) and 0 < len(token) <= 16384 and token not in rejected_tokens:
                                    candidate = token
                                    await remember(context)
                                    login_progress()
                            elif discover and campus_url(response.url, urlsplit(BALANCE_URL).path):
                                result = capture_params(response.url, response.request.post_data, await response.json())
                                if result and result[0] in config['items']:
                                    config['params'][result[0]] = result[1]
                                    persist()
                                    trace('identifying_rooms')
                                    remaining = [LABELS[k] for k in config['items'] if k not in config['params']]
                                    report('已识别 ' + LABELS[result[0]] + ('；接下来请查询 ' + '、'.join(remaining) if remaining else '；正在验证余额…'))
                        except Exception:
                            pass  # never report raw response bodies, URLs, or tokens
                    def received(response):
                        task = asyncio.create_task(capture(response))
                        tasks.add(task)
                        task.add_done_callback(tasks.discard)
                    context.on('response', received)
                    page = await context.new_page()
                    trace('loading_school')
                    await page.goto(PORTAL_URL if candidate else LOGIN_URL, wait_until='domcontentloaded', timeout=60000)
                    trace('waiting_for_login_or_rooms')
                    if candidate:
                        login_progress()
                    else:
                        report('请在学校窗口完成登录。' + ('然后依次进入所选缴费项目，选择房间并查询余额。' if discover else '成功后会自动返回。'))
                    last_attempt = None
                    while browser.is_connected() and context.pages:
                        if not candidate:
                            for current in context.pages:
                                if campus_url(current.url, urlsplit(current.url).path):
                                    try:
                                        token = await asyncio.wait_for(current.evaluate("sessionStorage.getItem('access_token')"), 2)
                                        if isinstance(token, str) and 0 < len(token) <= 16384 and token not in rejected_tokens:
                                            candidate = token
                                            await remember(context)
                                            login_progress()
                                    except Exception:
                                        pass
                        if auth_only and candidate:
                            persist()
                            trace('authenticated')
                            return config, candidate, None
                        signature = (candidate, json.dumps(config.get('params', {}), sort_keys=True))
                        if candidate and all(k in config.get('params', {}) for k in config['items']) and signature != last_attempt:
                            last_attempt = signature
                            campus.cfg = config
                            try:
                                trace('verifying_balances')
                                balances = await query_all(campus, config, candidate)
                                trace('verified')
                                return config, candidate, balances
                            except QueryError as error:
                                trace('verification_failed', error.kind)
                                if error.kind == 'auth' and checkpoint:
                                    rejected_tokens.add(candidate)
                                    checkpoint(None)
                                    candidate = None
                                report('验证尚未通过：' + {'auth':'登录失效，请重新登录', 'network':'网络异常，请退出本次登录后重试', 'business':'查询参数未被接受，请重新选择房间', 'parse':'余额格式无法识别'}.get(error.kind, '查询失败'))
                        await asyncio.sleep(1)
                    if candidate and checkpoint:
                        persist()
                        trace('saved_waiting_for_setup')
                        return config, candidate, None
                    raise ValueError('学校窗口已关闭，本次尚未取得登录凭证。请重新打开登录。')
                finally:
                    for task in list(tasks):
                        task.cancel()
                    await asyncio.gather(*list(tasks), return_exceptions=True)
                    await browser.close()
    except TimeoutError:
        trace('failed', 'timeout')
        raise ValueError('登录链接或会话已超时，请重试') from None
    except asyncio.CancelledError:
        trace('cancelled')
        raise
    except Exception as error:
        trace('failed', type(error).__name__)
        raise
    finally:
        if runner is not None:
            await runner.cleanup()
