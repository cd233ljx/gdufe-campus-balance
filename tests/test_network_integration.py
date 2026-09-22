import contextlib
from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from cardsclaim.desktop import network_core as core
from cardsclaim.desktop.network import NetworkConfig, NetworkLease, NetworkMonitor, validate
from cardsclaim.desktop.model import TZ

CFG = dict(student_id='test', password=' synthetic ', auto_login=True, check_interval=5)
ONLINE = core.Result('online', 'online')
PORTAL = core.Result('portal', 'portal')


class NetworkTests(unittest.TestCase):
    def test_response_spacing_and_invalid_jsonp(self):
        self.assertEqual(core.parse_login('dr1003({"result": 1});')['result'], 1)
        for text in ('[]', 'dr1003({bad});', 'evil({"result":1})'):
            with self.assertRaises(ValueError): core.parse_login(text)

    def test_portal_page_port_and_server_ip(self):
        self.assertEqual(core.PORTAL, 'http://100.64.13.17/')
        with patch.object(core, 'read_response', side_effect=[(302, ''), (302, ''), (200, 'eportal')]) as read:
            self.assertEqual(core.inspect_network(Mock()).code, 'portal')
            self.assertEqual(read.call_args.args[1], core.PORTAL)
        with patch.object(core, 'read_response', return_value=(200, 'eportal var v4ip="192.0.2.17";')):
            self.assertEqual(core.campus_ip(Mock()), '192.0.2.17')

    def test_campus_ip_fallback_no_fabricated_address(self):
        with patch.object(core, 'read_response', return_value=(200, 'eportal')), patch.object(core, 'local_ip', side_effect=OSError()):
            self.assertEqual(core.authenticate(Mock(), CFG, threading.Event()).code, 'route')

    def auth(self, data, online=False):
        stop = Mock(is_set=Mock(return_value=False), wait=Mock(return_value=False))
        with patch.object(core, 'read_response', return_value=(200, json.dumps(data))):
            return core.authenticate(Mock(), CFG, stop, ip_provider=lambda: '192.0.2.17', probe=lambda _: ONLINE if online else PORTAL)

    def test_authentication_separate_from_internet(self):
        self.assertEqual(self.auth({'result': 1}).code, 'authenticated')
        self.assertTrue(self.auth({'result': 1}, online=True).online)
        self.assertEqual(self.auth({'result': 10}).code, 'temporary')
        self.assertEqual(self.auth({'result': True}).code, 'temporary')

    def test_error_classification(self):
        for msg in ('密码错误', '用户不存在', '请完成验证码'):
            self.assertEqual(self.auth({'result': 0, 'msg': msg}).code, 'rejected')
        self.assertEqual(self.auth({'result': 0, 'msg': '服务器忙'}).code, 'temporary')
        self.assertEqual(self.auth({'result': 0, 'msg': '已在线'}).code, 'authenticated')

    def engine(self, state=PORTAL, result=None):
        self.now = 0
        login = Mock(return_value=result or core.Result('temporary', 'temporary'))
        return core.Reconnector(probe=Mock(return_value=state), login=login,
            session_factory=lambda: contextlib.nullcontext(None), clock=lambda: self.now), login

    def test_two_checks_backoff_and_recovery(self):
        engine, login = self.engine()
        self.assertEqual(engine.tick(CFG).code, 'confirming')
        self.assertEqual(engine.tick(CFG).code, 'temporary')
        self.assertEqual(engine.tick(CFG).code, 'waiting')
        self.now = 30
        engine.tick(CFG)
        self.assertEqual(login.call_count, 2)
        self.assertEqual(engine.next_attempt, 90)
        engine.probe.return_value = ONLINE
        engine.tick(CFG)
        self.assertEqual(engine.failures, 0)

    def test_auth_success_prevents_rapid_resubmission(self):
        engine, login = self.engine(result=core.Result('authenticated', 'accepted'))
        engine.tick(CFG, manual=True)
        self.now = 299
        self.assertEqual(engine.tick(CFG).code, 'waiting')
        self.assertEqual(login.call_count, 1)

    def test_repeated_failures_pause_but_manual_retry_allowed(self):
        engine, login = self.engine()
        engine.tick(CFG)
        for i in range(5):
            self.now += 400
            engine.tick(CFG)
        self.assertTrue(engine.paused)
        engine.tick(CFG)
        self.assertEqual(login.call_count, 5)
        engine.tick(CFG, manual=True)
        self.assertEqual(login.call_count, 6)

    def test_online_unknown_disabled_and_cancelled_do_not_login(self):
        for state in (ONLINE, core.Result('unknown', 'unknown')):
            engine, login = self.engine(state)
            engine.tick(CFG, manual=True)
            login.assert_not_called()
        engine, login = self.engine()
        engine.tick(dict(CFG, auto_login=False))
        engine.stop.set()
        engine.tick(CFG, manual=True)
        login.assert_not_called()

    def test_concurrent_login_serialized(self):
        engine, login = self.engine()
        entered, release = threading.Event(), threading.Event()
        def action(*args):
            entered.set()
            release.wait(2)
            return ONLINE
        login.side_effect = action
        thread = threading.Thread(target=lambda: engine.tick(CFG, manual=True))
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertEqual(engine.tick(CFG, manual=True).code, 'busy')
            self.assertEqual(login.call_count, 1)
        finally:
            release.set()
            thread.join(3)

    def test_http_transport_no_proxy_redirect_or_unbounded_body(self):
        class Response:
            code = 302
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, limit):
                self.limit = limit
                return b''
        response = Response()
        opener = Mock(open=Mock(return_value=response))
        self.assertEqual(core.read_response(opener, core.PORTAL), (302, ''))
        self.assertEqual(response.limit, 65537)
        self.assertIsNone(core.NoRedirect().redirect_request(None,None,302,'',{},'http://invalid'))
        with patch.object(core.urllib.request, 'ProxyHandler') as proxy, patch.object(core.urllib.request, 'build_opener'):
            proxy.return_value = core.NoRedirect()
            with core.direct_session(): pass
            proxy.assert_called_once_with({})

    def test_config_plaintext_and_atomic_preservation(self):
        with tempfile.TemporaryDirectory() as folder:
            config = NetworkConfig(folder)
            self.assertFalse(config.load()['auto_login'])
            config.save(CFG)
            self.assertEqual(config.load(), CFG)
            self.assertIn(' synthetic ', config.path.read_text())
            with patch('cardsclaim.desktop.network.os.replace', side_effect=OSError()):
                with self.assertRaises(OSError): config.save(dict(CFG, password='other'))
            self.assertEqual(config.load(), CFG)
            self.assertFalse(list(Path(folder).glob('*.tmp')))
        with self.assertRaises(ValueError): validate(dict(CFG, check_interval=True))
        with self.assertRaises(ValueError): validate(dict(CFG, password=''))

    def test_lease_conflict_prevents_parallel_clients(self):
        one, two = NetworkLease(), NetworkLease()
        try:
            # The user's standalone tool may already own the handle; either case
            # must prevent the second client from acquiring it.
            acquired = one.acquire()
            self.assertFalse(two.acquire())
            if acquired:
                one.close()
                self.assertTrue(two.acquire())
        finally:
            one.close(); two.close()

    def test_worker_disabled_and_conflict_never_probes(self):
        for enabled in (False, True):
            with tempfile.TemporaryDirectory() as folder:
                NetworkConfig(folder).save(dict(CFG, auto_login=enabled))
                engine = Mock()
                lease = Mock(acquire=Mock(return_value=False))
                arrived = threading.Event()
                events = []
                def callback(event):
                    events.append(event); arrived.set()
                monitor = NetworkMonitor(folder, callback, engine_factory=lambda: engine, lease_factory=lambda: lease)
                monitor.start()
                try:
                    self.assertTrue(arrived.wait(2))
                    self.assertEqual(events[0][1], 'conflict' if enabled else 'disabled')
                    engine.tick.assert_not_called()
                finally:
                    monitor.close(); monitor.thread.join(3)

    def test_recovery_once_and_stale_events_dropped(self):
        with tempfile.TemporaryDirectory() as folder:
            events = []
            monitor = NetworkMonitor(folder, events.append)
            monitor.emit(PORTAL, 0)
            monitor.emit(ONLINE, 0)
            monitor.emit(ONLINE, 0)
            self.assertEqual([e[3] for e in events], [False, True, False])
            monitor.configure(dict(CFG, auto_login=False))
            monitor.emit(PORTAL, 0)
            self.assertEqual(len(events), 3)
