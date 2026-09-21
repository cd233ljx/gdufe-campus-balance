# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CardsClaim contributors
"""Small Windows dashboard: setup once, then automatic monitoring."""
import asyncio
import copy
import ctypes
import os
from pathlib import Path
import queue
import threading
import sys
import time
import traceback
import tkinter as tk
from tkinter import messagebox, ttk

from PIL import Image, ImageDraw, ImageTk
import pystray

from ..common import ITEMS, LABELS
from .app import stop
from .controller import Controller, default_config
from .model import validate
from .store import DesktopStore
from ..api import QueryError

BG = '#F3F5F7'
INK = '#142A38'
MUTED = '#607380'
ACCENT = '#007B65'
NAMES = {'electricity': '宿舍电量', 'tap_water': '自来水', 'liwang': '力王热水'}


def configure_tk():
    # Some Windows Python installations do not discover their sibling Tcl data.
    # Frozen builds get private library paths from PyInstaller's runtime hook.
    if not getattr(sys, 'frozen', False):
        for variable, folder, marker in [('TCL_LIBRARY', 'tcl8.6', 'init.tcl'), ('TK_LIBRARY', 'tk8.6', 'tk.tcl')]:
            path = Path(sys.base_prefix) / 'tcl' / folder
            if (path / marker).is_file():
                os.environ.setdefault(variable, str(path))


configure_tk()


def icon_image():
    image = Image.new('RGBA', (64, 64), ACCENT)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((12, 17, 52, 47), radius=6, outline='white', width=4)
    draw.line((15, 27, 49, 27), fill='white', width=4)
    draw.ellipse((39, 36, 44, 41), fill='white')
    return image


