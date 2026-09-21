"""Native calendar button; no date-format typing required."""
import calendar
from datetime import date
import tkinter as tk
from tkinter import ttk


class DatePicker(ttk.Button):
    def __init__(self, parent, value):
        super().__init__(parent, width=19, command=self.open_calendar)
        self.set(value)

    def set(self, value):
        self.value = value
        self.configure(text=value.strftime('%Y年%m月%d日') + ' ▾')

    def get(self):
        return self.value.isoformat()

    def open_calendar(self):
        popup = tk.Toplevel(self)
        popup.title('选择日期')
        popup.resizable(False, False)
        popup.transient(self.winfo_toplevel())
        popup.grab_set()
        popup.bind('<Escape>', lambda _: popup.destroy())
        panel = ttk.Frame(popup, padding=12)
        panel.pack()
        header = ttk.Frame(panel)
        header.pack(fill='x', pady=(0, 10))
        year = tk.StringVar(value=str(self.value.year))
        month = tk.StringVar(value=str(self.value.month))
        years = ttk.Combobox(header, textvariable=year, state='readonly', width=6,
                             values=list(range(min(2000, self.value.year), max(date.today().year + 6, self.value.year + 1))))
        months = ttk.Combobox(header, textvariable=month, state='readonly', width=4,
                              values=list(range(1, 13)))
        grid = ttk.Frame(panel)
        grid.pack()

        def select(value):
            self.set(value)
            popup.destroy()

        def draw():
            for child in grid.winfo_children():
                child.destroy()
            y, m = int(year.get()), int(month.get())
            for col, label in enumerate('一二三四五六日'):
                ttk.Label(grid, text=label, anchor='center', width=4).grid(row=0, column=col, pady=5)
            for row, week in enumerate(calendar.monthcalendar(y, m), 1):
                for col, day in enumerate(week):
                    if day:
                        value = date(y, m, day)
                        button = ttk.Button(grid, text=str(day), width=4,
                                            command=lambda value=value: select(value))
                        button.grid(row=row, column=col, padx=1, pady=1)
                        if value == self.value:
                            button.state(['pressed'])

        def move(offset):
            index = int(year.get()) * 12 + int(month.get()) - 1 + offset
            y, m = divmod(index, 12)
            if 1 <= y <= 9999:
                year.set(str(y))
                month.set(str(m + 1))
                draw()

        ttk.Button(header, text='‹', width=3, command=lambda: move(-1)).pack(side='left')
        years.pack(side='left', padx=(6, 2))
        ttk.Label(header, text='年').pack(side='left')
        months.pack(side='left', padx=(6, 2))
        ttk.Label(header, text='月').pack(side='left')
        ttk.Button(header, text='›', width=3, command=lambda: move(1)).pack(side='right')
        years.bind('<<ComboboxSelected>>', lambda _: draw())
        months.bind('<<ComboboxSelected>>', lambda _: draw())
        ttk.Button(panel, text='选择今天', command=lambda: select(date.today())).pack(fill='x', pady=(10, 0))
        draw()
        popup.update_idletasks()
        x = min(self.winfo_rootx(), popup.winfo_screenwidth() - popup.winfo_reqwidth() - 16)
        y = min(self.winfo_rooty() + self.winfo_height(), popup.winfo_screenheight() - popup.winfo_reqheight() - 60)
        popup.geometry(f'+{max(0, x)}+{max(0, y)}')
