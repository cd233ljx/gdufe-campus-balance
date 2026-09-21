# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CardsClaim contributors
"""Real native GUI smoke test with isolated, synthetic account data."""
import asyncio
import ctypes
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import traceback
import threading
import tkinter as tk
from unittest.mock import patch

from PIL import ImageGrab

from .app import child_command, stop
from .controller import default_config
from .gui import DesktopWindow
from .model import TZ
from .store import DesktopStore


def run(report):
    report = Path(report).resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    results = []
    original = os.environ.get('CARDSCLAIM_DATA_DIR')
    failure = None
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    with tempfile.TemporaryDirectory(prefix='CardsClaim GUI 中文 ', ignore_cleanup_errors=True) as directory:
        os.environ['CARDSCLAIM_DATA_DIR'] = directory
        store = DesktopStore()
        root = tk.Tk()
        small_screen = os.environ.get('CARDSCLAIM_GUI_SMALL_SCREEN') == '1'
        layout_patch = None
        if small_screen:
            root.tk.call('tk', 'scaling', 2.0)
            layout_patch = patch('cardsclaim.desktop.layout.work_area', return_value=(0, 0, 1024, 640))
            layout_patch.start()
        class FakeStartup:
            value = False
            def enabled(self): return self.value
            def set_enabled(self, value): self.value = value
        startup = FakeStartup()
        app = DesktopWindow(root, store, autostart=False, startup=startup)
        lock = store.lock('window.lock')
        lock.__enter__()

        def screenshot(name, target=None):
            target = target or root
            target.attributes('-topmost', True)
            target.update()
            time.sleep(.3)
            box = (target.winfo_rootx(), target.winfo_rooty(), target.winfo_rootx() + target.winfo_width(), target.winfo_rooty() + target.winfo_height())
            ImageGrab.grab(bbox=box).save(report.with_name(name + '.png'))
            target.attributes('-topmost', False)

        def step_one():
            def guide_buttons(widget):
                for child in widget.winfo_children():
                    if isinstance(child, tk.Button):
                        yield child
                    yield from guide_buttons(child)
            app.show_guide()
            with patch('cardsclaim.desktop.gui.messagebox.askyesno') as prompt:
                app.ask_startup()
                prompt.assert_not_called()
            for index in range(3):
                screenshot(f'gui-guide-{index + 1}', app.guide_window)
                title = '开始使用' if index == 2 else '下一步'
                next(button for button in guide_buttons(app.guide_window) if button.cget('text') == title).invoke()
            assert store.read('preferences')['guide_seen']
            assert app.guide_window is None
            app.show_guide()
            next(button for button in guide_buttons(app.guide_window) if button.cget('text') == '跳过引导').invoke()
            results.append('three-step guide, completion, skip, reopening and deferred startup prompt')
            with patch('cardsclaim.desktop.gui.messagebox.askyesno', return_value=False) as prompt:
                app.ask_startup()
                self_answer = store.read('preferences', {})
                assert self_answer.get('startup_prompted') and not startup.enabled()
                app.ask_startup()
                assert prompt.call_count == 1
            store.write('preferences', {})
            with patch('cardsclaim.desktop.gui.messagebox.askyesno', return_value=True):
                app.ask_startup()
                assert startup.enabled()
            results.append('first-launch startup opt-in/out persists and is asked only once; registry mocked')
            assert app.page == 'setup' 
            assert app.tray_ready, 'tray did not start'
            assert not store.read('account')
            screenshot('gui-setup')
            results.append('first-run setup and real Windows tray ready')
            if small_screen:
                assert root.winfo_height() <= 576
                footer_buttons = [w for w in guide_buttons(root) if w.cget('text') in ('关闭程序', '使用指南')]
                assert len(footer_buttons) == 2
                for button in footer_buttons:
                    assert button.winfo_rooty() + button.winfo_height() <= root.winfo_rooty() + root.winfo_height()
                assert app.body_scroll.canvas.yview()[1] < 1
                app.body_scroll.canvas.yview_moveto(1)
                root.update()
                screenshot('gui-small-screen-scrolled')
                app.body_scroll.canvas.yview_moveto(0)
                results.append('1024x640 work area at 150% text scaling: footer visible and body scrollable')
            app.email_settings()
            root.update()
            email_window = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][0]
            screenshot('gui-email-setup', email_window)
            if small_screen:
                assert email_window.winfo_height() <= 576
                from .layout import ScrollPane
                pane = next(w for w in email_window.winfo_children() if isinstance(w, ScrollPane))
                pane.canvas.yview_moveto(1)
                root.update()
                last = next(w for w in guide_buttons(email_window) if w.cget('text') == '暂不设置 / 返回')
                assert last.winfo_rooty() + last.winfo_height() <= email_window.winfo_rooty() + email_window.winfo_height()
                screenshot('gui-small-email-scrolled', email_window)
            email_window.destroy()
            assert not store.read('email')
            results.append('optional first-run QQ email dialog can be skipped without enabling mail')
            cfg = default_config()
            balances = {'electricity': {'amount': '86.40', 'unit': '度'},
                        'tap_water': {'amount': '12.50', 'unit': '元'}}
            catalog = {item: {'source': '学校已绑定',
                'selected': {'campus':'campus-bound','building':'building-bound','room':'room-bound'},
                'options': {'campus':[{'name':'其他校区','value':'campus-other'},{'name':'示例校区','value':'campus-bound'}],
                            'building':[{'name':'示例宿舍楼','value':'building-bound'}],
                            'room':[{'name':'101','value':'room-other'},{'name':'808','value':'room-bound'}]}}
                for item in cfg['items']}
            def completed_login(*args):
                store.write('login-draft', {'config': cfg, 'token': 'offline-test-only', 'cookies': []})
                return cfg, 'offline-test-only', None
            app.controller.prepare_login = completed_login
            app.controller.load_rooms = lambda config: (config, catalog)
            app.controller.load_choices = lambda *args: [{'name':'新楼栋','value':'new-building'}]
            app.controller.finish_rooms = lambda config: app.controller.commit_login((config, 'offline-test-only', balances))
            app.begin_setup()
            deadline = time.monotonic() + 18
            def wait_dashboard():
                if app.page == 'dashboard' and not app.busy:
                    assert app.amounts['electricity'].get() == '86.40'
                    account = store.read('account')
                    assert account['token'] == 'offline-test-only'
                    assert account['config']['params']['electricity']['room'] == 'room-bound'
                    assert app.status.get().startswith('登录成功')
                    results.append('native room confirmation, encrypted save and automatic monitoring/dashboard')
                    root.after(500, guarded(step_two))
                elif time.monotonic() >= deadline:
                    raise AssertionError('room confirmation did not transition to dashboard')
                else:
                    root.after(100, guarded(wait_dashboard))
            def wait_cascade():
                if not app.busy:
                    picker = app.room_picker
                    assert picker.boxes['electricity']['room'].get() == ''
                    assert picker.maps['electricity']['room'] == {}
                    assert picker.boxes['electricity']['building'].get() == ''
                    results.append('changing parent selection clears stale building/room; room search never retains stale code')
                    app.show_rooms((cfg, catalog))
                    app.room_picker.submit()
                    root.after(100, guarded(wait_dashboard))
                else:
                    root.after(100, guarded(wait_cascade))
            def wait_rooms():
                if not app.busy and app.page == 'rooms':
                    assert store.read('login-draft').get('token')
                    assert not store.running()
                    picker = app.room_picker
                    assert picker.boxes['electricity']['campus'].get() == '示例校区'
                    assert picker.boxes['electricity']['room'].get() == '808'
                    screenshot('gui-native-rooms')
                    results.append('login automatically returns to native room picker with school-bound defaults, not first options')
                    picker.boxes['electricity']['room'].set('999')
                    picker.filter_rooms('electricity')
                    assert 'room' not in picker.rows['electricity']
                    picker.boxes['electricity']['campus'].set('其他校区')
                    picker.changed('electricity','campus')
                    root.after(100, guarded(wait_cascade))
                elif time.monotonic() >= deadline:
                    raise AssertionError('native room picker did not open')
                else:
                    root.after(100, guarded(wait_rooms))
            root.after(100, guarded(wait_rooms))

        def step_two():
            screenshot('gui-dashboard')
            app.hide()
            root.update()
            assert root.state() == 'withdrawn'
            assert store.running(), 'closing window stopped monitor'
            child = subprocess.Popen(child_command(True), creationflags=subprocess.CREATE_NO_WINDOW)
            child.wait(timeout=15)
            assert child.returncode == 0
            assert store.read('show-window', {}).get('at')
            results.append('close-to-tray keeps monitoring; second launch signals existing window')
            root.after(2400, guarded(step_three))

        def step_three():
            assert root.state() == 'normal', 'second launch did not restore window'
            app.settings()
            root.update()
            settings = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
            assert len(settings) == 1
            def descendants(widget):
                for child in widget.winfo_children():
                    yield child
                    yield from descendants(child)
            toggles = [w for w in descendants(settings[0]) if isinstance(w, tk.Checkbutton)]
            assert len(toggles) == 1
            toggles[0].invoke()
            assert not startup.enabled()
            toggles[0].invoke()
            assert startup.enabled()
            screenshot('gui-startup-settings', settings[0])
            results.append('settings startup toggle applies immediately without changing reminders')
            settings[0].destroy()
            results.append('dashboard, settings and restore work on Tk main thread')
            app.history()
            root.update()
            history_window = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][0]
            from tkinter import ttk
            trees = [w for w in descendants(history_window) if isinstance(w, ttk.Treeview)]
            assert len(trees[0].get_children()) == 2
            from .date_picker import DatePicker
            date_pickers = [w for w in descendants(history_window) if isinstance(w, DatePicker)]
            assert len(date_pickers) == 2
            filter_buttons = {w.cget('text'): w for w in descendants(history_window) if isinstance(w, ttk.Button)}
            filter_buttons['今天'].invoke()
            assert len(trees[0].get_children()) == 2
            filter_buttons['近 30 天'].invoke()
            assert len(trees[0].get_children()) == 2
            date_pickers[0].open_calendar()
            root.update()
            calendar_window = [w for w in date_pickers[0].winfo_children() if isinstance(w, tk.Toplevel)][0]
            screenshot('gui-date-calendar', calendar_window)
            calendar_buttons = [w for w in descendants(calendar_window) if isinstance(w, ttk.Button)]
            next(w for w in calendar_buttons if w.cget('text') == '选择今天').invoke()
            filter_buttons['查询范围'].invoke()
            assert len(trees[0].get_children()) == 2
            results.append('calendar range selection and today/30-day quick filters work without typing')
            screenshot('gui-query-history', history_window)
            history_window.destroy()
            results.append('query history shows persisted per-item login balances with scrollable columns')
            with patch('cardsclaim.desktop.gui.messagebox.askyesno', return_value=False) as confirm:
                app.quit()
                confirm.assert_called_once()
                assert confirm.call_args.kwargs['default'] == 'no'
                assert '停止自动余额查询' in confirm.call_args.args[1]
            assert store.running() and not app.closing and not app.busy
            results.append('cancel close confirmation preserves background monitoring; defaults to no')
            with patch('cardsclaim.desktop.gui.messagebox.askyesno', return_value=True):
                app.quit()

        def guarded(callback):
            def run_step():
                nonlocal failure
                try:
                    callback()
                except Exception:
                    failure = traceback.format_exc()
                    app.destroy()
            return run_step

        root.after(2000, guarded(step_one))
        root.after(45000, guarded(lambda: (_ for _ in ()).throw(AssertionError('GUI self-test timeout'))))
        try:
            root.mainloop()
            assert not store.running(), 'exit did not stop monitoring'
            results.append('quit stops monitoring and destroys tray/window')
        except Exception:
            failure = traceback.format_exc()
        finally:
            if layout_patch:
                layout_patch.stop()
            stop(store)
            lock.__exit__(None, None, None)
            if original is None:
                os.environ.pop('CARDSCLAIM_DATA_DIR', None)
            else:
                os.environ['CARDSCLAIM_DATA_DIR'] = original
    report.write_text(json.dumps({'ok': failure is None, 'passed': results, 'error': failure}, ensure_ascii=False, indent=2), encoding='utf-8')
    if failure:
        raise SystemExit(1)
