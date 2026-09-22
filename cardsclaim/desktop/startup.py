# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CardsClaim contributors
"""Opt-in startup for the current Windows user; no administrator rights."""
from pathlib import Path
import subprocess
import sys
import winreg

RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
VALUE_NAME = 'GDUFE Campus Balance'


def startup_command():
    if getattr(sys, 'frozen', False):
        args = [str(Path(sys.executable).resolve())]
    else:
        args = [str(Path(sys.executable).resolve().with_name('pythonw.exe')),
                str(Path(__file__).resolve().parents[2] / 'windows' / 'entry.py')]
    args.append('--startup')
    command = ('"' + args[0] + '" --startup') if getattr(sys, 'frozen', False) else subprocess.list2cmdline(args)
    if len(command) > 260:
        raise ValueError('程序路径过长，请将程序移到较短的路径后再开启开机自启。')
    return command


class StartupRegistration:
    def __init__(self, key=RUN_KEY, name=VALUE_NAME):
        self.key, self.name = key, name

    def enabled(self):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.key, 0, winreg.KEY_READ) as key:
                value, kind = winreg.QueryValueEx(key, self.name)
            return kind == winreg.REG_SZ and value == startup_command()
        except FileNotFoundError:
            return False

    def set_enabled(self, enabled):
        if enabled:
            command = startup_command()
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, self.key, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, self.name, 0, winreg.REG_SZ, command)
        else:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.key, 0, winreg.KEY_SET_VALUE) as key:
                    winreg.DeleteValue(key, self.name)
            except FileNotFoundError:
                pass
