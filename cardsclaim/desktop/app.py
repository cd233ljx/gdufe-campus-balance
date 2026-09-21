# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CardsClaim contributors
import argparse
import asyncio
import copy
import ctypes
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from datetime import datetime

import aiohttp
from ..api import Campus, QueryError
from ..common import ITEMS, LABELS, number
from .model import TZ, accept, due, fail, query_all, validate
from .store import DesktopStore

ERRORS = {'auth': '登录已失效，请选择浏览器登录 / 续期', 'network': '网络连接失败，下一轮查询会重试',
          'business': '学校接口未接受查询，请重新选择房间', 'parse': '余额格式无法识别，请勿把它当成零'}


def say(text):
    if sys.stdout:
        print(text, flush=True)


def ask(label, default=None):
    answer = input(label + (f' [{default}]' if default is not None else '') + '：').strip()
    return answer or default or ''


def settings(store):
    old = store.read('account', {})
    cfg = copy.deepcopy(old.get('config', {}))
    cfg['query_time'] = ask('每日查询时间（北京时间 HH:MM）', cfg.get('query_time', '22:00'))
    selected = ask('监控项目：1 电量、2 自来水、3 力王热水（空格分隔）', ' '.join(str(ITEMS.index(k)+1) for k in cfg.get('items', ITEMS[:2])))
    try:
        cfg['items'] = [ITEMS[int(k)-1] for k in selected.split() if k in ('1','2','3')]
        if len(cfg['items']) != len(selected.split()):
            raise ValueError()
    except (ValueError, IndexError):
        raise ValueError('请输入项目编号，例如 1 2') from None
    thresholds = cfg.setdefault('thresholds', {})
    for item in cfg['items']:
        unit = '度' if item == 'electricity' else '元'
        default = thresholds.get(item, {'electricity':'20','tap_water':'1','liwang':None}[item])
        answer = ask(f'{LABELS[item]}低于多少{unit}提醒（off 不提醒）', 'off' if default is None else str(default))
        thresholds[item] = None if answer.lower() == 'off' else str(number(answer))
    cfg['liwang_basis'] = 'cash'
    cfg.setdefault('params', {})
    validate(cfg, require_params=False)
    old['config'] = cfg
    store.write('account', old)
    state = store.read('state', {})
    for item in ITEMS:
        state.setdefault('active', {}).pop(item, None)
        state.setdefault('pending', {}).pop(item, None)
    state.pop('last_day', None)
    store.write('state', state)
    say('设置已保存。力王按现金余额判断，赠送金额不计入。首次使用请继续选择浏览器登录。')


def show_balances(state):
    say('最近状态：' + state.get('status', '未查询'))
    if state.get('last_success'):
        say('最近成功时间：' + state['last_success'])
    for item, value in state.get('balances', {}).items():
        say(f"{LABELS[item]}：{value['amount']} {value['unit']}")
    if state.get('status') != 'ok' and state.get('balances'):
        say('以上为历史余额。')


def session():
    return aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30, connect=10), trust_env=False)


async def login(store, discover=False):
    from .login import local_login
    account = store.read('account', {})
    cfg = validate(account.get('config', {}), require_params=False)
    discover = discover or any(k not in cfg.get('params', {}) for k in cfg['items'])
    async with session() as http:
        campus = Campus(http, cfg)
        cfg, token, balances = await local_login(cfg, campus, discover=discover)
    if discover:
        say('\n已识别你在校园卡页面实际查询成功的项目：')
        for item in cfg['items']:
            values = cfg['params'][item]
            details = ('手机号尾号 ' + values['telPhone'][-4:]) if item == 'liwang' else f"{values['campus']} / 楼栋编号 {values['building']} / 房间编号 {values['room']}"
            say(f'{LABELS[item]}：{details}')
        show_balances({'status':'ok', 'balances':balances})
        if ask('确认这些是你要监控的房间 / 账户？y/N', 'N').lower() != 'y':
            say('未保存本次凭证和房间设置。请重新登录并选择正确的房间。')
            return
    store.write('account', {'config':validate(cfg), 'token':token})
    state = store.read('state', {})
    if discover:
        state['active'] = {}
        state['pending'] = {}
    accept(cfg, state, balances)
    store.write('state', state)
    say('登录及所有所选项目的实际查询已通过，凭证已加密保存。')
    show_balances(state)


