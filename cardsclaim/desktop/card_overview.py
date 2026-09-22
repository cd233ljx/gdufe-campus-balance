"""Read-only campus card overview. Retain only display fields, never raw identity data."""
from datetime import datetime
from decimal import Decimal
import re

from ..api import QueryError, response_json
from .catalog import checked

URL = 'https://cardsp.gdufe.edu.cn/berserker-app/ykt/tsm/queryCard'


def cents(value):
    # Captured school frontend uses integer cents; reject missing/unknown values.
    if type(value) is not int:
        raise QueryError('parse')
    return value


def money(value):
    return format(Decimal(value) / 100, '.2f')


def expiry(value):
    if not value:
        return '未返回'
    if not isinstance(value, str):
        raise QueryError('parse')
    text = value.strip()
    for pattern in ('%Y%m%d', '%Y-%m-%d', '%Y%m%d%H%M%S', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(text, pattern).strftime('%Y-%m-%d')
        except ValueError:
            pass
    return '日期格式待确认'


def parse_cards(payload):
    checked(payload)
    data = payload.get('data')
    if not isinstance(data, dict) or str(data.get('retcode')) != '0':
        raise QueryError('business')
    cards = data.get('card')
    if not isinstance(cards, list):
        raise QueryError('parse')
    results = []
    for index, card in enumerate(cards):
        if not isinstance(card, dict):
            raise QueryError('parse')
        db = cents(card.get('db_balance'))
        unsettled = cents(card.get('unsettle_amount'))
        electronic = cents(card.get('elec_accamt'))
        lost, frozen = card.get('lostflag'), card.get('freezeflag')
        if type(lost) is not int or type(frozen) is not int:
            raise QueryError('parse')
        title = card.get('card_name') or card.get('cardname') or f'校园卡 {index+1}'
        if not isinstance(title, str):
            raise QueryError('parse')
        results.append({'label': title[:60], 'balance': money(db + unsettled),
                        'unsettled': money(unsettled), 'electronic': money(electronic),
                        'status': ('已挂失' if lost > 0 else '未挂失') + ' · ' + ('已冻结' if frozen > 0 else '未冻结'),
                        'expiry': expiry(card.get('expdate'))})
    return results


async def query(http, token):
    if not isinstance(token, str) or not token or '\n' in token or '\r' in token:
        raise QueryError('auth')
    async with http.get(URL, headers={'synjones-auth': 'Bearer ' + token}, allow_redirects=False) as response:
        if response.status in (401, 403) or 300 <= response.status < 400:
            raise QueryError('auth')
        if response.status != 200:
            raise QueryError('network')
        return parse_cards(await response_json(response))
