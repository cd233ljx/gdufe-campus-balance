"""Campus authentication with explicit uncertainty and serialized retries.

No import-time requests. All diagnostics are fixed messages, never response bodies,
request URLs, exception strings, account names or addresses.
"""
import ipaddress
import json
import logging
import re
import socket
import threading
import time
from dataclasses import dataclass

import contextlib
import urllib.request
import urllib.error
from urllib.parse import urlencode

GATEWAY = '100.64.13.17'
PORTAL = f'http://{GATEWAY}/'
LOGIN_URL = f'http://{GATEWAY}:801/eportal/portal/login'
LOG = logging.getLogger('cardsclaim.network')
PROBES = (
    ('https://www.msftconnecttest.com/connecttest.txt', 200, 'Microsoft Connect Test'),
    ('https://cp.cloudflare.com/generate_204', 204, ''),
)


@dataclass(frozen=True)
class Result:
    code: str
    message: str

    @property
    def online(self):
        return self.code == 'online'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def direct_session():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    return contextlib.nullcontext(opener)


def local_ip():
    # Route towards the campus gateway, not a public DNS server or a VPN default.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect((GATEWAY, 801))
        address = sock.getsockname()[0]
    parsed = ipaddress.ip_address(address)
    if (parsed.is_unspecified or parsed.is_loopback or parsed.is_link_local or parsed.is_multicast
            or parsed in ipaddress.ip_network('198.18.0.0/15')):
        raise ValueError('No usable campus route')
    return address


def read_response(session, url, **kwargs):
    params = kwargs.get('params')
    if params:
        url += '?' + urlencode(params)
    request = urllib.request.Request(url, headers=kwargs.get('headers', {}))
    try:
        response = session.open(request, timeout=5)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        content = response.read(65537)
        if len(content) > 65536:
            raise ValueError('Response too large')
        return response.code, content.decode('utf-8-sig', errors='replace')


def inspect_network(session):
    for index, (url, status, expected) in enumerate(PROBES):
        try:
            code, body = read_response(session, url)
            if code == status and body.strip() == expected:
                LOG.info('probe=%d outcome=online', index)
                return Result('online', '网络已连通')
            LOG.info('probe=%d outcome=unexpected_response http=%d', index, code)
        except (urllib.error.URLError, OSError, ValueError):
            LOG.info('probe=%d outcome=unavailable', index)
    # Probe failures alone are not evidence that submitting credentials is useful.
    try:
        code, body = read_response(session, PORTAL)
        lower = body.lower()
        if code == 200 and ('eportal' in lower or 'drcom' in lower):
            LOG.info('portal=page80 outcome=recognized')
            return Result('portal', '公网探测未通过，校园认证入口可达')
        LOG.info('portal=page80 outcome=unrecognized http=%d', code)
    except (urllib.error.URLError, OSError, ValueError):
        LOG.info('portal=page80 outcome=unavailable')
    return Result('unknown', '网络状态不明或校园认证入口不可达；等待恢复')


def parse_login(text):
    text = text.strip()
    if text.startswith('dr1003'):
        match = re.fullmatch(r'dr1003\s*\((.*)\)\s*;?', text, re.DOTALL)
        if not match:
            raise ValueError('Invalid JSONP')
        text = match.group(1)
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError('Invalid response shape')
    return value


def campus_ip(session):
    # The portal reports the address seen by the access controller. This also
    # works behind a dorm router, where the local interface has a different IP.
    code, body = read_response(session, PORTAL)
    if code != 200 or not any(marker in body.lower() for marker in ('eportal', 'drcom')):
        raise ValueError('Unrecognized portal')
    match = re.search(r'\bv4ip\s*=\s*[\"\x27]([^\"\x27]*)[\"\x27]', body)
    if match:
        address = ipaddress.IPv4Address(match.group(1))
        if address.is_unspecified or address.is_loopback or address.is_link_local or address.is_multicast:
            raise ValueError('Unusable portal address')
        LOG.info('login_address_source=portal')
        return str(address)
    LOG.info('login_address_source=gateway_route')
    return local_ip()


