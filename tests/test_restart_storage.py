# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CardsClaim contributors
import json, os, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from cardsclaim.desktop.store import DesktopStore
from test_desktop import config

class RestartStorageTests(unittest.TestCase):
    def test_both_launch_environments_use_same_profile_and_migrate(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp)/'user'; local=home/'AppData'/'Local'
            legacy=local/'Packages'/'OpenAI.Codex_test'/'LocalCache'/'Local'/'CardsClaim'
            old=DesktopStore(legacy,lambda data,decrypt=False:data)
            old.write('account',{'token':'synthetic-token','config':config()})
            old.write('state',{'status':'ok'})
            with patch.dict(os.environ,USERPROFILE=str(home),LOCALAPPDATA=str(local),CARDSCLAIM_DATA_DIR=''):
                first=DesktopStore(codec=lambda data,decrypt=False:data)
                self.assertEqual(first.root,home/'.cardsclaim')
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
            with patch.dict(os.environ,USERPROFILE=str(home),LOCALAPPDATA=str(local),CARDSCLAIM_DATA_DIR=''):
                first=DesktopStore(codec=lambda data,decrypt=False:data)
                self.assertEqual(first.read('login-draft')['token'],'synthetic-draft')
                self.assertEqual(DesktopStore(codec=lambda data,decrypt=False:data).read('login-draft'),first.read('login-draft'))

if __name__=='__main__': unittest.main()
