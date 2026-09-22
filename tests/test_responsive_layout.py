"""Real Tk geometry checks with synthetic state; no school/network requests."""
import ctypes
import os
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from cardsclaim.desktop.gui import DesktopWindow
from cardsclaim.desktop.controller import default_config
from cardsclaim.desktop.store import DesktopStore


class ResponsiveLayoutTests(unittest.TestCase):
    def test_home_pages_fit_across_widths_and_scaling(self):
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        for scale in (1.333333, 1.666667, 2.0, 2.333333, 2.666667):
            with tempfile.TemporaryDirectory() as directory:
                root = tk.Tk()
                root.tk.call('tk', 'scaling', scale)
                store = DesktopStore(directory)
                with patch('cardsclaim.desktop.layout.work_area', return_value=(0, 0, 3840, 2160)):
                    app = DesktopWindow(root, store, autostart=False, tray=False)
                try:
                    for page in ('setup', 'dashboard'):
                        if page == 'dashboard':
                            cfg = default_config()
                            cfg['items'] = ['electricity', 'tap_water', 'liwang']
                            store.write('account', {'token': 'synthetic', 'config': cfg})
                            app.dashboard()
                        sizes = [(1000,900), (800,600), (640,480), (480,640)]
                        for sw, sh in ((1366,768), (1920,1080), (1920,1200), (2560,1440), (2560,1600)):
                            ratio = scale / (96 / 72)
                            sizes.append((min(round(920 * ratio), sw - 48), min(round(760 * ratio), sh - 128)))
                        for width, height in sizes:
                            with self.subTest(scale=scale, page=page, size=(width,height)):
                                root.geometry(f'{width}x{height}+20+20')
                                root.update()
                                root.update_idletasks()
                                if os.environ.get('CARDSCLAIM_LAYOUT_SCREENSHOTS'):
                                    from PIL import ImageGrab
                                    folder = Path('build/layout-matrix')
                                    folder.mkdir(parents=True, exist_ok=True)
                                    root.attributes('-topmost', True)
                                    root.update()
                                    x, y = root.winfo_rootx(), root.winfo_rooty()
                                    ImageGrab.grab(bbox=(x, y, x + width, y + height), all_screens=True).save(folder / f'{page}-{scale}-{width}.png')
                                pane = app.body_scroll
                                self.assertEqual(pane.canvas.xview(), (0.0, 1.0))
                                self.assertFalse(pane.horizontal.winfo_ismapped())
                                self.assertGreater(pane.canvas.winfo_height(), 60)
                                left = pane.canvas.winfo_rootx()
                                right = left + pane.canvas.winfo_width()
                                for widget in descendants(app.body):
                                    if not widget.winfo_ismapped():
                                        continue
                                    self.assertGreaterEqual(widget.winfo_rootx(), left - 1, str(widget))
                                    self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(), right + 1, str(widget))
                                    if isinstance(widget, tk.Label):
                                        self.assertGreaterEqual(widget.winfo_width(), widget.winfo_reqwidth(), widget.cget('text'))
                                    if isinstance(widget, tk.Button):
                                        self.assertGreaterEqual(widget.winfo_width(), widget.winfo_reqwidth(), widget.cget('text'))
                                footer = [w for w in descendants(app.outer) if isinstance(w, tk.Button)
                                          and w.cget('text') in ('关闭程序', '使用指南')]
                                self.assertEqual(len(footer), 2)
                                for widget in footer:
                                    self.assertTrue(widget.winfo_ismapped())
                                    self.assertGreaterEqual(widget.winfo_width(), widget.winfo_reqwidth())
                                    self.assertLessEqual(widget.winfo_rooty()+widget.winfo_height(), root.winfo_rooty()+height)
                finally:
                    app.closing = True
                    app.network.close()
                    for timer in root.tk.call('after', 'info'):
                        root.after_cancel(timer)
                    root.destroy()

