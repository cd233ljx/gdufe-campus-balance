"""Keep installation/uninstallation from replacing a running application."""
import ctypes
from ctypes import wintypes

_handle = None


def hold_install_mutex():
    global _handle
    if _handle is not None:
        return
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    from .store import installation
    name = installation().get('mutexname', 'Local\\GDUFE.CampusBalance.Running')
    _handle = kernel.CreateMutexW(None, False, name)
    if not _handle:
        raise ctypes.WinError(ctypes.get_last_error())
    # Deliberately retain the handle until process exit, including background work.
