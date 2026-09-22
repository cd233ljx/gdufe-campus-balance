"""Installed storage is independent of program location and legacy data."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from cardsclaim.desktop.store import DesktopStore, default_data_dir, installation


class InstallationTests(unittest.TestCase):
    def test_installed_marker_and_portable_data_are_distinct(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            exe = base / '应用/app.exe'
            exe.parent.mkdir()
            target = base / '用户 数据/data'
            marker = exe.parent / 'installation.ini'
            with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', str(exe)):
                self.assertEqual(default_data_dir(), exe.parent / 'data')
                marker.write_text('[install]\nDataDir=' + str(target), encoding='utf-16')
                self.assertEqual(default_data_dir(), target)
                marker.write_text('[install]\nDataDir=relative/path', encoding='utf-16')
                with self.assertRaises(ValueError):
                    installation()

    def test_installed_storage_never_imports_legacy_data(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'user/data'
            with patch('cardsclaim.desktop.store.installation', return_value={'datadir': str(target)}), patch.dict(os.environ, CARDSCLAIM_DATA_DIR=''), patch.object(DesktopStore, '_migrate_legacy') as migrate:
                first = DesktopStore(codec=lambda value, decrypt=False: value)
                first.write('preferences', {'synthetic': True})
                second = DesktopStore(codec=lambda value, decrypt=False: value)
                self.assertEqual(second.read('preferences'), {'synthetic': True})
                with first.lock('window.lock'):
                    with self.assertRaises(ValueError):
                        with second.lock('window.lock'):
                            pass
                migrate.assert_not_called()
