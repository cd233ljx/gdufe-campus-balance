"""Small, append-only telemetry receiver. Run behind the existing local Caddy route."""
import argparse
import csv
import json
import os
import re
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


TZ = timezone(timedelta(hours=8))
KINDS = frozenset({
    'participation_started', 'active_open', 'setup_complete', 'feature_selected',
    'query_success_manual', 'query_success_automatic', 'alert_delivered_popup',
    'alert_delivered_email',
})
FEATURES = frozenset({'', 'electricity', 'tap_water', 'liwang', 'network'})
HEX_ID = re.compile(r'[0-9a-f]{32}\Z')
VERSION = re.compile(r'\d+\.\d+\.\d+\Z')
DAY = re.compile(r'\d{4}-\d{2}-\d{2}\Z')
EVENT_FIELDS = frozenset({'event_id', 'device_id', 'day', 'version', 'kind', 'feature'})
INSTALL_FIELDS = frozenset({'event_id', 'day', 'version'})
MAX_BODY = 32768
RATE_LOCK = threading.Lock()
RATE_BUCKETS = {}


def connect(path):
    db = sqlite3.connect(path, timeout=10)
    db.execute('PRAGMA busy_timeout=10000')
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE IF NOT EXISTS events ('
               'event_id TEXT PRIMARY KEY, device_id TEXT NOT NULL, day TEXT NOT NULL, '
               'version TEXT NOT NULL, kind TEXT NOT NULL, feature TEXT NOT NULL)')
    db.execute('CREATE INDEX IF NOT EXISTS events_day ON events(day)')
    db.execute('CREATE INDEX IF NOT EXISTS events_device ON events(device_id)')
    db.execute('CREATE TABLE IF NOT EXISTS deleted_devices ('
               'device_id TEXT PRIMARY KEY, deleted_at TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS installations ('
               'event_id TEXT PRIMARY KEY, day TEXT NOT NULL, version TEXT NOT NULL)')
    db.execute('CREATE INDEX IF NOT EXISTS installations_day ON installations(day)')
    return db


def valid_event(value, today=None):
    if not isinstance(value, dict) or set(value) != EVENT_FIELDS:
        return False
    if any(type(value[key]) is not str for key in EVENT_FIELDS):
        return False
    if not HEX_ID.fullmatch(value['event_id']) or not HEX_ID.fullmatch(value['device_id']):
        return False
    if not VERSION.fullmatch(value['version']) or value['kind'] not in KINDS or value['feature'] not in FEATURES:
        return False
    if not DAY.fullmatch(value['day']):
        return False
    try:
        date = datetime.strptime(value['day'], '%Y-%m-%d').date()
    except ValueError:
        return False
    today = today or datetime.now(TZ).date()
    return today - timedelta(days=29) <= date <= today + timedelta(days=1)


def purge_old(db):
    today = datetime.now(TZ).date()
    db.execute('DELETE FROM events WHERE day < ?', ((today - timedelta(days=364)).isoformat(),))
    db.execute('DELETE FROM installations WHERE day < ?', ((today - timedelta(days=364)).isoformat(),))
    db.execute('DELETE FROM deleted_devices WHERE deleted_at < ?', ((today - timedelta(days=31)).isoformat(),))


def insert_installation(path, value):
    if not isinstance(value, dict) or set(value) != INSTALL_FIELDS:
        raise ValueError('invalid installation')
    if any(type(value[key]) is not str for key in INSTALL_FIELDS):
        raise ValueError('invalid installation')
    if not HEX_ID.fullmatch(value['event_id']) or not VERSION.fullmatch(value['version']):
        raise ValueError('invalid installation')
    try:
        day = datetime.strptime(value['day'], '%Y-%m-%d').date()
    except (ValueError, TypeError):
        raise ValueError('invalid installation') from None
    today = datetime.now(TZ).date()
    if not today - timedelta(days=29) <= day <= today + timedelta(days=1):
        raise ValueError('invalid installation')
    with closing(connect(path)) as db, db:
        purge_old(db)
        db.execute('INSERT OR IGNORE INTO installations VALUES (?, ?, ?)',
                   (value['event_id'], value['day'], value['version']))


def insert_events(path, events):
    if not isinstance(events, list) or not 1 <= len(events) <= 50 or not all(valid_event(e) for e in events):
        raise ValueError('invalid events')
    with closing(connect(path)) as db, db:
        purge_old(db)
        for e in events:
            db.execute('INSERT OR IGNORE INTO events SELECT ?, ?, ?, ?, ?, ? WHERE NOT EXISTS '
                       '(SELECT 1 FROM deleted_devices WHERE device_id = ?)',
                       (e['event_id'], e['device_id'], e['day'], e['version'], e['kind'], e['feature'], e['device_id']))
    return len(events)


def delete_device(path, device_id):
    if type(device_id) is not str or not HEX_ID.fullmatch(device_id):
        raise ValueError('invalid device')
    with closing(connect(path)) as db, db:
        db.execute('DELETE FROM events WHERE device_id = ?', (device_id,))
        db.execute('INSERT OR REPLACE INTO deleted_devices VALUES (?, ?)',
                   (device_id, datetime.now(TZ).date().isoformat()))


