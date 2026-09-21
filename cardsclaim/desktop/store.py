# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CardsClaim contributors
"""Per-Windows-user DPAPI encrypted data and a single-process lock."""
import ctypes
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path


def crypt(data, decrypt=False):
    if os.name != 'nt':
        raise RuntimeError('桌面版需要 Windows 10/11')
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    api = ctypes.WinDLL('crypt32', use_last_error=True)
    method = api.CryptUnprotectData if decrypt else api.CryptProtectData
    method.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                       ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    method.restype = wintypes.BOOL
    if not method(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ValueError('无法读取当前 Windows 用户的加密数据')
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        kernel.LocalFree(target.data)


class DesktopStore:
    def __init__(self, root=None, codec=crypt):
        override = os.environ.get('CARDSCLAIM_DATA_DIR')
        # MSIX descendants can transparently redirect LocalAppData into Codex's
        # package cache. The user-profile root is shared with Explorer launches.
        self.root = Path(root or override or Path(os.environ['USERPROFILE']) / '.cardsclaim')
        self.root.mkdir(parents=True, exist_ok=True)
        self.codec = codec
        if root is None and not override:
            self._migrate_legacy()

    def _migrate_legacy(self):
        local = Path(os.environ['LOCALAPPDATA'])
        candidates = [local / 'CardsClaim']
        packages = local / 'Packages'
        if packages.is_dir():
            candidates.extend(packages.glob('OpenAI.Codex_*/LocalCache/Local/CardsClaim'))
        # Keep a completed migration authoritative. Never copy locks/process IDs.
        with self.lock('migration.lock'):
            if (self.root / 'account').exists() or (self.root / 'login-draft').exists():
                return
            accounts, drafts = [], []
            for folder in candidates:
                for name, records in [('account', accounts), ('login-draft', drafts)]:
                    path = folder / name
                    try:
                        data = json.loads(self.codec(path.read_bytes(), decrypt=True))
                        if not isinstance(data, dict) or not data.get('token'):
                            continue
                        from .model import validate
                        validate(data.get('config', {}), require_params=name == 'account')
                        records.append((path.stat().st_mtime_ns, folder, data))
                    except (OSError, ValueError, TypeError, KeyError):
                        continue
            latest = max(accounts, key=lambda entry: entry[0], default=None)
            if latest:
                self.write('account', latest[2])
                try:
                    state = json.loads(self.codec((latest[1] / 'state').read_bytes(), decrypt=True))
                    if isinstance(state, dict):
                        self.write('state', state)
                except (OSError, ValueError, TypeError):
                    pass
            draft = max(drafts, key=lambda entry: entry[0], default=None)
            if draft and (latest is None or draft[0] > latest[0]):
                self.write('login-draft', draft[2])

    def read(self, name, default=None):
        path = self.root / name
        if not path.exists():
            return default
        return json.loads(self.codec(path.read_bytes(), decrypt=True))

    def write(self, name, value):
        data = self.codec(json.dumps(value, ensure_ascii=False, allow_nan=False).encode())
        fd, temp = tempfile.mkstemp(dir=self.root)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.root / name)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    @contextmanager
    def lock(self, name='monitor.lock'):
        with open(self.root / name, 'a+b') as stream:
            stream.seek(0, 2)
            if not stream.tell():
                stream.write(b'0')
                stream.flush()
            stream.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise ValueError('监控正在运行，请先从菜单停止监控') from None
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream, fcntl.LOCK_UN)

    def running(self):
        try:
            with self.lock():
                return False
        except ValueError:
            return True
