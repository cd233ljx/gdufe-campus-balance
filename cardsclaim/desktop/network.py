"""Optional campus-network worker; one login client across all app instances."""
import ctypes
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import tempfile
import threading

from .network_core import Reconnector, Result

DEFAULT = {'student_id': '', 'password': '', 'auto_login': False, 'check_interval': 30}


def validate(cfg):
    if not isinstance(cfg, dict):
        raise ValueError('校园网配置格式无效')
    cfg = {key: cfg.get(key, value) for key, value in DEFAULT.items()}
    if not all(isinstance(cfg[key], str) for key in ('student_id', 'password')):
        raise ValueError('校园网账号和密码必须为文本')
    if type(cfg['auto_login']) is not bool:
        raise ValueError('校园网开关无效')
    if type(cfg['check_interval']) is not int or not 5 <= cfg['check_interval'] <= 300:
        raise ValueError('检测间隔请填写 5 到 300 秒的整数')
    if cfg['auto_login'] and (not cfg['student_id'].strip() or not cfg['password']):
        raise ValueError('请先在设置中填写校园网账号和密码')
    return cfg


class NetworkConfig:
    def __init__(self, folder):
        self.path = Path(folder) / 'network.json'

    def load(self):
        if not self.path.exists():
            return DEFAULT.copy()
        return validate(json.loads(self.path.read_text(encoding='utf-8-sig')))

    def save(self, cfg):
        data = json.dumps(validate(cfg), ensure_ascii=False, indent=2).encode('utf-8')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, filename = tempfile.mkstemp(dir=self.path.parent, suffix='.tmp')
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(filename, self.path)
        finally:
            if os.path.exists(filename):
                os.unlink(filename)


class NetworkLease:
    """Same named handle as the standalone repair app; no process is killed."""
    def __init__(self):
        self.handle = None

    def acquire(self):
        if self.handle:
            return True
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        self.kernel.CreateMutexW.restype = ctypes.c_void_p
        self.kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = self.kernel.CreateMutexW(None, False, 'Local\\GDUFE-CampusLogin-Repair')
        error = ctypes.get_last_error()
        if not handle:
            return False
        if error == 183:
            self.kernel.CloseHandle(handle)
            return False
        self.handle = handle
        return True

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class NetworkMonitor:
    def __init__(self, folder, callback, *, engine_factory=Reconnector, lease_factory=NetworkLease):
        self.settings = NetworkConfig(folder)
        self.callback = callback
        self.engine_factory = engine_factory
        self.lease = lease_factory()
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.stopped = threading.Event()
        self.thread = None
        self.engine = None
        self.revision = 0
        self.manual = False
        self.offline = False
        self.error = False
        try:
            self.config = self.settings.load()
        except (ValueError, OSError):
            self.config = DEFAULT.copy()
            self.error = True
        self.log = logging.getLogger('cardsclaim.network')
        self.handler = None
        self.folder = Path(folder)

    def start(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        self.handler = RotatingFileHandler(self.folder / 'network.log', maxBytes=256000, backupCount=2, encoding='utf-8')
        self.handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
        self.log.addHandler(self.handler)
        self.log.setLevel(logging.INFO)
        self.log.propagate = False
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def snapshot(self):
        with self.lock:
            return self.config.copy()

    def configure(self, cfg):
        cfg = validate(cfg)
        with self.lock:
            self.settings.save(cfg)
            self.config = cfg.copy()
            self.error = False
            self.revision += 1
            if self.engine:
                self.engine.stop.set()
        self.wake.set()

    def retry(self):
        with self.lock:
            self.manual = True
        self.wake.set()

    def close(self):
        self.stopped.set()
        with self.lock:
            if self.engine:
                self.engine.stop.set()
        self.wake.set()

    def emit(self, result, revision):
        with self.lock:
            if revision != self.revision or self.stopped.is_set():
                return
            recovered = result.online and self.offline
            if result.online:
                self.offline = False
            elif result.code not in ('disabled', 'conflict', 'busy', 'cancelled'):
                self.offline = True
        self.log.info('status=%s', result.code)
        self.callback((revision, result.code, result.message, recovered))

    def run(self):
        current = -1
        try:
            while not self.stopped.is_set():
                self.wake.clear()
                with self.lock:
                    cfg, revision = self.config.copy(), self.revision
                    manual, self.manual = self.manual, False
                    if current != revision:
                        self.engine = self.engine_factory()
                        current = revision
                        self.offline = False
                    engine = self.engine
                if not cfg['auto_login']:
                    self.lease.close()
                    result = Result('disabled', '校园网配置读取失败，请重新设置' if self.error else '校园网自动登录已关闭')
                elif not self.lease.acquire():
                    result = Result('conflict', '独立登录工具正在运行，请先关闭它')
                else:
                    result = engine.tick(cfg, manual=manual)
                self.emit(result, revision)
                self.wake.wait(cfg['check_interval'])
        finally:
            self.lease.close()
            if self.handler:
                self.log.removeHandler(self.handler)
                self.handler.close()