class Handler(BaseHTTPRequestHandler):
    db_path = None

    def log_message(self, format, *args):
        # Never log request bodies, IP addresses, device IDs, or profile data.
        pass

    def do_GET(self):
        if self.path != '/healthz':
            return self.send_error(404)
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.end_headers()
        self.wfile.write(b'ok')

    def do_POST(self):
        if self.path not in ('/v1/events', '/v1/delete', '/v1/install'):
            return self.send_error(404)
        # Cloudflare sets this header at the public edge. Keep only a short-lived
        # in-memory counter; IP addresses are never written to the database/log.
        remote = self.headers.get('CF-Connecting-IP', self.client_address[0])[:64]
        now = time.monotonic()
        with RATE_LOCK:
            if len(RATE_BUCKETS) > 10000:
                RATE_BUCKETS.clear()
            window, count = RATE_BUCKETS.get(remote, (now, 0))
            if now - window >= 60:
                window, count = now, 0
            RATE_BUCKETS[remote] = (window, count + 1)
            limited = count >= 120
        if limited:
            return self.send_error(429)
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= MAX_BODY or self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise ValueError('invalid request')
            data = json.loads(self.rfile.read(length))
            if self.path == '/v1/events':
                if not isinstance(data, dict) or set(data) != {'events'}:
                    raise ValueError('invalid request')
                insert_events(self.db_path, data['events'])
            elif self.path == '/v1/delete':
                if not isinstance(data, dict) or set(data) != {'device_id'}:
                    raise ValueError('invalid request')
                delete_device(self.db_path, data['device_id'])
            else:
                insert_installation(self.db_path, data)
        except (ValueError, UnicodeError, json.JSONDecodeError):
            return self.send_error(400)
        self.send_response(204)
        self.end_headers()


def export(path, output, start, end):
    if not DAY.fullmatch(start) or not DAY.fullmatch(end) or start > end:
        raise ValueError('invalid date range')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    with closing(connect(path)) as db:
        rows = db.execute('SELECT day, device_id, kind, feature FROM events WHERE day BETWEEN ? AND ? ORDER BY day',
                          (start, end)).fetchall()
        installs = db.execute('SELECT day, COUNT(*) FROM installations WHERE day BETWEEN ? AND ? GROUP BY day',
                              (start, end)).fetchall()
    daily = {}
    for day, device, kind, feature in rows:
        entry = daily.setdefault(day, {'devices': set(), 'active': set(), 'monitoring': set(), 'queries': 0, 'alerts': 0, 'installs': 0})
        entry['devices'].add(device)
        if kind in ('active_open', 'query_success_manual'):
            entry['active'].add(device)
        if kind == 'query_success_automatic':
            entry['monitoring'].add(device)
        if kind in ('query_success_manual', 'query_success_automatic'):
            entry['queries'] += 1
        if kind in ('alert_delivered_popup', 'alert_delivered_email'):
            entry['alerts'] += 1
    for day, count in installs:
        daily.setdefault(day, {'devices': set(), 'active': set(), 'monitoring': set(),
                               'queries': 0, 'alerts': 0, 'installs': 0})['installs'] = count
    all_devices = set().union(*(entry['devices'] for entry in daily.values())) if daily else set()
    all_active = set().union(*(entry['active'] for entry in daily.values())) if daily else set()
    all_monitoring = set().union(*(entry['monitoring'] for entry in daily.values())) if daily else set()
    with (output / 'daily.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.writer(stream)
        writer.writerow(['日期', '参与统计的设备数', '主动使用设备数', '后台监控设备数', '查询成功次数', '提醒送达次数', '安装完成次数'])
        for day, e in sorted(daily.items()):
            writer.writerow([day, len(e['devices']), len(e['active']), len(e['monitoring']),
                             e['queries'], e['alerts'], e['installs']])
        writer.writerow(['合计去重', len(all_devices), len(all_active), len(all_monitoring),
                         sum(e['queries'] for e in daily.values()), sum(e['alerts'] for e in daily.values()),
                         sum(e['installs'] for e in daily.values())])
    features = {}
    for _, device, kind, feature in rows:
        if kind == 'feature_selected' and feature:
            features.setdefault(feature, set()).add(device)
    with (output / 'features.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.writer(stream)
        writer.writerow(['关注功能', '参与统计的设备数'])
        for feature in sorted(features):
            writer.writerow([feature, len(features[feature])])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', default=os.environ.get('METRICS_DB', '/srv/data/campus-metrics/metrics.sqlite3'))
    sub = parser.add_subparsers(dest='command', required=True)
    serve = sub.add_parser('serve')
    serve.add_argument('--host', default='127.0.0.1')
    serve.add_argument('--port', type=int, default=18110)
    report = sub.add_parser('export')
    report.add_argument('--start', required=True)
    report.add_argument('--end', required=True)
    report.add_argument('--output', required=True)
    args = parser.parse_args()
    if args.command == 'export':
        export(args.db, args.output, args.start, args.end)
    else:
        Handler.db_path = args.db
        with closing(connect(args.db)) as db, db:
            purge_old(db)
        def cleanup():
            while True:
                time.sleep(3600)
                with closing(connect(args.db)) as db, db:
                    purge_old(db)
        threading.Thread(target=cleanup, daemon=True).start()
        server = ThreadingHTTPServer((args.host, args.port), Handler)
        server.serve_forever()


if __name__ == '__main__':
    main()
