# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CardsClaim contributors
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
import uuid
import winreg

from cardsclaim.desktop.startup import StartupRegistration, startup_command


class StartupTests(unittest.TestCase):
    def test_register_remove_preserves_other_entries(self):
        # Exercise the real registry API in an isolated non-startup test key.
        key = rf'Software\GDUFECampusBalanceTests\{uuid.uuid4().hex}'
        startup = StartupRegistration(key=key)
        try:
            self.assertFalse(startup.enabled())
            startup.set_enabled(True)
            self.assertTrue(startup.enabled())
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as handle:
                winreg.SetValueEx(handle, 'unrelated', 0, winreg.REG_SZ, 'keep')
            startup.set_enabled(False)
            startup.set_enabled(False)
            self.assertFalse(startup.enabled())
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
                self.assertEqual(winreg.QueryValueEx(handle, 'unrelated')[0], 'keep')
        finally:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)

    def test_frozen_path_with_spaces_is_quoted(self):
        path = str(Path('C:/A folder/校园余额/gdufe-campus-balance.exe').resolve())
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', path):
            self.assertEqual(startup_command(), subprocess.list2cmdline([path, '--startup']))

    def test_source_uses_absolute_entry_and_pythonw(self):
        with patch.object(sys, 'frozen', False, create=True):
            command = startup_command()
        self.assertIn('pythonw.exe', command)
        self.assertIn(str(Path(__file__).resolve().parents[1] / 'windows' / 'entry.py'), command)
        self.assertNotIn(' -m ', command)

    def test_frozen_command_matches_installer_even_without_spaces(self):
        path = str(Path('C:/Apps/Balance/app.exe').resolve())
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', path):
            self.assertEqual(startup_command(), f'"{path}" --startup')

    def test_permission_failure_is_not_reported_as_success(self):
        with patch('winreg.CreateKeyEx', side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                StartupRegistration().set_enabled(True)
