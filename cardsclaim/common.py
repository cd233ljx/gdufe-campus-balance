# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CardsClaim contributors
from decimal import Decimal, InvalidOperation

ITEMS = ('electricity', 'tap_water', 'liwang')
LABELS = {'electricity': '电费', 'tap_water': '自来水', 'liwang': '力王科技'}

def number(value):
    if isinstance(value, bool) or value is None:
        raise ValueError('余额缺失或无效')
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise ValueError('余额不是有效数字') from None
    if not result.is_finite():
        raise ValueError('余额不是有限数字')
    return result

