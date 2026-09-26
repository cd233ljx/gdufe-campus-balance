"""Encrypted daily query records, independent of account/room changes."""
import copy
import json
from datetime import date, datetime, timedelta

from .model import TZ
from ..common import number


RETENTION_DAYS = 365


def prune(store, today):
    """Keep 365 days of dated history, including today."""
    cutoff = today - timedelta(days=RETENTION_DAYS - 1)
    for path in store.root.glob('history-????-??-??'):
        try:
            day = date.fromisoformat(path.name[8:])
        except ValueError:
            continue
        if day < cutoff and path.is_file():
            path.unlink()
    return cutoff


def record(store, cfg, balances, status='ok', source='manual', failed_item=None):
    now = datetime.now(TZ)
    stamp = now.isoformat()
    cutoff = prune(store, now.date()).isoformat()
    name = 'history-' + stamp[:10]
    records = store.read(name, [])
    previous = {key: value for key, value in store.read('history-last', {}).items()
                if value.get('time', '')[:10] >= cutoff}
    rows = []
    for item in cfg['items']:
        identity = json.dumps([item, cfg['params'].get(item), cfg.get('liwang_basis')], sort_keys=True)
        value = balances.get(item)
        params = cfg['params'].get(item, {})
        room = ('手机号尾号 ' + str(params.get('telPhone', ''))[-4:] if item == 'liwang' else
                ' / '.join(str(params.get(k, '')) for k in ('campus', 'building', 'room')))
        row = {'item': item, 'room': cfg.get('room_labels', {}).get(item, room),
            'status': 'ok' if value else status if item == failed_item or not failed_item else 'not_queried'}
        if value:
            row.update(copy.deepcopy(value))
            last = previous.get(identity)
            if last and last['unit'] == value['unit']:
                row['change'] = str(number(value['amount']) - number(last['amount']))
                row['previous_time'] = last['time']
            previous[identity] = dict(value, time=stamp)
        rows.append(row)
    records.append({'time': stamp, 'source': source, 'rows': rows})
    store.write(name, records)
    store.write('history-last', previous)


def recent(store, day='', limit=1000):
    if day:
        try:
            if datetime.strptime(day, '%Y-%m-%d').strftime('%Y-%m-%d') != day:
                raise ValueError()
        except ValueError:
            raise ValueError('日期请填 YYYY-MM-DD，例如 2026-09-21') from None
    paths = sorted(store.root.glob('history-????-??-??'), reverse=True)
    result = []
    for path in paths:
        if day and path.name != 'history-' + day:
            continue
        result.extend(reversed(store.read(path.name, [])))
        if len(result) >= limit:
            break
    return result[:limit]


def query_range(store, start, end, page=0, page_size=100):
    """Inclusive day range, newest first; paginate without truncating matches."""
    try:
        first = datetime.strptime(start, '%Y-%m-%d').date()
        last = datetime.strptime(end, '%Y-%m-%d').date()
    except ValueError:
        raise ValueError('请选择开始日期和结束日期') from None
    if first > last:
        raise ValueError('开始日期不能晚于结束日期，请重新选择。')
    if page < 0 or page_size < 1:
        raise ValueError('分页参数无效')
    start, end = first.isoformat(), last.isoformat()
    offset, total, result = page * page_size, 0, []
    for path in sorted(store.root.glob('history-????-??-??'), reverse=True):
        if not start <= path.name[8:] <= end:
            continue
        records = store.read(path.name, [])
        count = len(records)
        if total + count > offset and len(result) < page_size:
            skip = max(0, offset - total)
            result.extend(list(reversed(records))[skip:skip + page_size - len(result)])
        total += count
    return result, total
