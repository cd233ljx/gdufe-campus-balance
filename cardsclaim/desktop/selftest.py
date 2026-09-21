# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CardsClaim contributors
"""Offline Windows installation checks, isolated from the user's account."""
import asyncio
import ctypes
from datetime import datetime
import os
from pathlib import Path
import tempfile
import subprocess
import secrets

from .store import DesktopStore


async def run():
    from .app import background, stop, child_command
    from .model import TZ
    from playwright.async_api import async_playwright

    original = os.environ.get('CARDSCLAIM_DATA_DIR')
    with tempfile.TemporaryDirectory(prefix='CardsClaim 中文 smoke ') as directory:
        os.environ['CARDSCLAIM_DATA_DIR'] = directory
        store = DesktopStore()
        try:
            value = {'text': '离线加密检查'}
            store.write('probe', value)
            assert store.read('probe') == value
            assert '离线'.encode() not in (store.root / 'probe').read_bytes()
            with store.lock():
                assert store.running()
            assert not store.running()
            print('PASS DPAPI and exclusive lock', flush=True)
            # Mark today handled: this child must never query the school.
            cfg = {'query_time': '23:59', 'items': ['electricity'],
                   'thresholds': {'electricity': None}, 'liwang_basis': 'cash',
                   'params': {'electricity': dict(campus='test', building='test', room='test',
                                                 type='IEC', level='3', feeitemid='1')}}
            store.write('account', {'config': cfg, 'token': 'offline-test-only'})
            store.write('state', {'last_day': datetime.now(TZ).date().isoformat()})
            background(store)
            assert store.running()
            try:
                background(store)
                raise AssertionError('duplicate monitor accepted')
            except ValueError:
                pass
            stop(store)
            assert not store.running()
            print('PASS background child, duplicate rejection and stop', flush=True)
            notice = 'notice-' + secrets.token_hex(16)
            notice_text = 'CardsClaim 离线通知自检（自动关闭）' + notice[-8:]
            store.write(notice, {'text': notice_text})
            child = subprocess.Popen([*child_command(True), '--notify', notice],
                                     creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                from ctypes import wintypes
                user32 = ctypes.WinDLL('user32', use_last_error=True)
                user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
                user32.FindWindowW.restype = wintypes.HWND
                user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
                user32.GetDlgItem.argtypes = [wintypes.HWND, ctypes.c_int]
                user32.GetDlgItem.restype = wintypes.HWND
                user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
                callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
                user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
                matched = []
                @callback_type
                def match_window(window, _):
                    title = ctypes.create_unicode_buffer(128)
                    user32.GetWindowTextW(window, title, 128)
                    if title.value == 'CardsClaim 余额提醒':
                        text = ctypes.create_unicode_buffer(256)
                        user32.GetWindowTextW(user32.GetDlgItem(window, 65535), text, 256)
                        if text.value == notice_text:
                            matched.append(window)
                    return True
                for _ in range(100):
                    user32.EnumWindows(match_window, 0)
                    if matched:
                        # Some Windows hosts expose the sole acknowledgement
                        # button as IDCANCEL (2), rather than IDOK (1).
                        button_id = 1 if user32.GetDlgItem(matched[0], 1) else 2
                        button = user32.GetDlgItem(matched[0], button_id)
                        user32.PostMessageW(matched[0], 0x111, button_id, button)
                        break
                    await asyncio.sleep(.1)
                else:
                    raise AssertionError('native notification did not appear')
                assert await asyncio.to_thread(child.wait, 30) == 0
                print('PASS native Unicode notification and acknowledgement', flush=True)
            finally:
                if child.poll() is None:
                    child.terminate()
                    child.wait(timeout=10)
            async with async_playwright() as playwright:
                browser = None
                for channel in ('msedge', 'chrome'):
                    try:
                        browser = await playwright.chromium.launch(channel=channel, headless=True)
                        break
                    except Exception:
                        continue
                if browser is None:
                    raise RuntimeError('Edge / Chrome driver smoke test failed')
                try:
                    page = await browser.new_page()
                    await page.set_content('<title>CardsClaim offline</title><p>中文正常</p>')
                    assert await page.title() == 'CardsClaim offline'
                    assert await page.locator('p').inner_text() == '中文正常'
                    print('PASS bundled Playwright driver and local browser', flush=True)
                finally:
                    await browser.close()
        finally:
            if store.running():
                stop(store)
            if original is None:
                os.environ.pop('CARDSCLAIM_DATA_DIR', None)
            else:
                os.environ['CARDSCLAIM_DATA_DIR'] = original
    print('PASS all offline installation checks', flush=True)
