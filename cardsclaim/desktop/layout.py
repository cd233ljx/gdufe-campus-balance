"""Screen-bounded windows with scrollable content at any Windows scaling."""
import ctypes
import tkinter as tk
from tkinter import ttk


def work_area(window, point=None):
    window.update_idletasks()
    try:
        from ctypes import wintypes
        class MonitorInfo(ctypes.Structure):
            _fields_ = [('size', wintypes.DWORD), ('monitor', wintypes.RECT),
                        ('work', wintypes.RECT), ('flags', wintypes.DWORD)]
        api = ctypes.WinDLL('user32', use_last_error=True)
        api.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        api.MonitorFromWindow.restype = wintypes.HANDLE
        api.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
        info = MonitorInfo()
        info.size = ctypes.sizeof(info)
        if point is not None:
            api.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
            api.MonitorFromPoint.restype = wintypes.HANDLE
            handle = api.MonitorFromPoint(wintypes.POINT(*point), 2)
        else:
            handle = api.MonitorFromWindow(window.winfo_id(), 2)
        if api.GetMonitorInfoW(handle, ctypes.byref(info)):
            r = info.work
            return r.left, r.top, r.right, r.bottom
    except (AttributeError, OSError):
        pass
    return 0, 0, window.winfo_screenwidth(), window.winfo_screenheight()


def fit_window(window, width=None, height=None, saved=None):
    owner = window.master.winfo_toplevel() if window.master else None
    if saved and not all(isinstance(saved.get(k), int) for k in ('x', 'y', 'width', 'height')):
        saved = None
    point = None if owner else window.winfo_pointerxy()
    if saved:
        width, height = max(420, saved['width']), max(300, saved['height'])
        point = (saved['x'] + width // 2, saved['y'] + height // 2)
    left, top, right, bottom = work_area(owner or window, point)
    available_width = max(1, right - left - 24)
    available_height = max(1, bottom - top - 64)
    width = min(width or window.winfo_reqwidth(), available_width)
    height = min(height or window.winfo_reqheight(), available_height)
    window.resizable(True, True)
    window.minsize(min(420, available_width), min(300, available_height))
    if saved:
        x, y = saved['x'], saved['y']
    elif owner:
        x = owner.winfo_rootx() + (owner.winfo_width() - width) // 2
        y = owner.winfo_rooty() + (owner.winfo_height() - height) // 2 - 20
    else:
        x = left + (right - left - width) // 2
        y = top + (bottom - top - height - 40) // 2
    x = max(left + 8, min(x, right - width - 16))
    y = max(top + 8, min(y, bottom - height - 48))
    window.geometry(f'{width}x{height}')
    place_window(window, x, y)


def place_window(window, x, y):
    """Absolute desktop coordinates, including monitors left of the primary."""
    window.update_idletasks()
    try:
        from ctypes import wintypes
        api = ctypes.WinDLL('user32', use_last_error=True)
        api.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        api.GetAncestor.restype = wintypes.HWND
        api.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, wintypes.UINT]
        handle = api.GetAncestor(window.winfo_id(), 2)
        if api.SetWindowPos(handle, None, x, y, 0, 0, 0x15):
            return
    except (AttributeError, OSError):
        pass
    window.geometry(f'{x:+d}{y:+d}')


class ScrollPane(ttk.Frame):
    def __init__(self, parent, bg='white'):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.content = tk.Frame(self.canvas, bg=bg)
        self.item = self.canvas.create_window(0, 0, window=self.content, anchor='nw')
        self.vertical = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.horizontal = ttk.Scrollbar(self, orient='horizontal', command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=self.vertical.set, xscrollcommand=self.horizontal.set)
        self.canvas.grid(row=0, column=0, sticky='nsew')
        self.vertical.grid(row=0, column=1, sticky='ns')
        self.horizontal.grid(row=1, column=0, sticky='ew')
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.content.bind('<Configure>', self.update_region)
        self.canvas.bind('<Configure>', self.update_region)
        self.binding = parent.winfo_toplevel().bind('<MouseWheel>', self.wheel, add='+')
        self.top = parent.winfo_toplevel()
        self.bind('<Destroy>', self.cleanup, add='+')

    def update_region(self, event=None):
        width = max(self.content.winfo_reqwidth(), self.canvas.winfo_width())
        height = max(self.content.winfo_reqheight(), self.canvas.winfo_height())
        self.canvas.itemconfigure(self.item, width=width, height=height)
        self.canvas.configure(scrollregion=(0, 0, width, height))

    def wheel(self, event):
        widget = event.widget
        while widget is not None and widget != self:
            widget = getattr(widget, 'master', None)
        if widget != self:
            return
        scroll = self.canvas.xview_scroll if event.state & 1 else self.canvas.yview_scroll
        scroll(-int(event.delta / 120) or (-1 if event.delta > 0 else 1), 'units')

    def cleanup(self, event):
        if event.widget == self:
            try:
                self.top.unbind('<MouseWheel>', self.binding)
            except tk.TclError:
                pass
