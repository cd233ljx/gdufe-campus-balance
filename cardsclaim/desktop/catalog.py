# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CardsClaim contributors
"""Read-only campus binding and cascading location selectors."""
import asyncio
import re
import aiohttp
from ..api import QueryError, response_json, BALANCE_URL

ITEM_IDS = {'electricity': '1', 'tap_water': '5', 'liwang': '7'}
FIELDS = ('campus', 'building', 'room')
DETAIL_URL = 'https://cardsp.gdufe.edu.cn/charge/feeitem/singleFeeitem'


def checked(data):
    if not isinstance(data, dict):
        raise QueryError('parse')
    code = str(data.get('code', ''))
    message = str(data.get('msg', data.get('message', ''))).lower()
    if code in ('401', '403') or any(x in message for x in ('未登录', '登录失效', '登录过期', 'token失效', 'token过期', 'invalid token', 'invalid_token', 'unauthorized')):
        raise QueryError('auth')
    if data.get('success') is False or code not in ('0', '200'):
        raise QueryError('business')
    return data


def parse_binding(data, item):
    checked(data)
    fee = data.get('feeitem')
    if not isinstance(fee, dict) or str(fee.get('feeitemid')) != ITEM_IDS[item]:
        raise QueryError('parse')
    # A selector's first option is NOT an account binding.
    if str(fee.get('bindStatus')) != '1':
        return {}
    scene = data.get('sceneinfo')
    if not isinstance(scene, str) or not scene.strip():
        return {}
    allowed = ('telPhone',) if item == 'liwang' else FIELDS
    found = {}
    for segment in scene.split(';'):
        if not segment.strip():
            continue
        if ':' not in segment:
            return {}
        key, encoded = segment.split(':', 1)
        if key not in allowed or key in found:
            return {}  # ambiguous/unknown bindings require explicit selection
        parts = encoded.split('#$#', 1)
        value = parts[0].strip()
        if not value:
            return {}
        found[key] = value
    return found if set(found) == set(allowed) else {}


def parse_choices(data):
    checked(data)
    mapping = data.get('map')
    if not isinstance(mapping, dict) or not isinstance(mapping.get('data'), list):
        raise QueryError('parse')
    result, seen = [], set()
    for row in mapping['data']:
        if not isinstance(row, dict) or not isinstance(row.get('name'), str) or not row['name'].strip():
            raise QueryError('parse')
        value = row.get('value')
        if type(value) not in (str, int) or not str(value).strip() or str(value) in seen:
            raise QueryError('parse')
        seen.add(str(value))
        result.append({'name': row['name'].strip(), 'value': str(value)})
    return result


class Catalog:
    def __init__(self, session, token):
        self.session, self.token = session, token

    async def request(self, url, *, params=None, data=None):
        if not isinstance(self.token, str) or not self.token or '\n' in self.token or '\r' in self.token:
            raise QueryError('auth')
        for attempt in range(2):
            try:
                method = self.session.get if data is None else self.session.post
                arguments = {'params': params} if data is None else {'data': data}
                async with method(url, **arguments, headers={'synjones-auth': 'Bearer ' + self.token}, allow_redirects=False) as response:
                    if response.status in (401, 403) or 300 <= response.status < 400:
                        raise QueryError('auth')
                    if response.status == 429 or response.status >= 500:
                        raise QueryError('network')
                    if response.status != 200:
                        raise QueryError('business')
                    return checked(await response_json(response))
            except (aiohttp.ClientError, asyncio.TimeoutError):
                error = QueryError('network')
            except QueryError as exc:
                error = exc
            if error.kind != 'network' or attempt:
                raise error
            await asyncio.sleep(2)

    async def bound(self, item):
        return parse_binding(await self.request(DETAIL_URL, params={'feeitemid': ITEM_IDS[item]}), item)

    async def choices(self, item, field, parents):
        level = FIELDS.index(field)
        required = FIELDS[:level]
        if any(not parents.get(key) for key in required):
            raise ValueError('请先选择校区和楼栋')
        data = {'feeitemid': ITEM_IDS[item], 'type': 'select', 'level': str(level)}
        data.update({key: parents[key] for key in required})
        return parse_choices(await self.request(BALANCE_URL, data=data))

    async def initial(self, cfg):
        result = {}
        for item in cfg['items']:
            bound = await self.bound(item)
            source = '学校已绑定' if bound else '本机已保存'
            previous = cfg.get('params', {}).get(item, {})
            selected = bound or previous
            row = {'source': source if selected else '请选择', 'selected': {}, 'options': {}}
            if item == 'liwang':
                phone = selected.get('telPhone', '')
                row['selected']['telPhone'] = phone if re.fullmatch(r'1\d{10}', phone) else ''
            else:
                for field in FIELDS:
                    options = await self.choices(item, field, row['selected'])
                    row['options'][field] = options
                    value = selected.get(field)
                    if value not in {option['value'] for option in options}:
                        if value:
                            row['source'] = '原绑定已不可用，请重新选择'
                        break
                    row['selected'][field] = value
            result[item] = row
        return result


def selection_params(item, values):
    fields = ('telPhone',) if item == 'liwang' else FIELDS
    if any(not isinstance(values.get(key), str) or not values[key].strip() for key in fields):
        raise ValueError('请完成所有项目的房间或手机号选择')
    if item == 'liwang' and not re.fullmatch(r'1\d{10}', values['telPhone']):
        raise ValueError('请输入力王账户的 11 位手机号')
    return {**{key: values[key] for key in fields}, 'type': 'IEC',
            'level': '1' if item == 'liwang' else '3', 'feeitemid': ITEM_IDS[item]}
