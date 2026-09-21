# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CardsClaim contributors
"""Native location selection with school-bound defaults and searchable rooms."""
import copy
import tkinter as tk
from tkinter import ttk, messagebox
from .catalog import FIELDS, selection_params

FIELD_LABELS = {'campus': '校区', 'building': '楼栋', 'room': '房间'}


class RoomPicker:
    def __init__(self, window, cfg, catalog):
        from .gui import BG, INK, MUTED, NAMES
        self.window, self.cfg = window, copy.deepcopy(cfg)
        self.rows, self.boxes, self.maps, self.labels, self.phones = {}, {}, {}, {}, {}
        window.clear()
        window.page = 'rooms'
        window.select_widgets = []
        tk.Label(window.body, text='确认你的房间', bg=BG, fg=INK,
                 font=('Microsoft YaHei UI', 21, 'bold')).pack(anchor='w')
        tk.Label(window.body, text='优先使用校园卡已绑定的房间。核对后，一键开始监控。', bg=BG,
                 fg=MUTED).pack(anchor='w', pady=(8, 16))
        tabs = ttk.Notebook(window.body)
        tabs.pack(fill='both', expand=True)
        for item in cfg['items']:
            row = catalog[item]
            self.rows[item] = dict(row['selected'])
            self.boxes[item], self.maps[item], self.labels[item] = {}, {}, {}
            panel = tk.Frame(tabs, bg='white', padx=24, pady=18)
            tabs.add(panel, text=NAMES[item])
            tk.Label(panel, text=row['source'], bg='white', fg=MUTED).grid(row=0, column=0, columnspan=2, sticky='w', pady=(0, 10))
            panel.columnconfigure(1, weight=1)
            if item == 'liwang':
                tk.Label(panel, text='绑定手机号', bg='white', fg=INK).grid(row=1, column=0, sticky='w', padx=(0, 22))
                phone = tk.StringVar(value=row['selected'].get('telPhone', ''))
                entry = ttk.Entry(panel, textvariable=phone, width=28)
                entry.grid(row=1, column=1, sticky='ew')
                self.phones[item] = phone
                window.select_widgets.append(entry)
                tk.Label(panel, text='填写力王热水账户的手机号；按现金余额提醒。', bg='white', fg=MUTED).grid(row=2, column=0, columnspan=2, sticky='w', pady=20)
                continue
            for index, field in enumerate(FIELDS, 1):
                tk.Label(panel, text=FIELD_LABELS[field], bg='white', fg=INK).grid(row=index, column=0, sticky='w', padx=(0, 22), pady=7)
                box = ttk.Combobox(panel, width=38)
                box.grid(row=index, column=1, sticky='ew', pady=7)
                self.boxes[item][field] = box
                window.select_widgets.append(box)
                box.bind('<<ComboboxSelected>>', lambda event, i=item, f=field: self.changed(i, f))
                if field == 'room':
                    box.bind('<KeyRelease>', lambda event, i=item: self.filter_rooms(i))
                self.populate(item, field, row['options'].get(field, []), row['selected'].get(field))
            tk.Label(panel, text='房间较多时，可以输入房间号筛选。更换校区或楼栋后，请重新选房间。',
                     bg='white', fg=MUTED, wraplength=610, justify='left').grid(row=4, column=0, columnspan=2, sticky='w', pady=(10, 0))
        actions = tk.Frame(window.body, bg=BG)
        actions.pack(fill='x', pady=(15, 0))
        window.button(actions, '确认房间并开始监控', self.submit).pack(side='left')
        window.button(actions, '更改监控项目', window.setup, secondary=True).pack(side='right')
        window.cancel_button = window.button(actions, '取消登录', window.cancel_login, tracked=False)
        window.status.set('请核对每个项目的房间；确认后将显示余额并开始自动提醒。')

    def populate(self, item, field, choices, selected=None):
        names = [choice['name'] for choice in choices]
        mapping = {}
        for choice in choices:
            label = choice['name']
            if names.count(label) > 1:
                label += '（' + choice['value'] + '）'
            mapping[label] = choice['value']
        self.maps[item][field] = mapping
        box = self.boxes[item][field]
        box.configure(values=list(mapping))
        box._ready_state = ('normal' if field == 'room' else 'readonly') if choices else 'disabled'
        box.configure(state=box._ready_state)
        label = next((name for name, value in mapping.items() if value == selected), '')
        box.set(label)
        if label:
            self.rows[item][field] = selected
            self.labels[item][field] = label
        else:
            self.rows[item].pop(field, None)
            self.labels[item].pop(field, None)

    def changed(self, item, field):
        if self.window.busy:
            return
        label = self.boxes[item][field].get()
        value = self.maps[item][field].get(label)
        old = self.rows[item].get(field)
        self.rows[item].pop(field, None)
        if value:
            self.rows[item][field] = value
            self.labels[item][field] = label
        if value == old or field == 'room':
            return
        index = FIELDS.index(field)
        for child in FIELDS[index + 1:]:
            self.populate(item, child, [])
        if value:
            child = FIELDS[index + 1]
            parents = dict(self.rows[item])
            self.window.job(lambda: self.window.controller.load_choices(item, child, parents),
                            lambda options: self.populate(item, child, options),
                            '正在加载' + FIELD_LABELS[child] + '…')

    def filter_rooms(self, item):
        if self.window.busy:
            return
        box = self.boxes[item]['room']
        text = box.get()
        self.rows[item].pop('room', None)
        self.labels[item].pop('room', None)
        mapping = self.maps[item]['room']
        if text in mapping:
            self.rows[item]['room'] = mapping[text]
            self.labels[item]['room'] = text
        box.configure(values=[name for name in mapping if text.casefold() in name.casefold()])

    def submit(self):
        if self.window.busy:
            return
        cfg = copy.deepcopy(self.cfg)
        cfg['params'], cfg['room_labels'] = {}, {}
        try:
            for item in cfg['items']:
                if item == 'liwang':
                    values = {'telPhone': self.phones[item].get().strip()}
                    cfg['room_labels'][item] = '手机号尾号 ' + values['telPhone'][-4:]
                else:
                    values = {field: self.maps[item][field].get(self.boxes[item][field].get(), '') for field in FIELDS}
                    cfg['room_labels'][item] = ' / '.join(self.boxes[item][field].get() for field in ('building', 'room'))
                cfg['params'][item] = selection_params(item, values)
        except ValueError as error:
            messagebox.showinfo('请完成选择', str(error), parent=self.window.root)
            return
        self.window.job(lambda: self.window.controller.finish_rooms(cfg), self.window.login_saved,
                        '正在验证余额并开启监控…')
