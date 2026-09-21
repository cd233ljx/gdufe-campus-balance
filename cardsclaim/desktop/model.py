# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CardsClaim contributors
from datetime import datetime, timezone, timedelta
from ..common import ITEMS, LABELS, number
from ..api import QueryError

TZ = timezone(timedelta(hours=8))
REQUIRED = {
    'electricity': ('campus', 'building', 'room', 'type', 'level', 'feeitemid'),
    'tap_water': ('campus', 'building', 'room', 'type', 'level', 'feeitemid'),
    'liwang': ('telPhone', 'type', 'level', 'feeitemid'),
}
FEE_ITEMS = {'1': 'electricity', '5': 'tap_water', '7': 'liwang'}


def validate(cfg, require_params=True):
    try:
        datetime.strptime(cfg['query_time'], '%H:%M')
        if len(cfg['query_time']) != 5:
            raise ValueError()
        items = cfg['items']
        if not items or len(set(items)) != len(items) or any(k not in ITEMS for k in items):
            raise ValueError()
        if cfg['liwang_basis'] not in ('cash', 'total'):
            raise ValueError()
        for item in items:
            threshold = cfg['thresholds'][item]
            if threshold is not None and number(threshold) < 0:
                raise ValueError()
            if require_params:
                if any(not isinstance(cfg['params'][item].get(k), str) or not cfg['params'][item][k].strip() for k in REQUIRED[item]):
                    raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise ValueError('请完成查询时间、项目、阈值和房间设置') from None
    return cfg


async def query_all(campus, cfg, token):
    result = {}
    for item in cfg['items']:
        try:
            result[item] = await campus.query(item, token)
        except QueryError as error:
            error.item = item
            raise
    return result


def due(cfg, state, now=None):
    now = now or datetime.now(TZ)
    return state.get('last_day') != now.date().isoformat() and now.strftime('%H:%M') >= cfg['query_time']


def accept(cfg, state, balances):
    state.update(status='ok', balances=balances, last_success=datetime.now(TZ).isoformat())
    active = state.setdefault('active', {})
    pending = state.setdefault('pending', {})
    pending.pop('auth', None)
    active.pop('auth', None)
    for item in ITEMS:
        threshold = cfg['thresholds'].get(item)
        if item not in cfg['items'] or threshold is None:
            active.pop(item, None)
            pending.pop(item, None)
            continue
        value = balances[item]
        low = number(value['amount']) < number(threshold)
        if low and not active.get(item):
            pending[item] = f"{LABELS[item]}余额 {value['amount']} {value['unit']}，低于设定的 {threshold} {value['unit']}。"
        if not low:
            pending.pop(item, None)
        active[item] = low


def fail(state, error):
    state['status'] = error.kind
    if error.kind == 'auth' and not state.setdefault('active', {}).get('auth'):
        state['active']['auth'] = True
        state.setdefault('pending', {})['auth'] = '学校登录已过期。请打开 CardsClaim，点击“重新登录”即可恢复自动监控。'