def authenticate(session, config, stop, ip_provider=None, probe=inspect_network):
    if stop.is_set():
        return Result('cancelled', '已停止')
    try:
        address = ip_provider() if ip_provider is not None else campus_ip(session)
    except (OSError, ValueError, urllib.error.URLError):
        return Result('route', '无法确认校园认证 IP；未提交登录')
    params = {
        'callback': 'dr1003', 'login_method': '1',
        'user_account': ',0,' + config['student_id'],
        'user_password': config['password'], 'wlan_user_ip': address,
        'wlan_user_ipv6': '', 'wlan_user_mac': '000000000000',
        'wlan_ac_ip': '100.64.13.18', 'wlan_ac_name': '',
        'jsVersion': '4.1.3', 'terminal_type': '1', 'lang': 'zh-cn', 'v': '5464',
    }
    if stop.is_set():
        return Result('cancelled', '已停止')
    try:
        code, body = read_response(session, LOGIN_URL, params=params,
                                   headers={'Referer': PORTAL})
        if code != 200:
            return Result('http', '认证入口响应异常；稍后重试')
        response = parse_login(body)
    except TimeoutError:
        return Result('timeout', '登录请求超时；稍后重新检查网络')
    except urllib.error.URLError:
        return Result('transport', '无法连接认证服务；稍后重试')
    except (ValueError, TypeError):
        return Result('format', '认证响应格式无法识别；稍后重试')
    result = response.get('result')
    success = (type(result) is int and result == 1) or result == '1'
    # Even an "already online" response must be followed by an actual probe.
    for delay in (1, 2, 4) if success else (1,):
        if stop.wait(delay):
            return Result('cancelled', '已停止')
        if probe(session).online:
            return Result('online', '网络已连通')
    if success:
        return Result('authenticated', '校园网认证成功，正在确认外网连接')
    message = str(response.get('msg', response.get('message', ''))).lower()
    if any(word in message for word in ('密码错误', '密码不正确', '账号不存在', '用户不存在', 'password error')):
        return Result('rejected', '账号或密码未通过验证；请检查设置后重试')
    if any(word in message for word in ('验证码', '短信验证', '二次认证', 'captcha')):
        return Result('rejected', '学校要求人工验证，请在学校网页完成认证')
    if any(word in message for word in ('已在线', '已登录', '已经登录', 'already online')):
        return Result('authenticated', '学校返回账号已在线，正在确认外网连接')
    return Result('temporary', '认证暂未通过，将稍后重试')


class Reconnector:
    def __init__(self, probe=inspect_network, login=authenticate,
                 session_factory=direct_session, clock=time.monotonic):
        self.probe, self.login = probe, login
        self.session_factory, self.clock = session_factory, clock
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.failures = 0
        self.misses = 0
        self.next_attempt = 0
        self.paused = False

    def reset(self):
        # Caller holds lock when replacing configuration.
        self.failures = self.misses = 0
        self.next_attempt = 0
        self.paused = False

    def tick(self, config, manual=False, diagnose=False):
        if not self.lock.acquire(blocking=False):
            return Result('busy', '正在检测或登录，请稍候')
        try:
            if self.stop.is_set():
                return Result('cancelled', '已停止')
            with self.session_factory() as session:
                state = self.probe(session)
                if state.online:
                    self.reset()
                    return state
                if diagnose:
                    return state
                if state.code != 'portal':
                    self.misses = 0
                    return state
                self.misses += 1
                if not config.get('student_id') or not config.get('password'):
                    return Result('setup', '请先配置校园网账号')
                if not manual:
                    if not config.get('auto_login', False):
                        return Result('disabled', '自动登录未开启')
                    if self.paused:
                        return Result('rejected', '自动登录已暂停，请检查账号后点击立即登录')
                    if self.misses < 2:
                        return Result('confirming', '等待下一次检测确认，避免短暂波动触发登录')
                    if self.clock() < self.next_attempt:
                        return Result('waiting', '等待重试；避免频繁提交登录')
                result = self.login(session, config, self.stop)
                if result.online:
                    self.reset()
                elif result.code != 'cancelled':
                    self.failures += 1
                    self.next_attempt = self.clock() + min(300, 30 * 2 ** min(self.failures - 1, 4))
                    self.paused = result.code == 'rejected' or (self.failures >= 5 and result.code != 'authenticated')
                    if result.code == 'authenticated':
                        self.failures = 0
                        self.next_attempt = self.clock() + 300
                    elif self.paused and result.code != 'rejected':
                        return Result('rejected', '连续五次认证未通过，已暂停；请检查设置后重试')
                return result
        except Exception:
            # Preserve monitor liveness without logging potentially secret exception text.
            self.next_attempt = self.clock() + 60
            return Result('internal', '本次检测异常；稍后重试（日志不记录敏感内容）')
        finally:
            self.lock.release()