async def check(store, cfg, token, state):
    state['last_attempt'] = datetime.now(TZ).isoformat()
    async with session() as http:
        try:
            balances = await query_all(Campus(http, cfg), cfg, token)
            accept(cfg, state, balances)
        except QueryError as error:
            fail(state, error)
            say(ERRORS[error.kind])
    store.write('state', state)
    return state.get('status') == 'ok'


def child_command(background=False):
    if getattr(sys, 'frozen', False):
        return [sys.executable]
    python = Path(sys.executable)
    if background:
        python = python.with_name('pythonw.exe')
    return [str(python), '-m', 'cardsclaim.desktop.app']


async def deliver(store, state):
    # A child owns the native dialog; the monitor continues querying while it is open.
    for key, text in list(state.get('pending', {}).items()):
        if state['pending'].get(key) != text:
            continue
        say('提醒：' + text)
        try:
            notice = 'notice-' + secrets.token_hex(16)
            store.write(notice, {'text': text})
            process = await asyncio.create_subprocess_exec(*child_command(True), '--notify', notice,
                        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                await process.wait()
            finally:
                if process.returncode is None:
                    process.terminate()
                    await process.wait()
                (store.root / notice).unlink(missing_ok=True)
            if process.returncode == 0 and state.get('pending', {}).get(key) == text:
                del state['pending'][key]
                store.write('state', state)
        except OSError:
            return  # keep event; retry after a bounded delay


def account_ready(store):
    account = store.read('account', {})
    if not account.get('token') and store.read('login-draft', {}).get('token'):
        raise ValueError('学校登录已保存，请在窗口中点击“继续选择房间”完成设置')
    validate(account.get('config', {}))
    if not account.get('token'):
        raise ValueError('请先选择浏览器登录 / 续期')
    return account


async def monitor(store, background=False, instance=None):
    account = account_ready(store)
    cfg, token = account['config'], account['token']
    instance = instance or secrets.token_hex(16)
    store.write('process', {'instance':instance, 'pid':os.getpid()})
    state = store.read('state', {})
    delivery = None
    retry_at = 0
    try:
        say(f"监控已启动，每天北京时间 {cfg['query_time']} 查询。按 B 切换后台，按 Q 返回菜单。")
        while True:
            control = store.read('stop', {})
            if control.get('instance') == instance:
                return False
            if not background:
                import msvcrt
                if msvcrt.kbhit():
                    key = msvcrt.getwch().lower()
                    if key in ('q', 'b'):
                        return key == 'b'
            if due(cfg, state):
                state['last_day'] = datetime.now(TZ).date().isoformat()
                store.write('state', state)
                await check(store, cfg, token, state)
                if not background:
                    show_balances(state)
            if delivery is not None and delivery.done():
                await delivery
                delivery = None
                retry_at = time.monotonic() + 60
            if state.get('pending') and delivery is None and time.monotonic() >= retry_at:
                delivery = asyncio.create_task(deliver(store, state))
            await asyncio.sleep(1)
    finally:
        if delivery:
            delivery.cancel()
            await asyncio.gather(delivery, return_exceptions=True)


def stop(store):
    if not store.running():
        say('监控未运行。')
        return
    info = store.read('process', {})
    store.write('stop', {'instance':info.get('instance')})
    deadline = time.monotonic() + 120
    while store.running() and time.monotonic() < deadline:
        time.sleep(.2)
    if store.running():
        raise ValueError('仍在结束当前查询，请稍后再次停止')
    say('监控已停止。')


def background(store):
    with store.lock():
        account_ready(store)
    instance = secrets.token_hex(16)
    process = subprocess.Popen([*child_command(True), '--background', '--instance', instance], cwd=str(Path(__file__).resolve().parents[2]),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=subprocess.CREATE_NO_WINDOW)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        if store.running() and store.read('process', {}).get('instance') == instance:
            break
        time.sleep(.2)
    if process.poll() is not None or not store.running() or store.read('process', {}).get('instance') != instance:
        raise ValueError('后台启动失败，请先尝试前台监控')
    say('已切换到后台，可以关闭终端。再次双击启动程序可查看状态或停止。电脑关闭 / 睡眠时无法查询。')


def main():
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and not stream.isatty() and hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='CardsClaim Windows 本机版')
    parser.add_argument('--background', action='store_true')
    parser.add_argument('--instance', help=argparse.SUPPRESS)
    parser.add_argument('--console', action='store_true', help='使用旧版终端菜单')
    parser.add_argument('--rooms', action='store_true', help='打开软件内房间选择')
    parser.add_argument('--gui-self-test', metavar='REPORT', help=argparse.SUPPRESS)
    parser.add_argument('--notify')
    parser.add_argument('--self-test', action='store_true', help='离线检查加密、后台进程和浏览器驱动，不访问学校')
    args = parser.parse_args()
    if os.name != 'nt':
        parser.exit(2, '此入口用于 Windows 10/11；服务器请继续使用 manage.sh。\n')
    if args.self_test:
        from .selftest import run
        asyncio.run(run())
        return
    if args.gui_self_test:
        from .gui_smoke import run
        run(args.gui_self_test)
        return
    if args.notify:
        if not args.notify.startswith('notice-') or len(args.notify) != 39 or any(c not in '0123456789abcdef' for c in args.notify[7:]):
            parser.exit(2)
        text = DesktopStore().read(args.notify, {}).get('text', 'CardsClaim')
        result = ctypes.windll.user32.MessageBoxW(None, text, 'CardsClaim 余额提醒', 0x40 | 0x10000)
        raise SystemExit(0 if result else 1)
    store = DesktopStore()
    if args.background:
        try:
            with store.lock():
                asyncio.run(monitor(store, True, args.instance))
        except Exception:
            # Fixed metadata only: no tracebacks, request bodies, tokens or URLs.
            store.write('background-error', {'message':'后台运行失败，请用前台模式检查设置', 'time':datetime.now(TZ).isoformat()})
        return
    if not args.console:
        from .gui import main as window_main
        window_main(choose_rooms=args.rooms)
        return
    say('CardsClaim Windows 本机版\n无需服务器、Tailscale 或飞书。密码和邮箱验证码只在学校浏览器页面输入。')
    while True:
        say('\n1 设置时间 / 阈值 / 项目\n2 浏览器登录 / 续期\n3 立即查询\n4 前台监控\n5 后台监控\n6 停止监控\n7 查看状态\n8 重新选择房间\n0 退出菜单（不停止已启动的后台监控）')
        try:
            choice = ask('请选择', '7')
            if choice == '0':
                return
            if choice == '6':
                stop(store)
                continue
            if choice == '7':
                say('运行状态：' + ('监控运行中' if store.running() else '已停止'))
                cfg = store.read('account', {}).get('config', {})
                if cfg:
                    say('每日时间（北京时间）：' + cfg['query_time'])
                    for item in cfg['items']:
                        threshold = cfg['thresholds'][item]
                        say(LABELS[item] + ('：不提醒' if threshold is None else f'：低于 {threshold} 提醒'))
                show_balances(store.read('state', {}))
                continue
            if choice == '5':
                background(store)
                continue
            switch = False
            with store.lock():
                if choice == '1':
                    settings(store)
                elif choice in ('2', '8'):
                    if not store.read('account', {}).get('config'):
                        settings(store)
                    asyncio.run(login(store, discover=choice == '8'))
                elif choice == '3':
                    account = account_ready(store)
                    state = store.read('state', {})
                    asyncio.run(check(store, account['config'], account['token'], state))
                    show_balances(state)
                    asyncio.run(deliver(store, state))
                elif choice == '4':
                    switch = asyncio.run(monitor(store))
            if switch:
                background(store)
        except EOFError:
            return
        except (ValueError, KeyboardInterrupt) as error:
            say(str(error) if isinstance(error, ValueError) else '操作已取消。')
        except Exception:
            say('操作未完成。请检查网络与 Edge / Chrome 安装后重试；原有凭证不会被未验证的登录覆盖。')


if __name__ == '__main__':
    main()
