# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CardsClaim contributors
import asyncio
import copy
import tempfile
import unittest
from unittest.mock import patch, AsyncMock

from cardsclaim.desktop.controller import Controller, default_config
from cardsclaim.desktop.store import DesktopStore
from cardsclaim.desktop.model import accept


def configured():
    cfg = default_config()
    cfg['params'] = {k: dict(campus='test', building='test', room='test', type='IEC', level='3', feeitemid=v)
                     for k, v in [('electricity', '1'), ('tap_water', '5')]}
    return cfg


class GuiControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = DesktopStore(self.temp.name, lambda data, decrypt=False: data)
        self.controller = Controller(self.store)
        self.cfg = configured()
        self.store.write('account', {'config': self.cfg, 'token': 'old-test-token'})

    def test_cancelled_login_keeps_old_account_and_resumes(self):
        old = self.store.read('account')
        with patch('cardsclaim.desktop.controller.stop'), patch.object(self.controller, 'resume') as resume, \
             patch('cardsclaim.desktop.login.local_login', new=AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                self.controller.prepare_login(self.cfg, True, lambda text: None)
        self.assertEqual(self.store.read('account'), old)
        self.assertIsNone(self.controller.login_task)
        resume.assert_called_once()

    def test_renewal_does_not_duplicate_existing_low_alert(self):
        balances = {'electricity': {'amount': '10', 'unit': '度'}, 'tap_water': {'amount': '4', 'unit': '元'}}
        state = {}
        accept(self.cfg, state, balances)
        state['pending'].clear()
        self.store.write('state', state)
        with patch('cardsclaim.desktop.controller.stop'), patch.object(self.controller, 'resume'):
            self.controller.commit_login((self.cfg, 'new-test-token', balances))
        self.assertFalse(self.store.read('state')['pending'])
        self.assertEqual(self.store.read('account')['token'], 'new-test-token')

    def test_changed_room_resets_low_episode(self):
        balances = {'electricity': {'amount': '10', 'unit': '度'}, 'tap_water': {'amount': '4', 'unit': '元'}}
        state = {}
        accept(self.cfg, state, balances)
        state['pending'].clear()
        self.store.write('state', state)
        updated = copy.deepcopy(self.cfg)
        updated['params']['electricity']['room'] = 'different-room'
        with patch('cardsclaim.desktop.controller.stop'), patch.object(self.controller, 'resume'):
            self.controller.commit_login((updated, 'new-test-token', balances))
        self.assertIn('electricity', self.store.read('state')['pending'])

    def test_threshold_update_preserves_balance_timestamp(self):
        state = {'status': 'ok', 'last_success': '2026-09-20T08:00:00+08:00',
                 'balances': {'electricity': {'amount': '30', 'unit': '度'}, 'tap_water': {'amount': '4', 'unit': '元'}}}
        self.store.write('state', state)
        self.cfg['thresholds']['electricity'] = '40'
        with patch('cardsclaim.desktop.controller.stop'), patch.object(self.controller, 'resume'):
            self.controller.save_settings(self.cfg)
        result = self.store.read('state')
        self.assertEqual(result['last_success'], state['last_success'])
        self.assertIn('electricity', result['pending'])

    def test_window_lock_does_not_block_monitor_lock(self):
        with self.store.lock('window.lock'):
            self.assertFalse(self.store.running())
            with self.store.lock():
                self.assertTrue(self.store.running())

    def test_incomplete_login_is_saved_separately_and_does_not_enable_monitor(self):
        self.store.write('account', {'config': default_config()})
        async def capture(*args, **kwargs):
            kwargs['checkpoint']({'token': 'pending-test-token', 'config': default_config(), 'cookies': []})
            return default_config(), 'pending-test-token', None
        with patch('cardsclaim.desktop.controller.stop'), patch('cardsclaim.desktop.controller.background') as start, \
             patch('cardsclaim.desktop.login.local_login', side_effect=capture):
            result = self.controller.prepare_login(default_config(), True, lambda _: None)
        self.assertIsNone(result[2])
        self.assertEqual(self.store.read('login-draft')['token'], 'pending-test-token')
        self.assertNotIn('token', self.store.read('account'))
        self.assertFalse(self.controller.ready())
        start.assert_not_called()
