"""QQ SMTP delivery with separate, persistent low-balance episodes."""
import asyncio
import re
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from .model import TZ
from ..common import LABELS, number


def credentials(address, code):
    address = address.strip().lower()
    code = ''.join(code.split())
    if not re.fullmatch(r'[a-z0-9._+-]+@qq\.com', address):
        raise ValueError('请填写完整的 QQ 邮箱地址，例如 123456@qq.com')
    if not re.fullmatch(r'[A-Za-z0-9]+', code):
        raise ValueError('请填写 QQ 邮箱的 SMTP 授权码，不是 QQ 登录密码')
    return {'address': address, 'code': code, 'enabled': True}


def send(config, subject, body):
    message = EmailMessage()
    message['From'] = config['address']
    message['To'] = config['address']
    message['Subject'] = subject
    message['Date'] = formatdate(localtime=True)
    message['Message-ID'] = make_msgid()
    message.set_content(body)
    try:
        with smtplib.SMTP_SSL('smtp.qq.com', 465, timeout=15, context=ssl.create_default_context()) as smtp:
            smtp.login(config['address'], config['code'])
            smtp.send_message(message)
    except smtplib.SMTPAuthenticationError:
        raise ValueError('QQ 邮箱验证失败，请确认已开启 SMTP 服务，并填写授权码而非登录密码。') from None
    except (OSError, smtplib.SMTPException):
        raise ValueError('邮件未能发送，请检查网络、QQ 邮箱 SMTP 服务及授权码后重试。') from None


def queue_alerts(store, cfg, state):
    if not store.read('email', {}).get('enabled') or state.get('status') != 'ok':
        return
    mail = store.read('email-state', {})
    active, pending = mail.setdefault('active', {}), mail.setdefault('pending', {})
    for item in LABELS:
        value = state.get('balances', {}).get(item)
        threshold = cfg['thresholds'].get(item)
        low = item in cfg['items'] and value and threshold is not None and number(value['amount']) < number(threshold)
        if low and not active.get(item):
            room = cfg.get('room_labels', {}).get(item, '')
            pending[item] = (f"{LABELS[item]} {room}\n余额：{value['amount']} {value['unit']}\n"
                             f"提醒阈值：{threshold} {value['unit']}\n查询时间：{state['last_success']}\n"
                             '持续低余额期间不重复提醒；恢复后再次低于阈值会重新提醒。')
        if not low:
            pending.pop(item, None)
        active[item] = bool(low)
    if not pending:
        mail.pop('error', None)
    store.write('email-state', mail)


async def deliver(store):
    config = store.read('email', {})
    if not config.get('enabled'):
        return
    mail = store.read('email-state', {})
    pending = mail.get('pending', {})
    if not pending:
        return
    try:
        await asyncio.to_thread(send, config, '校园余额不足提醒', '\n\n'.join(pending.values()))
    except ValueError as error:
        mail['error'] = str(error)
    else:
        mail['pending'] = {}
        mail.pop('error', None)
        mail['last_sent'] = datetime.now(TZ).isoformat()
    store.write('email-state', mail)
