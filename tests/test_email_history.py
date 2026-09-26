import asyncio
import copy
import smtplib
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

from cardsclaim.api import QueryError
from cardsclaim.desktop import email_alerts
from cardsclaim.desktop.app import check
from cardsclaim.desktop.controller import Controller
from cardsclaim.desktop.history import record, recent, query_range
from cardsclaim.desktop.model import accept, TZ
from cardsclaim.desktop.store import DesktopStore
from test_desktop import config, balances


class EmailHistoryTests(unittest.TestCase):
    def test_date_range_inclusive_and_pagination_without_truncation(self):
        for day, count in [('2024-02-28', 1), ('2024-02-29', 650), ('2024-03-01', 650), ('2024-03-02', 1)]:
            self.store.write('history-' + day, [{'id': f'{day}:{i}'} for i in range(count)])
        result = []
        for page in range(13):
            rows, total = query_range(self.store, '2024-02-29', '2024-03-01', page=page)
            self.assertEqual(total, 1300)
            self.assertEqual(len(rows), 100)
            result.extend(row['id'] for row in rows)
        self.assertEqual(len(set(result)), 1300)
        self.assertEqual(result[0], '2024-03-01:649')
        self.assertEqual(result[-1], '2024-02-29:0')
        self.assertEqual(query_range(self.store, '2024-03-02', '2024-03-02')[1], 1)
        self.assertEqual(query_range(self.store, '2024-04-01', '2024-04-02'), ([], 0))
        with self.assertRaises(ValueError):
            query_range(self.store, '2024-03-01', '2024-02-29')
        with self.assertRaises(ValueError):
            query_range(self.store, '2023-02-29', '2024-03-01')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = DesktopStore(self.temp.name, lambda data, decrypt=False: data)
        self.cfg = config()
        self.store.write('account', {'config': self.cfg, 'token': 'synthetic-token'})
        self.mail = email_alerts.credentials('test@qq.com', 'syntheticcode')
        self.store.write('email', self.mail)

    def queue(self, values):
        state = self.store.read('state', {})
        accept(self.cfg, state, values)
        self.store.write('state', state)
        email_alerts.queue_alerts(self.store, self.cfg, state)

    def test_smtp_tls_self_delivery_and_redacted_errors(self):
        with patch('cardsclaim.desktop.email_alerts.smtplib.SMTP_SSL') as factory:
            smtp = factory.return_value.__enter__.return_value
            email_alerts.send(self.mail, '测试', '测试正文')
            self.assertEqual(factory.call_args.args, ('smtp.qq.com', 465))
            self.assertTrue(factory.call_args.kwargs['context'].check_hostname)
            smtp.login.assert_called_once_with('test@qq.com', 'syntheticcode')
            message = smtp.send_message.call_args.args[0]
            self.assertEqual(message['To'], message['From'])
            smtp.login.side_effect = smtplib.SMTPAuthenticationError(535, b'sensitive server text')
            with self.assertRaises(ValueError) as error:
                email_alerts.send(self.mail, '测试', '正文')
            self.assertNotIn('sensitive', str(error.exception))

    def test_invalid_credentials(self):
        for address, code in [('bad@other.com', 'code'), ('x@qq.com\r\nBcc:x@y.com', 'code'), ('x@qq.com', '')]:
            with self.assertRaises(ValueError):
                email_alerts.credentials(address, code)

    def test_retesting_same_mailbox_preserves_delivered_episode(self):
        self.queue(balances())
        with patch('cardsclaim.desktop.email_alerts.send'):
            asyncio.run(email_alerts.deliver(self.store))
        before = self.store.read('email-state')
        controller = Controller(self.store)
        with patch('cardsclaim.desktop.email_alerts.send'), \
             patch('cardsclaim.desktop.controller.stop'), patch.object(controller, 'resume'):
            controller.bind_email('test@qq.com', 'replacementcode')
        self.assertEqual(self.store.read('email-state'), before)

    def test_history_across_days_keeps_comparison_and_filters(self):
        with patch('cardsclaim.desktop.history.datetime') as clock:
            clock.now.return_value = datetime(2026, 9, 20, 23, 45, tzinfo=TZ)
            record(self.store, self.cfg, balances('20', '4'))
            clock.now.return_value = datetime(2026, 9, 21, 0, 15, tzinfo=TZ)
            record(self.store, self.cfg, balances('19', '4'))
        self.assertEqual(len(recent(self.store)), 2)
        self.assertEqual(len(recent(self.store, '2026-09-20')), 1)
        row = recent(self.store, '2026-09-21')[0]['rows'][0]
        self.assertEqual(row['change'], '-1')
        self.assertTrue(row['previous_time'].startswith('2026-09-20'))

    def test_history_retains_latest_365_days_without_stale_comparison(self):
        old = '2025-09-26'
        edge = '2025-09-27'
        self.store.write('history-' + old, [{'id': 'old'}])
        self.store.write('history-' + edge, [{'id': 'edge'}])
        self.store.write('history-invalid-day', [{'id': 'unrelated'}])
        with patch('cardsclaim.desktop.history.datetime') as clock:
            clock.now.return_value = datetime(2026, 9, 26, 12, 0, tzinfo=TZ)
            record(self.store, self.cfg, balances('20', '4'))
        self.assertFalse((self.store.root / ('history-' + old)).exists())
        self.assertTrue((self.store.root / ('history-' + edge)).exists())
        self.assertTrue((self.store.root / 'history-invalid-day').exists())

        with patch('cardsclaim.desktop.history.datetime') as clock:
            clock.now.return_value = datetime(2027, 9, 27, 12, 0, tzinfo=TZ)
            record(self.store, self.cfg, balances('19', '4'))
        self.assertNotIn('change', recent(self.store)[0]['rows'][0])

    def test_failed_bind_keeps_previous_then_success_and_disable(self):
        controller = Controller(self.store)
        with patch('cardsclaim.desktop.email_alerts.send', side_effect=ValueError('测试失败')):
            with self.assertRaises(ValueError):
                controller.bind_email('new@qq.com', 'newcode')
        self.assertEqual(self.store.read('email'), self.mail)
        with patch('cardsclaim.desktop.email_alerts.send') as send, \
             patch('cardsclaim.desktop.controller.stop'), patch.object(controller, 'resume'):
            controller.bind_email('new@qq.com', 'newcode')
            send.assert_called_once()
            self.assertEqual(self.store.read('email')['address'], 'new@qq.com')
            controller.disable_email()
            self.assertEqual(self.store.read('email'), {})
            self.assertEqual(self.store.read('email-state'), {})

    def test_dedup_survives_restart_recovery_and_independent_desktop_notice(self):
        self.queue(balances())
        with patch('cardsclaim.desktop.email_alerts.send') as send:
            asyncio.run(email_alerts.deliver(self.store))
            self.assertEqual(send.call_count, 1)
            self.assertTrue(self.store.read('state')['pending'])
            self.store = DesktopStore(self.temp.name, lambda data, decrypt=False: data)
            self.queue(balances('18', '0.8'))
            asyncio.run(email_alerts.deliver(self.store))
            self.assertEqual(send.call_count, 1)
            self.queue(balances('20', '1'))
            self.queue(balances())
            asyncio.run(email_alerts.deliver(self.store))
            self.assertEqual(send.call_count, 2)

    def test_failed_delivery_retries_and_recovery_cancels(self):
        self.queue(balances())
        with patch('cardsclaim.desktop.email_alerts.send', side_effect=ValueError('失败')):
            asyncio.run(email_alerts.deliver(self.store))
        self.assertTrue(self.store.read('email-state')['pending'])
        self.queue(balances())
        with patch('cardsclaim.desktop.email_alerts.send') as send:
            asyncio.run(email_alerts.deliver(self.store))
            send.assert_called_once()
        self.assertNotIn('error', self.store.read('email-state'))
        self.queue(balances('30', '5'))
        self.queue(balances())
        self.queue(balances('30', '5'))
        with patch('cardsclaim.desktop.email_alerts.send') as send:
            asyncio.run(email_alerts.deliver(self.store))
            send.assert_not_called()

    def test_history_difference_failure_room_switch_and_date_filter(self):
        record(self.store, self.cfg, balances('20', '4'))
        record(self.store, self.cfg, {}, 'network', 'automatic')
        record(self.store, self.cfg, balances('18.25', '5'))
        rows = recent(self.store)
        self.assertEqual(rows[0]['rows'][0]['change'], '-1.75')
        self.assertEqual(rows[0]['rows'][1]['change'], '1')
        self.assertNotIn('amount', rows[1]['rows'][0])
        other = copy.deepcopy(self.cfg)
        other['params']['electricity']['room'] = 'other'
        record(self.store, other, balances('100', '5'))
        self.assertNotIn('change', recent(self.store)[0]['rows'][0])
        self.assertEqual(len(recent(self.store, datetime.now(TZ).date().isoformat())), 4)
        self.assertEqual(recent(self.store, '2000-01-01'), [])
        with self.assertRaises(ValueError):
            recent(self.store, '../../account')
        self.assertEqual(len(recent(self.store, limit=2)), 2)

    def test_partial_query_failure_records_real_results_only(self):
        async def query(campus, item, token):
            if item == 'tap_water':
                raise QueryError('network')
            return {'amount': '12', 'unit': '度'}
        state = {}
        with patch('cardsclaim.desktop.app.Campus.query', query):
            self.assertFalse(asyncio.run(check(self.store, self.cfg, 'synthetic', state)))
        rows = recent(self.store)[0]['rows']
        self.assertEqual(rows[0]['amount'], '12')
        self.assertEqual(rows[1]['status'], 'network')
        self.assertNotIn('amount', rows[1])
        self.assertIn('last_attempt', state)

    def test_settings_changes_do_not_fabricate_history(self):
        controller = Controller(self.store)
        self.queue(balances())
        with patch('cardsclaim.desktop.controller.stop'), patch.object(controller, 'resume'):
            controller.save_settings(self.cfg)
        self.assertEqual(recent(self.store), [])

    def test_failed_room_verification_is_logged_without_replacing_account(self):
        controller = Controller(self.store)
        original = self.store.read('account')
        other = copy.deepcopy(self.cfg)
        other['params']['electricity']['room'] = 'unconfirmed'
        with patch('cardsclaim.desktop.controller.stop'), patch.object(controller, 'resume'), \
             patch('cardsclaim.desktop.controller.query_all', side_effect=QueryError('business')):
            with self.assertRaises(QueryError):
                controller.finish_rooms(other)
        self.assertEqual(self.store.read('account'), original)
        self.assertEqual(recent(self.store)[0]['source'], 'login')
        self.assertEqual(recent(self.store)[0]['rows'][0]['status'], 'business')
