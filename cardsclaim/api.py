# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CardsClaim contributors
import asyncio
import json
import aiohttp
from .common import ITEMS, number

BALANCE_URL = 'https://cardsp.gdufe.edu.cn/charge/feeitem/getThirdData'
LOGIN_URL = 'https://cardsp.gdufe.edu.cn/berserker-auth/cas/login/wisedu?targetUrl=https://cardsp.gdufe.edu.cn/plat/?name=loginTransit'

class QueryError(Exception):
    """Only a fixed classification is exposed; never include raw responses."""
    def __init__(self, kind):
        self.kind = kind
        super().__init__(kind)


def parse_balance(item, data, basis='cash'):
    if not isinstance(data, dict):
        raise QueryError('parse')
    code = str(data.get('code', ''))
    message = str(data.get('message', data.get('msg', ''))).lower()
    if code in ('401', '403') or any(s in message for s in ('token失效', 'token过期', '未登录', '登录失效', '登录过期', 'invalid_token', 'invalid token', 'token expired', 'unauthorized')):
        raise QueryError('auth')
    if data.get('success') is False or (code and code not in ('0', '200')):
        raise QueryError('business')
    values = data.get('map')
    if not isinstance(values, dict):
        raise QueryError('parse')
    try:
        if item != 'liwang':
            return {'amount': str(number(values.get('surplusCharge'))), 'unit': '度' if item == 'electricity' else '元'}
        raw = values.get('showData')
        # The exact showData schema must be confirmed with sanitized evidence.
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                raise ValueError('unknown showData format') from None
        if isinstance(raw, list):
            parsed = {}
            for row in raw:
                if not isinstance(row, dict):
                    raise ValueError('invalid row')
                key = row.get('name', row.get('key', row.get('label')))
                if key in parsed:
                    raise ValueError('duplicate row')
                parsed[key] = row.get('value')
            raw = parsed
        if not isinstance(raw, dict):
            raise ValueError('unknown showData format')
        cash = number(raw.get('现金金额（单位：元）'))
        gift = number(raw.get('赠送金额（单位：元）'))
        return {'amount': str(cash if basis == 'cash' else cash + gift), 'cash': str(cash), 'gift': str(gift), 'unit': '元', 'basis': basis}
    except (ValueError, TypeError):
        raise QueryError('parse') from None


async def response_json(response):
    if response.content_length and response.content_length > 1024 * 1024:
        raise QueryError('parse')
    body = bytearray()
    async for chunk in response.content.iter_chunked(65536):
        body.extend(chunk)
        if len(body) > 1024 * 1024:
            raise QueryError('parse')
    try:
        return json.loads(body)
    except (ValueError, UnicodeError):
        raise QueryError('parse') from None


class Campus:
    def __init__(self, session, cfg):
        self.session, self.cfg = session, cfg

    async def query(self, item, token):
        if not isinstance(token, str) or not token or len(token) > 16384 or '\n' in token or '\r' in token:
            raise QueryError('auth')
        for attempt in range(3):
            try:
                async with self.session.post(BALANCE_URL, data=self.cfg['params'][item], headers={'synjones-auth': 'Bearer ' + token}, allow_redirects=False) as response:
                    if response.status in (401, 403) or 300 <= response.status < 400:
                        raise QueryError('auth')
                    if response.status == 429 or response.status >= 500:
                        raise QueryError('network')
                    if response.status != 200:
                        raise QueryError('business')
                    return parse_balance(item, await response_json(response), self.cfg['liwang_basis'])
            except (aiohttp.ClientError, asyncio.TimeoutError):
                error = QueryError('network')
            except QueryError as exc:
                error = exc
            if error.kind != 'network' or attempt == 2:
                raise error
            await asyncio.sleep((3, 10)[attempt])

    async def all(self, token):
        results = {}
        for item in ITEMS:
            try:
                results[item] = await self.query(item, token)
            except QueryError as error:
                error.item = item
                raise
        return results
