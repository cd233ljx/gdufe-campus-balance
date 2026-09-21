# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CardsClaim contributors
"""Small Windows dashboard: setup once, then automatic monitoring."""
import asyncio
import copy
import ctypes
from datetime import datetime, timedelta
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
from .model import validate, TZ
from .store import DesktopStore
from .layout import ScrollPane, fit_window
from .startup import StartupRegistration
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
    def __init__(self, root, store, *, autostart=True, tray=True, startup=None):
        self.root, self.store = root, store
        self.controller = Controller(store)
        self.startup = startup if startup is not None else StartupRegistration()
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
        self.guide_window = None
        self.buttons = []
        self.page = 'setup'
        root.title('GDUFE Campus Balance · 校园余额')
        self.window_icon = ImageTk.PhotoImage(icon_image())
        self.check_images = []
        for selected in (False, True):
            mark = Image.new('RGBA', (96, 80), 'white')
            draw = ImageDraw.Draw(mark)
            draw.rounded_rectangle((4, 8, 68, 72), radius=12,
                                   fill=ACCENT if selected else 'white',
                                   outline=ACCENT if selected else '#8B9CA6', width=5)
            if selected:
                draw.line((18, 39, 31, 53, 55, 27), fill='white', width=8, joint='curve')
            self.check_images.append(ImageTk.PhotoImage(mark.resize((24, 20), Image.Resampling.LANCZOS)))
        root.iconphoto(True, self.window_icon)
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
        header.grid(row=0, column=0, sticky='ew')
        tk.Label(header, text='GDUFE Campus Balance', bg=BG, fg=INK, font=('Segoe UI', 23, 'bold')).pack(side='left')
        tk.Label(header, text='校园余额助手', bg=BG, fg=MUTED).pack(side='left', padx=14, pady=(12, 0))
        self.outer.columnconfigure(0, weight=1)
        self.outer.rowconfigure(1, weight=1)
        self.body_scroll = ScrollPane(self.outer, bg=BG)
        self.body_scroll.grid(row=1, column=0, sticky='nsew', pady=(14, 8))
        self.body = tk.Frame(self.body_scroll.content, bg=BG)
        self.body.pack(fill='both', expand=True)
        self.status = tk.StringVar(value='欢迎使用。先连接你的校园卡。')
        tk.Label(self.outer, textvariable=self.status, bg=BG, fg=MUTED, anchor='w',
                 justify='left', wraplength=680).grid(row=2, column=0, sticky='ew')
        footer = tk.Frame(self.outer, bg=BG)
        footer.grid(row=3, column=0, sticky='ew', pady=(10, 0))
        tk.Label(footer, text='关闭窗口后留在托盘 · 电脑开机且联网时自动查询', bg=BG, fg=MUTED,
                 font=('Microsoft YaHei UI', 9)).pack(side='left')
        self.button(footer, '关闭程序', self.quit, secondary=True, tracked=False).pack(side='right')
        self.button(footer, '使用指南', self.show_guide, secondary=True, tracked=False).pack(side='right', padx=8)
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
        fit_window(root, 1000, 900, saved=store.read('preferences', {}).get('window_bounds'))
        self.window_save_after = None
        root.bind('<Configure>', self.window_changed, add='+')
        root.after(100, self.pump)
        if autostart:
            if (not store.read('preferences', {}).get('guide_seen')
                    and not store.read('account', {}).get('token')
                    and not store.read('login-draft', {}).get('token')):
                root.after(100, self.show_guide)
            root.after(500, self.ask_startup)

    def show_guide(self):
        if self.busy:
            return
        if self.guide_window and self.guide_window.winfo_exists():
            self.guide_window.lift()
            return
        window = tk.Toplevel(self.root)
        self.guide_window = window
        window.title('欢迎使用 · 校园余额助手')
        window.configure(bg=BG)
        window.transient(self.root)
        window.grab_set()
        outer = tk.Frame(window, bg=BG, padx=28, pady=24)
        outer.pack(fill='both', expand=True)
        progress = tk.StringVar()
        tk.Label(outer, textvariable=progress, bg=BG, fg=ACCENT,
                 font=('Microsoft YaHei UI', 11, 'bold')).pack(anchor='w')
        navigation = tk.Frame(outer, bg=BG)
        navigation.pack(side='bottom', fill='x')
        guide_scroll = ScrollPane(outer, bg='white')
        guide_scroll.pack(fill='both', expand=True, pady=18)
        content = tk.Frame(guide_scroll.content, bg='white', padx=24, pady=20)
        content.pack(fill='both', expand=True)
        steps = [
            ('连接校园卡，确认自己的房间',
             '选择余额项目 → 登录学校账号 → 确认房间',
             '1. 选择宿舍电量、自来水或力王热水。\n\n'
             '2. 在 Edge / Chrome 中完成学校登录，随后会自动回到软件。\n\n'
             '3. 核对软件中显示的校区、楼栋、房间或手机号，点击确认后开始监控。'),
            ('邮箱提醒服务',
             '设置 → QQ 邮箱告警 → 发送测试邮件并保存',
             '填写自己的 QQ 邮箱地址和 SMTP 授权码，即可给自己发送告警（授权码不是 QQ 登录密码。\n\n'
             '绑定时先发送测试邮件，成功后才保存；请检查收件箱或垃圾箱。\n\n'
             '余额首次低于阈值会提醒，持续低余额不重复发送；恢复后再次低于才重发。阈值可在设置中修改。'),
            ('查看历史记录',
             '每 30 分钟查询记录一次，点击“查询历史”可查看记录',
             '在“查询历史”点击日历选择开始和结束日期，也可选今天、近 7 天或近 30 天。记录多时可翻页查看。\n\n'
             '关闭主窗口后软件留在托盘继续监控；点击“关闭程序”并确认后才会停止。关机、睡眠或断网时无法查询。\n\n'
             '稍后可选择是否开机自启。此引导可随时通过主窗口底部的“使用指南”重新打开。')]
        def finish():
            preferences = self.store.read('preferences', {})
            preferences['guide_seen'] = True
            self.store.write('preferences', preferences)
            window.destroy()
            self.guide_window = None
        window.protocol('WM_DELETE_WINDOW', finish)
        def render(index):
            for child in content.winfo_children():
                child.destroy()
            for child in navigation.winfo_children():
                child.destroy()
            progress.set(f'快速上手  {index + 1} / {len(steps)}')
            title, subtitle, body = steps[index]
            tk.Label(content, text=title, bg='white', fg=INK,
                     font=('Microsoft YaHei UI', 18, 'bold'), wraplength=580,
                     justify='left').pack(anchor='w')
            tk.Label(content, text=subtitle, bg='white', fg=ACCENT,
                     wraplength=570, justify='left').pack(anchor='w', pady=(10, 20))
            tk.Label(content, text=body, bg='white', fg=INK, wraplength=570,
                     justify='left', anchor='nw').pack(fill='x')
            self.button(navigation, '跳过引导', finish, secondary=True, tracked=False).pack(side='left')
            self.button(navigation, '开始使用' if index == len(steps) - 1 else '下一步',
                        finish if index == len(steps) - 1 else lambda: render(index + 1),
                        tracked=False).pack(side='right')
            if index:
                self.button(navigation, '上一步', lambda: render(index - 1),
                            secondary=True, tracked=False).pack(side='right', padx=8)
        render(0)
        fit_window(window, 700, 510)

    def window_changed(self, event):
        if event.widget != self.root or self.closing or self.root.state() != 'normal':
            return
        if self.window_save_after:
            self.root.after_cancel(self.window_save_after)
        self.window_save_after = self.root.after(600, self.remember_window)

    def remember_window(self):
        self.window_save_after = None
        if self.root.state() != 'normal':
            return
        preferences = self.store.read('preferences', {})
        preferences['window_bounds'] = {'x': self.root.winfo_x(), 'y': self.root.winfo_y(),
                                        'width': self.root.winfo_width(), 'height': self.root.winfo_height()}
        self.store.write('preferences', preferences)

    def checkbox(self, parent, text, variable, command=None):
        return tk.Checkbutton(parent, text=text, variable=variable, command=command,
                              indicatoron=False, image=self.check_images[0],
                              selectimage=self.check_images[1], compound='left',
                              bg='white', activebackground='white', selectcolor='white',
                              fg=INK, bd=0, padx=5, pady=5, cursor='hand2',
                              highlightthickness=1, highlightbackground='white', highlightcolor=ACCENT)

    def ask_startup(self):
        preferences = self.store.read('preferences', {})
        if preferences.get('startup_prompted'):
            return
        if self.busy or (self.guide_window and self.guide_window.winfo_exists()):
            self.root.after(500, self.ask_startup)
            return
        enable = messagebox.askyesno('开机自启',
            '是否开机后自动启动 GDUFE Campus Balance？\n\n登录 Windows 后自动打开软件并恢复监控。\n之后可以在“设置”中随时修改。',
            default=messagebox.NO, parent=self.root)
        try:
            self.startup.set_enabled(enable)
            preferences['startup_prompted'] = True
            self.store.write('preferences', preferences)
        except (OSError, ValueError):
            messagebox.showerror('开机自启', '开机自启设置未能保存，请稍后在设置中重试。', parent=self.root)

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
        self.body_scroll.canvas.yview_moveto(0)

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
            widget = self.checkbox(row, NAMES[item], var)
            widget.pack(side='left', padx=(0, 15))
            self.select_widgets.append(widget)
        tk.Label(panel, text="常驻期间每 30 分钟自动查询，余额不足时提醒。\n提醒金额可以稍后在设置中修改。",
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
        self.button(self.body, '可选：绑定 QQ 邮箱告警', self.email_settings, secondary=True).pack(anchor='w', pady=(14, 0))
        tk.Label(self.body, text='不绑定也可使用；以后可在设置里添加。绑定时会发送测试邮件。',
                 bg=BG, fg=MUTED).pack(anchor='w', pady=(6, 0))

    def dashboard(self):
        self.clear()
        self.page = 'dashboard'
        top = tk.Frame(self.body, bg=BG)
        top.pack(fill='x')
        tk.Label(top, text='你的校园余额', bg=BG, fg=INK,
                 font=('Microsoft YaHei UI', 21, 'bold')).pack(side='left')
        self.button(top, '设置', self.settings, secondary=True).pack(side='right')
        self.button(top, '查询历史', self.history, secondary=True).pack(side='right', padx=8)
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
        self.monitor_text.set(('● 自动监控中' if running else '○ 自动监控尚未运行') + "   每 30 分钟查询一次（常驻期间）")
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
                    'ok': '点击右上角“x”按钮关闭窗口，余额低于设定值时将发送提醒。无需保持此窗口打开。'}.get(status, '准备查询余额…')
            if time.monotonic() >= self.success_until:
                mail = self.store.read('email-state', {})
                if mail.get('error'):
                    text = '邮箱告警发送失败，将自动重试；可在设置中重新绑定邮箱。'
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
            pystray.MenuItem('关闭程序', lambda: self.events.put(('quit', None)))))
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
        self.remember_window()
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
            if button.winfo_exists():
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
        scroll = ScrollPane(window)
        scroll.pack(fill='both', expand=True)
        panel = tk.Frame(scroll.content, bg='white', padx=28, pady=24)
        panel.pack(fill='both', expand=True)
        tk.Label(panel, text='提醒设置', bg='white', fg=INK, font=('Microsoft YaHei UI', 17, 'bold')).grid(row=0, column=0, columnspan=2, sticky='w', pady=(0, 20))
        tk.Label(panel, text='查询频率：常驻期间每 30 分钟一次', bg='white', fg=ACCENT).grid(row=1, column=0, columnspan=2, sticky='w')
        mail = self.store.read('email', {})
        mail_card = tk.Frame(panel, bg='#E9F5F1', padx=18, pady=16,
                             highlightbackground='#A9D7C6', highlightthickness=1)
        mail_card.grid(row=2, column=0, columnspan=2, sticky='ew', pady=(18, 12))
        tk.Label(mail_card, text='QQ 邮箱告警', bg='#E9F5F1', fg=INK,
                 font=('Microsoft YaHei UI', 14, 'bold')).pack(anchor='w')
        mail_status = ('已绑定：' + mail.get('address', '') if mail.get('enabled') else
                       '尚未开启 · 绑定后，余额不足会发送邮件提醒')
        tk.Label(mail_card, text=mail_status, bg='#E9F5F1', fg=MUTED,
                 wraplength=440, justify='left').pack(anchor='w', pady=(6, 12))
        def email():
            window.destroy()
            self.email_settings()
        self.button(mail_card, '管理邮箱 / 发送测试邮件' if mail.get('enabled') else '绑定 QQ 邮箱，开启告警',
                    email, tracked=False).pack(fill='x')
        values = {}
        for row, item in enumerate(cfg['items'], 3):
            tk.Label(panel, text=NAMES[item] + ('（度）' if item == 'electricity' else '（元）'), bg='white').grid(row=row, column=0, sticky='w', pady=10)
            var = tk.StringVar(value='' if cfg['thresholds'][item] is None else cfg['thresholds'][item])
            values[item] = var
            ttk.Entry(panel, textvariable=var, width=15).grid(row=row, column=1)
        n = len(values) + 3
        try:
            initial_startup = self.startup.enabled()
        except (OSError, ValueError):
            messagebox.showerror('开机自启', '无法读取开机自启状态，请稍后重试。', parent=window)
            window.destroy()
            return
        startup_enabled = tk.BooleanVar(value=initial_startup)
        def toggle_startup():
            try:
                self.startup.set_enabled(startup_enabled.get())
                preferences = self.store.read('preferences', {})
                preferences['startup_prompted'] = True
                self.store.write('preferences', preferences)
            except (OSError, ValueError):
                startup_enabled.set(not startup_enabled.get())
                messagebox.showerror('开机自启', '开机自启设置未能保存，请重试。', parent=window)
        self.checkbox(panel, '开机自启（登录 Windows 后自动运行）', startup_enabled,
                      toggle_startup).grid(row=n, column=0, columnspan=2, sticky='w', pady=(12, 0))
        n += 1
        tk.Label(panel, text='低于设定值时提醒；留空关闭该项提醒。\n关闭主窗口仍继续监控；关闭程序并确认后停止。', bg='white', fg=MUTED, justify='left').grid(row=n, column=0, columnspan=2, sticky='w', pady=15)
        def save():
            cfg['thresholds'].update({k: v.get().strip() or None for k, v in values.items()})
            try:
                validate(cfg)
            except ValueError:
                messagebox.showerror('请检查设置', '提醒值请填非负数字或留空。', parent=window)
                return
            window.destroy()
            self.job(lambda: self.controller.save_settings(cfg), lambda _: self.dashboard(), '正在更新提醒设置…')
        self.button(panel, '保存设置', save, tracked=False).grid(row=n+1, column=1, sticky='e')
        def choose():
            window.destroy()
            self.setup()
        self.button(panel, '更换房间 / 项目', choose, secondary=True, tracked=False).grid(row=n+1, column=0, sticky='w')
        window.update_idletasks()
        fit_window(window, panel.winfo_reqwidth() + 20, panel.winfo_reqheight() + 20)

    def email_settings(self):
        if self.busy:
            return
        saved = self.store.read('email', {})
        window = tk.Toplevel(self.root)
        window.title('QQ 邮箱告警（可选）')
        window.configure(bg='white')
        window.transient(self.root)
        window.grab_set()
        window.resizable(False, False)
        scroll = ScrollPane(window)
        scroll.pack(fill='both', expand=True)
        panel = tk.Frame(scroll.content, bg='white', padx=26, pady=24)
        panel.pack(fill='both', expand=True)
        tk.Label(panel, text='绑定 QQ 邮箱，接收低余额提醒', bg='white', fg=INK,
                 font=('Microsoft YaHei UI', 16, 'bold')).pack(anchor='w')
        tk.Label(panel, text='填写自己的 QQ 邮箱和 SMTP 授权码，邮件会发送到这个邮箱。\n'
                 '首次低于阈值提醒一次，恢复后再次低于才重发。',
                 bg='white', fg=MUTED, justify='left').pack(anchor='w', pady=12)
        address = tk.StringVar(value=saved.get('address', ''))
        code = tk.StringVar()
        for label, variable, hidden in [('QQ 邮箱地址', address, False), ('SMTP 授权码', code, True)]:
            tk.Label(panel, text=label, bg='white').pack(anchor='w', pady=(8, 3))
            ttk.Entry(panel, textvariable=variable, width=46, show='●' if hidden else '').pack(fill='x')
        guidance = ('已保存授权码；邮箱不变且授权码留空时，使用原授权码。\n' if saved.get('enabled') else '')
        tk.Label(panel, text=guidance + '获取授权码：登录 QQ 邮箱网页版 → 设置 → 账号与安全\n'
                 '找到 POP3/IMAP/SMTP 服务，开启 SMTP 并生成授权码。\n'
                 '这里填写授权码，不是 QQ 密码。授权码只在本机加密保存。\n'
                 '点击下方按钮发送测试邮件；成功后保存，失败保留原设置。',
                 bg='white', fg=MUTED, justify='left').pack(anchor='w', pady=14)
        mail = self.store.read('email-state', {})
        if mail.get('error'):
            tk.Label(panel, text=mail['error'], bg='white', fg='#B54708', wraplength=470,
                     justify='left').pack(anchor='w', pady=(0, 10))
        def close():
            if not self.busy:
                window.destroy()
        window.protocol('WM_DELETE_WINDOW', close)
        def done(_):
            window.destroy()
            messagebox.showinfo('邮箱绑定成功', '测试邮件已提交给 QQ 邮箱，绑定已保存。\n'
                '请查看收件箱；若未看到，请检查垃圾箱或稍后刷新。\n'
                '低余额提醒会在软件常驻期间自动发送，可在设置中随时修改或关闭。', parent=self.root)
        def bind():
            entered_address, entered_code = address.get().strip(), code.get().strip()
            if not entered_code and entered_address.lower() == saved.get('address'):
                entered_code = saved.get('code', '')
            self.job(lambda: self.controller.bind_email(entered_address, entered_code), done,
                     '正在验证 QQ 邮箱并发送测试邮件…')
        self.button(panel, '发送测试邮件并保存绑定', bind).pack(fill='x')
        if saved.get('enabled'):
            self.button(panel, '关闭邮箱告警并删除授权码',
                        lambda: self.job(self.controller.disable_email,
                            lambda _: (window.destroy(), self.status.set('邮箱告警已关闭，授权码已删除。'))),
                        secondary=True).pack(fill='x', pady=(8, 0))
        self.button(panel, '暂不设置 / 返回', close, secondary=True).pack(fill='x', pady=(8, 0))
        window.update_idletasks()
        fit_window(window, panel.winfo_reqwidth() + 20, panel.winfo_reqheight() + 20)

    def history(self):
        if self.busy:
            return
        from .history import query_range
        from .date_picker import DatePicker
        window = tk.Toplevel(self.root)
        window.title('查询历史 · 余额核对')
        scroll = ScrollPane(window)
        scroll.pack(fill='both', expand=True)
        panel = ttk.Frame(scroll.content, padding=16)
        panel.pack(fill='both', expand=True)
        ttk.Label(panel, text='每次自动、手动及登录验证查询均留存；日期和时间为北京时间。').pack(anchor='w')
        ttk.Label(panel, text='余额变化 = 本次余额 − 同房间上次成功余额；包含充值等变化，不等同于消费账单。').pack(anchor='w', pady=(4, 12))
        filters = ttk.Frame(panel)
        filters.pack(fill='x', pady=(0, 12))
        today = datetime.now(TZ).date()
        ttk.Label(filters, text='开始日期').pack(side='left')
        start_date = DatePicker(filters, today - timedelta(days=6))
        start_date.pack(side='left', padx=(8, 14))
        ttk.Label(filters, text='结束日期').pack(side='left')
        end_date = DatePicker(filters, today)
        end_date.pack(side='left', padx=8)
        shortcuts = ttk.Frame(panel)
        shortcuts.pack(fill='x', pady=(0, 12))
        ttk.Label(shortcuts, text='快捷范围：').pack(side='left')
        def quick(days):
            today = datetime.now(TZ).date()
            start_date.set(today - timedelta(days=days - 1))
            end_date.set(today)
            load()
        for label, days in [('今天', 1), ('近 7 天', 7), ('近 30 天', 30)]:
            ttk.Button(shortcuts, text=label, width=9, command=lambda days=days: quick(days)).pack(side='left', padx=(0, 6))
        ttk.Label(shortcuts, text='包含起止两天；点击日期打开日历。').pack(side='left', padx=8)
        frame = ttk.Frame(panel)
        frame.pack(fill='both', expand=True)
        columns = ('time', 'source', 'item', 'room', 'status', 'amount', 'change', 'previous')
        tree = ttk.Treeview(frame, columns=columns, show='headings')
        for key, title, width in zip(columns, ['查询时间', '来源', '项目', '房间 / 账户', '结果', '余额', '余额变化', '对比查询时间'],
                                     [160, 70, 95, 180, 110, 100, 100, 160]):
            tree.heading(key, text=title)
            tree.column(key, width=width, minwidth=60, stretch=False)
        vertical = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
        horizontal = ttk.Scrollbar(frame, orient='horizontal', command=tree.xview)
        tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        tree.grid(row=0, column=0, sticky='nsew')
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        summary = tk.StringVar()
        ttk.Label(panel, textvariable=summary).pack(anchor='w', pady=(10, 0))
        pagination = ttk.Frame(panel)
        pagination.pack(fill='x', pady=(8, 0))
        current_page = 0
        applied_range = (start_date.get(), end_date.get())
        def load(page=0, paginate=False):
            nonlocal current_page, applied_range
            selected_range = applied_range if paginate else (start_date.get(), end_date.get())
            try:
                records, total = query_range(self.store, *selected_range, page=page)
            except ValueError as error:
                messagebox.showerror('日期范围', str(error), parent=window)
                return
            current_page, applied_range = page, selected_range
            tree.delete(*tree.get_children())
            statuses = {'ok': '成功', 'auth': '登录失效', 'network': '网络失败', 'business': '查询被拒绝',
                        'parse': '格式异常', 'not_queried': '未查询'}
            for entry in records:
                for row in entry['rows']:
                    change = row.get('change')
                    tree.insert('', 'end', values=(entry['time'][:19].replace('T', ' '),
                        {'manual': '手动', 'automatic': '自动', 'login': '登录验证'}.get(entry['source'], entry['source']),
                        NAMES[row['item']], row['room'], statuses.get(row['status'], row['status']),
                        f"{row['amount']} {row['unit']}" if 'amount' in row else '—',
                        f"{change} {row['unit']}" if change is not None else '—',
                        row.get('previous_time', '')[:19].replace('T', ' ') or '—'))
            pages = max(1, (total + 99) // 100)
            summary.set(f'{applied_range[0]} 至 {applied_range[1]} · 共 {total} 次查询 · 第 {page + 1} / {pages} 页' +
                        (' · 每页最多 100 次，可翻页查看全部结果。' if total else ' · 此范围暂无记录。'))
            previous.configure(state='normal' if page > 0 else 'disabled')
            following.configure(state='normal' if (page + 1) * 100 < total else 'disabled')
        previous = ttk.Button(pagination, text='上一页', command=lambda: load(current_page - 1, True))
        previous.pack(side='left')
        following = ttk.Button(pagination, text='下一页', command=lambda: load(current_page + 1, True))
        following.pack(side='left', padx=8)
        ttk.Button(filters, text='查询范围', width=10, command=load).pack(side='left', padx=8)
        load()
        fit_window(window, 1000, 600)

    def quit(self):
        if getattr(self, 'confirming_close', False):
            return
        if self.busy:
            self.status.set('正在完成当前操作，请稍后再关闭程序。')
            self.show()
            return
        self.show()
        self.confirming_close = True
        try:
            confirmed = messagebox.askyesno('确认关闭程序？',
                '关闭程序将同时关闭后台，停止自动余额查询和余额不足告警。\n\n'
                '如果只是暂时不看窗口，请取消，再点击右上角 ×，软件会留在托盘继续监控。\n\n'
                '确定关闭程序吗？', default=messagebox.NO, parent=self.root)
        finally:
            self.confirming_close = False
        if not confirmed:
            return
        self.job(lambda: stop(self.store), lambda _: self.destroy(), '正在关闭后台并停止监控…')

    def destroy(self):
        self.remember_window()
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