class DesktopWindow:
    def __init__(self, root, store, *, autostart=True, tray=True):
        self.root, self.store = root, store
        self.controller = Controller(store)
        self.events = queue.Queue()
        self.busy = False
        self.closing = False
        self.login_active = False
        self.tray = None
        self.tray_ready = False
        self.last_show = store.read('show-window', {}).get('at')
        self.next_status = 0
        self.success_until = 0
        self.needs_relogin = False
        self.buttons = []
        self.page = 'setup'
        root.title('GDUFE Campus Balance · 校园余额')
        self.window_icon = ImageTk.PhotoImage(icon_image())
        root.iconphoto(True, self.window_icon)
        root.geometry('820x700')
        root.minsize(740, 680)
        root.configure(bg=BG)
        root.protocol('WM_DELETE_WINDOW', self.hide)
        root.report_callback_exception = self.callback_error
        root.option_add('*Font', ('Microsoft YaHei UI', 10))
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('TCheckbutton', background='white', foreground=INK, padding=5,
                        font=('Microsoft YaHei UI', 10))
        style.configure('TEntry', padding=6)
        self.outer = tk.Frame(root, bg=BG, padx=30, pady=24)
        self.outer.pack(fill='both', expand=True)
        header = tk.Frame(self.outer, bg=BG)
        header.pack(fill='x')
        tk.Label(header, text='GDUFE Campus Balance', bg=BG, fg=INK, font=('Segoe UI', 23, 'bold')).pack(side='left')
        tk.Label(header, text='校园余额助手', bg=BG, fg=MUTED).pack(side='left', padx=14, pady=(12, 0))
        self.body = tk.Frame(self.outer, bg=BG)
        self.body.pack(fill='both', expand=True, pady=(22, 10))
        self.status = tk.StringVar(value='欢迎使用。先连接你的校园卡。')
        tk.Label(self.outer, textvariable=self.status, bg=BG, fg=MUTED, anchor='w',
                 justify='left', wraplength=680).pack(fill='x')
        footer = tk.Frame(self.outer, bg=BG)
        footer.pack(fill='x', pady=(14, 0))
        tk.Label(footer, text='关闭窗口后留在托盘 · 电脑开机且联网时自动查询', bg=BG, fg=MUTED,
                 font=('Microsoft YaHei UI', 9)).pack(side='left')
        self.button(footer, '退出程序', self.quit, secondary=True, tracked=False).pack(side='right')
        if tray:
            self.start_tray()
        if self.controller.ready():
            self.dashboard()
            if autostart:
                root.after(200, self.refresh)
        else:
            self.setup()
            if autostart and self.store.read('login-draft', {}).get('token'):
                root.after(200, self.begin_setup)
        root.after(100, self.pump)

    def button(self, parent, text, command, *, secondary=False, tracked=True):
        button = tk.Button(parent, text=text, command=command, relief='flat', bd=0,
                           bg='white' if secondary else ACCENT, fg=INK if secondary else 'white',
                           activebackground='#DFEAE6' if secondary else '#006551',
                           activeforeground=INK if secondary else 'white', cursor='hand2',
                           padx=17, pady=9, disabledforeground='#97AAA4')
        if tracked:
            self.buttons.append(button)
        return button

    def clear(self):
        for child in self.body.winfo_children():
            child.destroy()
        self.buttons = []

    def setup(self):
        self.clear()
        self.page = 'setup'
        draft = self.store.read('login-draft', {})
        saved_login = bool(draft.get('token') or self.store.read('account', {}).get('token')) and not self.needs_relogin
        tk.Label(self.body, text='已登录，接下来选择房间' if saved_login else '连接校园卡，开启自动提醒', bg=BG, fg=INK,
                 font=('Microsoft YaHei UI', 21, 'bold')).pack(anchor='w')
        tk.Label(self.body, text='在软件里核对学校已绑定的房间，或选择新的房间。' if saved_login else '浏览器只负责学校登录，登录后自动返回软件选择房间。',
                 bg=BG, fg=MUTED).pack(anchor='w', pady=(9, 20))
        panel = tk.Frame(self.body, bg='white', padx=24, pady=20)
        panel.pack(fill='x')
        tk.Label(panel, text='你想关注哪些余额？', bg='white', fg=INK,
                 font=('Microsoft YaHei UI', 12, 'bold')).pack(anchor='w')
        account = self.store.read('account', {})
        self.setup_cfg = copy.deepcopy(draft.get('config') or account.get('config') or default_config())
        self.selected = {}
        self.select_widgets = []
        row = tk.Frame(panel, bg='white')
        row.pack(anchor='w', pady=(12, 8))
        for item in ITEMS:
            var = tk.BooleanVar(value=item in self.setup_cfg['items'])
            self.selected[item] = var
            widget = ttk.Checkbutton(row, text=NAMES[item], variable=var)
            widget.pack(side='left', padx=(0, 15))
            self.select_widgets.append(widget)
        tk.Label(panel, text=f"每天 {self.setup_cfg['query_time']} 自动查询，余额不足时提醒。\n时间和提醒金额可以稍后在设置中修改。",
                 bg='white', fg=MUTED, justify='left', anchor='w').pack(anchor='w', pady=(6, 0))
        tk.Label(self.body, text='① 登录学校账号     ② 软件内确认房间     ③ 自动监控',
                 bg=BG, fg=INK).pack(anchor='w', pady=(24, 12))
        row = tk.Frame(self.body, bg=BG)
        row.pack(fill='x')
        self.button(row, '继续选择房间' if saved_login else '登录学校账号', self.begin_setup).pack(side='left')
        if saved_login:
            self.button(row, '重新登录', self.restart_setup, secondary=True).pack(side='left', padx=10)
            self.status.set('登录已保存，尚未开启监控。点击“继续选择房间”完成设置。')
        if self.controller.ready():
            self.button(row, '返回余额', self.dashboard, secondary=True).pack(side='left', padx=10)
        self.cancel_button = self.button(row, '取消登录', self.cancel_login, secondary=True, tracked=False)
        self.cancel_button.configure(state='disabled')

    def dashboard(self):
        self.clear()
        self.page = 'dashboard'
        top = tk.Frame(self.body, bg=BG)
        top.pack(fill='x')
        tk.Label(top, text='你的校园余额', bg=BG, fg=INK,
                 font=('Microsoft YaHei UI', 21, 'bold')).pack(side='left')
        self.button(top, '设置', self.settings, secondary=True).pack(side='right')
        self.updated = tk.StringVar()
        tk.Label(self.body, textvariable=self.updated, bg=BG, fg=MUTED).pack(anchor='w', pady=(8, 20))
        cards = tk.Frame(self.body, bg=BG)
        cards.pack(fill='x')
        cfg = self.store.read('account', {})['config']
        self.amounts = {}
        for index, item in enumerate(cfg['items']):
            cards.columnconfigure(index, weight=1, uniform='cards')
            card = tk.Frame(cards, bg='white', padx=20, pady=23)
            card.grid(row=0, column=index, sticky='nsew', padx=(0 if index == 0 else 6, 6))
            tk.Label(card, text=NAMES[item], bg='white', fg=MUTED).pack(anchor='w')
            var = tk.StringVar(value='—')
            self.amounts[item] = var
            tk.Label(card, textvariable=var, bg='white', fg=INK,
                     font=('Segoe UI', 25, 'bold')).pack(anchor='w', pady=(14, 5))
            tk.Label(card, text='度' if item == 'electricity' else '元（现金）' if item == 'liwang' else '元',
                     bg='white', fg=MUTED).pack(anchor='w')
            if cfg.get('room_labels', {}).get(item):
                tk.Label(card, text=cfg['room_labels'][item], bg='white', fg=MUTED,
                         wraplength=185, justify='left', font=('Microsoft YaHei UI', 9)).pack(anchor='w', pady=(8, 0))
        self.monitor_text = tk.StringVar()
        tk.Label(self.body, textvariable=self.monitor_text, bg=BG, fg=ACCENT,
                 justify='left', anchor='w').pack(fill='x', pady=(24, 18))
        row = tk.Frame(self.body, bg=BG)
        row.pack(fill='x')
        self.button(row, '刷新余额', self.refresh).pack(side='left')
        self.renew_button = self.button(row, '重新登录', self.begin_renewal, secondary=True)
        self.cancel_button = self.button(row, '取消登录', self.cancel_login, secondary=True, tracked=False)
        self.cancel_button.configure(state='disabled')
        self.render_state()

    def render_state(self):
        if self.page != 'dashboard':
            return
        cfg = self.store.read('account', {})['config']
        state = self.store.read('state', {})
        for item, var in self.amounts.items():
            var.set(state.get('balances', {}).get(item, {}).get('amount', '—'))
        stamp = state.get('last_success', '')[:19].replace('T', ' ')
        self.updated.set(('最近更新 ' + stamp if stamp else '等待首次查询') +
                         (' · 上次成功余额' if state.get('status') != 'ok' and stamp else ''))
        running = self.store.running()
        self.monitor_text.set(('● 自动监控中' if running else '○ 自动监控尚未运行') + f"   每天 {cfg['query_time']} 查询（北京时间）")
        if not self.busy:
            status = state.get('status')
            if status == 'auth':
                self.renew_button.pack(side='left', padx=10)
            else:
                self.renew_button.pack_forget()
            text = {'auth': '登录已过期，点击“重新登录”即可继续。',
                    'network': '网络暂时不可用，显示上次余额。联网后可点击刷新。',
                    'business': '学校暂未接受查询，可在设置中重新选择房间。',
                    'parse': '学校返回的余额暂时无法识别，显示上次成功结果。',
                    'ok': '余额低于设定值时会提醒你。无需保持此窗口打开。'}.get(status, '准备查询余额…')
            if time.monotonic() >= self.success_until:
                self.status.set(text)

    def callback_error(self, kind, value, tb):
        # Record code locations only; never exception text, locals, or credentials.
        self.store.write('ui-error', {'type': kind.__name__,
            'frames': [{'file': Path(frame.filename).name, 'line': frame.lineno, 'function': frame.name}
                       for frame in traceback.extract_tb(tb)]})
        self.busy = False
        self.show()
        self.status.set('界面处理未完成，已保留诊断记录。请重新打开程序后重试。')
        messagebox.showerror('GDUFE Campus Balance', '界面处理未完成，不能确认登录已保存。\n已保留不含密码和令牌的诊断记录，请反馈此提示。', parent=self.root)

    def start_tray(self):
        def ready(icon):
            icon.visible = True
            self.events.put(('tray_ready', None))
        self.tray = pystray.Icon('GDUFE Campus Balance', icon_image(), 'GDUFE Campus Balance · 校园余额', pystray.Menu(
            pystray.MenuItem('打开余额窗口', lambda: self.events.put(('show', None)), default=True),
            pystray.MenuItem('刷新余额', lambda: self.events.put(('refresh', None))),
            pystray.MenuItem('退出并停止监控', lambda: self.events.put(('quit', None)))))
        def run():
            try:
                self.tray.run(setup=ready)
            except Exception:
                self.events.put(('tray_failed', None))
        threading.Thread(target=run, daemon=True).start()

    def show(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def hide(self):
        if self.tray_ready:
            self.root.withdraw()
        else:
            self.root.iconify()

    def job(self, operation, done=None, text='正在处理…'):
        if self.busy:
            return
        self.busy = True
        self.status.set(text)
        for button in self.buttons:
            button.configure(state='disabled')
        for widget in getattr(self, 'select_widgets', []):
            if widget.winfo_exists():
                widget.configure(state='disabled')
        def worker():
            try:
                result = operation()
                self.events.put(('done', (done, result, None)))
            except asyncio.CancelledError:
                self.events.put(('done', (None, None, '登录已取消，原有设置保留。')))
            except QueryError as error:
                if error.kind == 'auth':
                    self.events.put(('auth_required', None))
                message = {'auth': '学校登录已过期，请重新登录。', 'network': '网络暂时不可用，请稍后重试。',
                           'business': '学校暂未接受这次查询，请核对选择后重试。',
                           'parse': '学校返回的房间或余额格式暂时无法识别。'}.get(error.kind, '查询未完成')
                self.events.put(('done', (None, None, message)))
            except ValueError as error:
                self.events.put(('done', (None, None, str(error))))
            except Exception:
                self.events.put(('done', (None, None, '操作未完成，请检查网络及 Edge / Chrome 后重试。')))
        threading.Thread(target=worker, daemon=True).start()

    def pump(self):
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == 'done':
                    callback, result, error = payload
                    self.busy = False
                    self.login_active = False
                    self.cancel_button.configure(state='disabled')
                    self.cancel_button.pack_forget()
                    for button in self.buttons:
                        if button.winfo_exists():
                            button.configure(state='normal')
                    for widget in getattr(self, 'select_widgets', []):
                        if widget.winfo_exists():
                            widget.configure(state=getattr(widget, '_ready_state', 'normal'))
                    if error:
                        if self.page == 'setup' and self.store.read('login-draft', {}).get('token'):
                            self.setup()
                        self.status.set(error)
                        self.show()
                        messagebox.showinfo('GDUFE Campus Balance', error, parent=self.root)
                    elif callback:
                        callback(result)
                elif event == 'progress':
                    self.status.set(payload)
                elif event == 'auth_required':
                    self.needs_relogin = True
                    self.setup()
                elif event == 'tray_ready':
                    self.tray_ready = True
                elif event == 'tray_failed':
                    self.tray_ready = False
                    self.show()
                elif event == 'show':
                    self.show()
                elif event == 'refresh' and not self.busy and self.controller.ready():
                    self.refresh()
                elif event == 'quit':
                    self.quit()
        except queue.Empty:
            pass
        if self.closing:
            return
        if time.monotonic() >= self.next_status:
            self.next_status = time.monotonic() + 2
            signal = self.store.read('show-window', {}).get('at')
            if signal != self.last_show:
                self.last_show = signal
                self.show()
            self.render_state()
        self.root.after(100, self.pump)

    def refresh(self):
        self.job(self.controller.refresh, lambda _: self.render_state(), '正在更新余额…')

    def begin_setup(self):
        cfg = copy.deepcopy(self.setup_cfg)
        cfg['items'] = [item for item in ITEMS if self.selected[item].get()]
        if not cfg['items']:
            messagebox.showinfo('选择项目', '请至少选择一个余额项目。', parent=self.root)
            return
        for item, value in default_config()['thresholds'].items():
            cfg.setdefault('thresholds', {}).setdefault(item, value)
        if not self.needs_relogin and (self.store.read('login-draft', {}).get('token') or self.store.read('account', {}).get('token')):
            self.job(lambda: self.controller.load_rooms(cfg), self.show_rooms, '正在读取学校绑定及房间列表…')
        else:
            self.start_login(cfg, True)

    def restart_setup(self):
        self.store.write('login-draft', {})
        self.needs_relogin = True
        self.setup()
        self.begin_setup()

    def begin_renewal(self):
        self.start_login(self.store.read('account')['config'], False)

    def start_login(self, cfg, discover):
        if self.busy:
            return
        self.controller.cancel_requested.clear()
        self.job(lambda: self.controller.prepare_login(cfg, discover,
                 lambda text: self.events.put(('progress', text))),
                 lambda result: self.finish_login(result, discover), '正在打开学校登录窗口…')
        self.login_active = True
        self.cancel_button.pack(side='right')
        self.cancel_button.configure(state='normal')

    def cancel_login(self):
        self.controller.cancel_login()
        self.status.set('正在关闭本次登录…')

    def finish_login(self, result, discover):
        self.show()
        self.needs_relogin = False
        if result[2] is None:
            self.store.write('login-ui-status', {'phase': 'choosing_rooms' if discover else 'verifying'})
            if discover:
                self.job(lambda: self.controller.load_rooms(result[0]), self.show_rooms, '登录成功，正在读取已绑定的房间…')
            else:
                self.job(lambda: self.controller.finish_rooms(result[0]), self.login_saved, '登录成功，正在恢复监控…')
            return
        self.store.write('login-ui-status', {'phase': 'awaiting_confirmation' if discover else 'saving'})
        if discover:
            cfg, _, balances = result
            summary = '\n'.join(f"{NAMES[k]}：{balances[k]['amount']} {balances[k]['unit']}" for k in cfg['items'])
            if not messagebox.askyesno('确认关注的余额', summary + '\n\n这些是你刚才选择的房间 / 账户吗？\n确认后自动开始监控。', parent=self.root):
                self.store.write('login-ui-status', {'phase': 'confirmation_declined'})
                self.status.set('未保存这次选择，可重新登录并选择房间。')
                return
        self.store.write('login-ui-status', {'phase': 'saving'})
        self.job(lambda: self.controller.commit_login(result), self.login_saved, '正在保存并启动自动监控…')

    def login_saved(self, _):
        self.store.write('login-ui-status', {'phase': 'saved'})
        self.dashboard()
        self.success_until = time.monotonic() + 30
        self.status.set('登录成功，已开始自动监控。你可以直接关闭窗口。')
        self.show()

    def show_rooms(self, result):
        from .room_picker import RoomPicker
        cfg, catalog = result
        self.room_picker = RoomPicker(self, cfg, catalog)
        self.show()

    def settings(self):
        if self.busy:
            return
        cfg = copy.deepcopy(self.store.read('account')['config'])
        window = tk.Toplevel(self.root)
        window.title('提醒设置')
        window.configure(bg='white')
        window.resizable(False, False)
        window.transient(self.root)
        window.grab_set()
        panel = tk.Frame(window, bg='white', padx=28, pady=24)
        panel.pack()
        tk.Label(panel, text='提醒设置', bg='white', fg=INK, font=('Microsoft YaHei UI', 17, 'bold')).grid(row=0, column=0, columnspan=2, sticky='w', pady=(0, 20))
        tk.Label(panel, text='每天查询时间', bg='white').grid(row=1, column=0, sticky='w', padx=(0, 20))
        when = tk.StringVar(value=cfg['query_time'])
        ttk.Entry(panel, textvariable=when, width=15).grid(row=1, column=1)
        values = {}
        for row, item in enumerate(cfg['items'], 2):
            tk.Label(panel, text=NAMES[item] + ('（度）' if item == 'electricity' else '（元）'), bg='white').grid(row=row, column=0, sticky='w', pady=10)
            var = tk.StringVar(value='' if cfg['thresholds'][item] is None else cfg['thresholds'][item])
            values[item] = var
            ttk.Entry(panel, textvariable=var, width=15).grid(row=row, column=1)
        n = len(values) + 2
        tk.Label(panel, text='低于设定值时提醒；留空关闭该项提醒。\n关闭主窗口仍继续监控，退出程序则停止。', bg='white', fg=MUTED, justify='left').grid(row=n, column=0, columnspan=2, sticky='w', pady=15)
        def save():
            cfg['query_time'] = when.get().strip()
            cfg['thresholds'].update({k: v.get().strip() or None for k, v in values.items()})
            try:
                validate(cfg)
            except ValueError:
                messagebox.showerror('请检查设置', '时间请填 HH:MM，例如 22:00；提醒值请填非负数字或留空。', parent=window)
                return
            window.destroy()
            self.job(lambda: self.controller.save_settings(cfg), lambda _: self.dashboard(), '正在更新提醒设置…')
        self.button(panel, '保存设置', save, tracked=False).grid(row=n+1, column=1, sticky='e')
        def choose():
            window.destroy()
            self.setup()
        self.button(panel, '更换房间 / 项目', choose, secondary=True, tracked=False).grid(row=n+1, column=0, sticky='w')

    def quit(self):
        if self.busy:
            if self.login_active:
                self.cancel_login()
            self.status.set('正在完成当前操作，请稍后再退出。')
            self.show()
            return
        self.job(lambda: stop(self.store), lambda _: self.destroy(), '正在停止监控并退出…')

    def destroy(self):
        self.closing = True
        if self.tray:
            self.tray.stop()
        self.root.destroy()


def main(choose_rooms=False):
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    store = DesktopStore()
    lock = store.lock('window.lock')
    try:
        lock.__enter__()
    except ValueError:
        store.write('show-window', {'at': time.time_ns()})
        return
    try:
        root = tk.Tk()
        app = DesktopWindow(root, store, autostart=not choose_rooms)
        if choose_rooms:
            app.setup()
            root.after(200, app.begin_setup)
        root.mainloop()
    finally:
        lock.__exit__(None, None, None)


if __name__ == '__main__':
    main()
