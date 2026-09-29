import csv
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import closing
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from cardsclaim.desktop.store import DesktopStore
from cardsclaim.desktop.telemetry import Telemetry
from cardsclaim.desktop.install_counts import InstallationCounter
from cardsclaim.desktop.model import TZ
from server.metrics_service import Handler, connect, delete_device, export, insert_events, insert_installation, valid_event


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = DesktopStore(self.root, codec=lambda data, decrypt=False: data)
        self.sent = []

    def sender(self, path, data):
        self.sent.append((path, data))

    def test_installer_opt_in_applies_once_and_existing_choice_wins(self):
        with patch('cardsclaim.desktop.telemetry.installation', return_value={'telemetryoptin': 'yes'}):
            metrics = Telemetry(self.store, sender=self.sender, auto_flush=False)
            self.assertTrue(metrics.status()['enabled'])
            first_id = self.store.read('telemetry')['device_id']
            Telemetry(self.store, sender=self.sender, auto_flush=False)
            self.assertEqual(self.store.read('telemetry')['device_id'], first_id)
            metrics.choose(False)
            Telemetry(self.store, sender=self.sender, auto_flush=False)
            self.assertFalse(self.store.read('telemetry')['enabled'])

    def test_consent_queue_retry_and_delete(self):
        metrics = Telemetry(self.store, sender=self.sender, auto_flush=False)
        self.assertFalse(metrics.record('active_open'))
        self.assertFalse(self.store.read('telemetry', {}).get('device_id'))
        metrics.choose(True)
        metrics.record('active_open')
        metrics.record('query_success_automatic')
        self.assertEqual(len(self.store.read('telemetry')['queue']), 3)
        self.assertTrue(metrics.flush())
        self.assertEqual(self.sent[0][0], '/v1/events')
        self.assertEqual(len(self.sent[0][1]['events']), 3)
        self.assertEqual(self.store.read('telemetry')['queue'], [])
        device = self.sent[0][1]['events'][0]['device_id']
        metrics.choose(False)
        self.assertFalse(metrics.record('active_open'))
        self.assertTrue(metrics.flush())
        self.assertIn(('/v1/delete', {'device_id': device}), self.sent)
        self.assertFalse(self.store.read('telemetry')['pending_delete'])

    def test_install_completion_offline_retry_dedup_and_report(self):
        db_path = self.root / 'install.sqlite3'
        def offline(path, data):
            raise OSError('offline')
        counter = InstallationCounter(self.store, sender=offline)
        self.assertTrue(counter.record_completion())
        event = self.store.read('install-counts')['queue'][0]
        self.assertEqual(set(event), {'event_id', 'day', 'version'})
        self.assertIsNone(self.store.read('telemetry'))
        self.assertFalse(counter.flush())
        self.assertEqual(self.store.read('install-counts')['queue'], [event])
        def receive(path, data):
            self.assertEqual(path, '/v1/install')
            insert_installation(db_path, data)
        counter.sender = receive
        self.assertTrue(counter.flush())
        self.assertEqual(self.store.read('install-counts')['queue'], [])
        insert_installation(db_path, event)
        output = self.root / 'install-report'
        export(db_path, output, event['day'], event['day'])
        with (output / 'daily.csv').open(encoding='utf-8-sig', newline='') as stream:
            report = list(csv.reader(stream))
        self.assertEqual(report[1], [event['day'], '0', '0', '0', '0', '0', '1'])
        self.assertEqual(report[2], ['合计去重', '0', '0', '0', '0', '0', '1'])
        with self.assertRaises(ValueError):
            insert_installation(db_path, dict(event, room='private'))

    def test_installer_command_records_and_flushes(self):
        from cardsclaim.desktop import app
        with (patch('sys.argv', ['gdufe-campus-balance.exe', '--install-count']),
              patch.object(app, 'DesktopStore', return_value=self.store),
              patch.object(app, 'InstallationCounter') as counter):
            counter.return_value.record_completion.return_value = True
            app.main()
            counter.return_value.record_completion.assert_called_once_with()
            counter.return_value.flush.assert_called_once_with()

    def test_network_failure_preserves_queue_and_field_whitelist(self):
        def fail(path, data):
            raise OSError('offline')
        self.store.write('account', {'config': {'items': ['electricity'],
                          'params': {'electricity': {'room': 'private-room'}}},
                          'token': 'private-token'})
        metrics = Telemetry(self.store, sender=fail, auto_flush=False)
        metrics.choose(True)
        self.assertFalse(metrics.flush())
        queue = self.store.read('telemetry')['queue']
        self.assertEqual(len(queue), 2)
        self.assertEqual(set(queue[0]), {'event_id', 'device_id', 'day', 'version', 'kind', 'feature'})
        self.assertFalse(metrics.record('unknown', 'room-123'))
        self.assertEqual(len(self.store.read('telemetry')['queue']), 2)
        self.assertNotIn('private-room', json.dumps(queue))
        self.assertNotIn('private-token', json.dumps(queue))

    def test_receiver_deduplicates_deletes_and_exports(self):
        db_path = self.root / 'metrics.sqlite3'
        today = datetime.now(TZ).date().isoformat()
        device = 'a' * 32
        def event(event_id, kind, feature=''):
            return dict(event_id=event_id * 32, device_id=device, day=today,
                        version='0.5.1', kind=kind, feature=feature)
        rows = [event('1', 'participation_started'), event('2', 'active_open'),
                event('3', 'query_success_automatic'), event('4', 'alert_delivered_popup', 'electricity'),
                event('5', 'feature_selected', 'electricity')]
        insert_events(db_path, rows)
        insert_events(db_path, rows)
        with closing(connect(db_path)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM events').fetchone()[0], 5)
        output = self.root / 'report'
        export(db_path, output, today, today)
        with (output / 'daily.csv').open(encoding='utf-8-sig', newline='') as stream:
            report = list(csv.reader(stream))
        self.assertEqual(report[1], [today, '1', '1', '1', '1', '1', '0'])
        self.assertEqual(report[2], ['合计去重', '1', '1', '1', '1', '1', '0'])
        with (output / 'features.csv').open(encoding='utf-8-sig', newline='') as stream:
            self.assertEqual(list(csv.reader(stream))[1], ['electricity', '1'])
        delete_device(db_path, device)
        insert_events(db_path, rows)
        with closing(connect(db_path)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM events').fetchone()[0], 0)

    def test_receiver_rejects_extra_or_sensitive_fields(self):
        event = dict(event_id='1' * 32, device_id='2' * 32,
                     day=datetime.now(TZ).date().isoformat(), version='0.5.1',
                     kind='active_open', feature='')
        self.assertTrue(valid_event(event))
        self.assertFalse(valid_event(dict(event, room='101')))
        self.assertFalse(valid_event(dict(event, feature='手机号尾号')))
        with self.assertRaises(ValueError):
            insert_events(self.root / 'metrics.sqlite3', [dict(event, token='secret')])

    def test_http_receiver_and_deletion(self):
        db_path = self.root / 'http.sqlite3'
        Handler.db_path = db_path
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f'http://127.0.0.1:{server.server_port}'
        event = dict(event_id='1' * 32, device_id='2' * 32,
                     day=datetime.now(TZ).date().isoformat(), version='0.5.1',
                     kind='active_open', feature='')
        def post(path, payload):
            request = urllib.request.Request(base + path, json.dumps(payload).encode(),
                                             {'Content-Type': 'application/json'}, method='POST')
            return urllib.request.urlopen(request, timeout=3).status
        self.assertEqual(post('/v1/events', {'events': [event]}), 204)
        install = dict(event_id='3' * 32, day=event['day'], version='0.5.3')
        self.assertEqual(post('/v1/install', install), 204)
        self.assertEqual(post('/v1/install', install), 204)
        with self.assertRaises(urllib.error.HTTPError) as bad_install:
            post('/v1/install', dict(install, device_id='private'))
        self.assertEqual(bad_install.exception.code, 400)
        with self.assertRaises(urllib.error.HTTPError) as error:
            post('/v1/events', {'events': [dict(event, room='secret')]} )
        self.assertEqual(error.exception.code, 400)
        self.assertEqual(post('/v1/delete', {'device_id': event['device_id']}), 204)
        with closing(connect(db_path)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM events').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM installations').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
