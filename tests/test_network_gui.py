import tempfile
import tkinter as tk
import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock
from cardsclaim.desktop.gui import DesktopWindow
from cardsclaim.desktop.store import DesktopStore
from cardsclaim.desktop.model import TZ
from test_gui_controller import configured


class NetworkGuiTests(unittest.TestCase):
    def test_recovery_refreshes_failed_or_due_balance_once(self):
        with tempfile.TemporaryDirectory() as folder:
            store = DesktopStore(folder, lambda data, decrypt=False: data)
            store.write('account', {'config': configured(), 'token': 'synthetic-token'})
            root = tk.Tk()
            app = DesktopWindow(root, store, autostart=False, tray=False)
            app.refresh = Mock()
            try:
                fresh = datetime.now(TZ).isoformat()
                store.write('state', {'status': 'ok', 'last_attempt': fresh})
                app.events.put(('network', (0, 'online', 'online', True)))
                app.pump()
                app.refresh.assert_not_called()
                store.write('state', {'status': 'network', 'last_attempt': fresh})
                app.events.put(('network', (0, 'online', 'online', True)))
                app.pump()
                app.refresh.assert_called_once()
                app.pump()
                self.assertEqual(app.refresh.call_count, 1)
                app.busy = True
                app.events.put(('network', (0, 'online', 'online', True)))
                app.pump()
                self.assertTrue(app.network_recovery_pending)
                app.busy = False
                store.write('state', {'status': 'ok', 'last_attempt': (datetime.now(TZ)-timedelta(hours=1)).isoformat()})
                app.pump()
                self.assertEqual(app.refresh.call_count, 2)
                app.events.put(('network', (-1, 'online', 'stale', True)))
                app.pump()
                self.assertEqual(app.refresh.call_count, 2)
            finally:
                app.destroy()
