# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CardsClaim contributors
import json, os, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from cardsclaim.desktop.store import DesktopStore, default_data_dir
from test_desktop import config

class RestartStorageTests(unittest.TestCase):
    def test_both_launch_environments_use_same_program_directory_and_migrate(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp)/'user'; local=home/'AppData'/'Local'
            legacy=local/'Packages'/'OpenAI.Codex_test'/'LocalCache'/'Local'/'CardsClaim'
            old=DesktopStore(legacy,lambda data,decrypt=False:data)
            old.write('account',{'token':'synthetic-token','config':config()})
            old.write('state',{'status':'ok'})
            with patch.dict(os.environ,USERPROFILE=str(home),LOCALAPPDATA=str(local),CARDSCLAIM_DATA_DIR=''), patch('cardsclaim.desktop.store.default_data_dir', return_value=home/'app'/'data'):
                first=DesktopStore(codec=lambda data,decrypt=False:data)
                self.assertEqual(first.root,home/'app'/'data')
                self.assertEqual(first.read('account')['token'],'synthetic-token')
                self.assertEqual(first.read('state')['status'],'ok')
                with patch.dict(os.environ,LOCALAPPDATA=str(home/'different-view')):
                    second=DesktopStore(codec=lambda data,decrypt=False:data)
                    self.assertEqual(first.root,second.root)
                    self.assertEqual(second.read('account'),first.read('account'))
                    with first.lock('window.lock'):
                        with self.assertRaises(ValueError):
                            with second.lock('window.lock'): pass
                old.write('account',{'token':'newer-legacy-must-not-overwrite','config':config()})
                self.assertEqual(DesktopStore(codec=lambda data,decrypt=False:data).read('account')['token'],'synthetic-token')

    def test_draft_only_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp); local=home/'local'
            old=DesktopStore(local/'CardsClaim',lambda data,decrypt=False:data)
            old.write('login-draft',{'token':'synthetic-draft','config':config()})
            with patch.dict(os.environ,USERPROFILE=str(home),LOCALAPPDATA=str(local),CARDSCLAIM_DATA_DIR=''), patch('cardsclaim.desktop.store.default_data_dir', return_value=home/'app'/'data'):
                first=DesktopStore(codec=lambda data,decrypt=False:data)
                self.assertEqual(first.read('login-draft')['token'],'synthetic-draft')
                self.assertEqual(DesktopStore(codec=lambda data,decrypt=False:data).read('login-draft'),first.read('login-draft'))

    def test_program_path_is_independent_of_working_directory(self):
        import sys
        expected=Path(__file__).resolve().parents[1]/'data'
        with patch.object(sys,'frozen',False,create=True):
            self.assertEqual(default_data_dir(),expected)
        with tempfile.TemporaryDirectory() as tmp:
            exe=Path(tmp)/'app'/'CardsClaim.exe'
            with patch.object(sys,'frozen',True,create=True), patch.object(sys,'executable',str(exe)):
                self.assertEqual(default_data_dir(),exe.parent/'data')

    def test_profile_account_and_state_migrate_without_overwriting_local(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp)
            old=DesktopStore(home/'.cardsclaim',lambda data,decrypt=False:data)
            old.write('account',{'token':'profile-token','config':config()})
            old.write('state',{'status':'ok','active':{'electricity':True}})
            with patch.dict(os.environ,USERPROFILE=str(home),LOCALAPPDATA=str(home/'local'),CARDSCLAIM_DATA_DIR=''), patch('cardsclaim.desktop.store.default_data_dir',return_value=home/'app'/'data'):
                new=DesktopStore(codec=lambda data,decrypt=False:data)
                self.assertEqual(new.read('account'),old.read('account'))
                self.assertEqual(new.read('state'),old.read('state'))
                new.write('account',{'token':'local-token','config':config()})
                self.assertEqual(DesktopStore(codec=lambda data,decrypt=False:data).read('account')['token'],'local-token')
                self.assertTrue((home/'.cardsclaim'/'account').exists())

if __name__=='__main__': unittest.main()
