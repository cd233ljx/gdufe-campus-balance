import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from cardsclaim.desktop.gui import DesktopWindow
from cardsclaim.desktop.store import DesktopStore
from cardsclaim.desktop import updater
from test_updater import release


class UpdateGuiTests(unittest.TestCase):
    def test_failed_download_keeps_window_and_monitoring(self):
        with tempfile.TemporaryDirectory() as folder:
            root = tk.Tk()
            app = DesktopWindow(root, DesktopStore(folder), autostart=False, tray=False)
            app.job = lambda operation, done=None, text='': done(operation())
            try:
                with patch.object(updater, 'current_version', return_value='0.5.0'), patch.object(updater, 'check_update', return_value=updater.parse_release(release(), '0.5.0')), patch('cardsclaim.desktop.gui.installation', return_value={'datadir': folder}), patch('sys.frozen', True, create=True):
                    app.check_update()
                window = next(w for w in root.winfo_children() if isinstance(w, tk.Toplevel))
                def children(widget):
                    for child in widget.winfo_children():
                        yield child
                        yield from children(child)
                button = next(w for w in children(window) if isinstance(w, tk.Button) and w.cget('text') == '下载并安装')
                with patch.object(updater, 'prepare', side_effect=ValueError('模拟校验失败')), patch.object(updater, 'launch') as launch, patch('cardsclaim.desktop.gui.stop') as stop:
                    button.invoke()
                launch.assert_not_called()
                stop.assert_not_called()
                self.assertTrue(window.winfo_exists())
                self.assertFalse(app.closing)
                self.assertEqual(button.cget('state'), 'normal')
            finally:
                app.destroy()

    def test_current_version_does_not_offer_install(self):
        with tempfile.TemporaryDirectory() as folder:
            root = tk.Tk()
            app = DesktopWindow(root, DesktopStore(folder), autostart=False, tray=False)
            app.job = lambda operation, done=None, text='': done(operation())
            try:
                with patch.object(updater, 'check_update', return_value=None), patch('cardsclaim.desktop.gui.messagebox.showinfo') as message:
                    app.check_update()
                self.assertIn('最新正式版', message.call_args.args[1])
            finally:
                app.destroy()
