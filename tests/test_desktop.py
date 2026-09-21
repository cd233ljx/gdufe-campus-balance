# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CardsClaim contributors
import asyncio
import copy
from datetime import datetime
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer
from cardsclaim.api import QueryError, BALANCE_URL
from cardsclaim.desktop.login import capture_params, campus_url, login_app
from cardsclaim.desktop.model import accept, due, fail, query_all, validate, TZ
from cardsclaim.desktop.store import DesktopStore, crypt


def config():
    return {'query_time':'22:00', 'items':['electricity','tap_water'], 'liwang_basis':'cash',
            'thresholds':{'electricity':'20','tap_water':'1','liwang':None},
            'params':{k:dict(campus='test-campus',building='test-building',room='test-room',type='IEC',level='3',feeitemid=v)
                      for k,v in [('electricity','1'),('tap_water','5')]}}


def balances(e='19', w='0.9'):
    return {'electricity':{'amount':e,'unit':'度'},'tap_water':{'amount':w,'unit':'元'}}


class DesktopRules(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows menu only')
    def test_eof_exits_menu_instead_of_spinning(self):
        from cardsclaim.desktop.app import main
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, CARDSCLAIM_DATA_DIR=tmp), patch('sys.argv', ['CardsClaim', '--console']), \
                 patch('builtins.input', side_effect=EOFError), patch('builtins.print'):
                main()

    def test_config_never_supplies_someone_elses_room(self):
        c=config()
        validate(c)
        del c['params']['electricity']['room']
        with self.assertRaises(ValueError): validate(c)
        validate(c, require_params=False)

    def test_strict_threshold_disabled_and_recovery(self):
        c=config(); state={}
        accept(c,state,balances('20','1'))
        self.assertFalse(state['pending'])
        accept(c,state,balances())
        self.assertEqual(set(state['pending']),{'electricity','tap_water'})
        state['pending'].clear()
        accept(c,state,balances())
        self.assertFalse(state['pending'])
        accept(c,state,balances('30','5'))
        accept(c,state,balances())
        self.assertEqual(len(state['pending']),2)
        c['thresholds']['tap_water']=None
        accept(c,state,balances())
        self.assertNotIn('tap_water',state['pending'])

    def test_auth_dedup_and_no_zero_on_failure(self):
        state={}; c=config()
        accept(c,state,balances())
        original=copy.deepcopy(state['balances'])
        fail(state,QueryError('auth')); state['pending'].pop('auth')
        fail(state,QueryError('network')); fail(state,QueryError('auth'))
        self.assertNotIn('auth',state['pending'])
        self.assertEqual(state['balances'], original)
        accept(c,state,balances()); fail(state,QueryError('auth'))
        self.assertIn('auth',state['pending'])

    def test_daily_sleep_restart_and_timezone(self):
        c=config(); state={}
        self.assertFalse(due(c,state,datetime(2026,9,22,21,59,tzinfo=TZ)))
        self.assertTrue(due(c,state,datetime(2026,9,22,23,59,tzinfo=TZ)))
        state['last_day']='2026-09-22'
        self.assertFalse(due(c,state,datetime(2026,9,22,23,59,tzinfo=TZ)))
        self.assertFalse(due(c,state,datetime(2026,9,23,9,0,tzinfo=TZ)))
        self.assertTrue(due(c,state,datetime(2026,9,23,22,0,tzinfo=TZ)))

    def test_parameter_capture_only_successful_expected_origin(self):
        form='campus=test&building=12&room=34&type=IEC&level=3&feeitemid=1&ignore=secret'
        response={'map':{'surplusCharge':'12.5'}}
        result=capture_params(BALANCE_URL,form,response)
        self.assertEqual(result[0],'electricity')
        self.assertNotIn('ignore',result[1])
        self.assertIsNone(capture_params(BALANCE_URL.replace('https:','http:'),form,response))
        self.assertIsNone(capture_params(BALANCE_URL,form+'&room=56',response))
        with self.assertRaises(QueryError): capture_params(BALANCE_URL,form,{'map':{}})
        self.assertFalse(campus_url('https://cardsp.gdufe.edu.cn.evil.test/charge/feeitem/getThirdData','/charge/feeitem/getThirdData'))

    def test_store_atomic_and_lock(self):
        codec=lambda data, decrypt=False:data
        with tempfile.TemporaryDirectory() as tmp:
            store=DesktopStore(tmp,codec)
            store.write('state',{'hello':'你好'})
            self.assertEqual(store.read('state'),{'hello':'你好'})
            with store.lock():
                self.assertTrue(store.running())
            self.assertFalse(store.running())
            self.assertEqual(len(list(Path(tmp).glob('tmp*'))),0)

    @unittest.skipUnless(os.name=='nt','Windows DPAPI only')
    def test_windows_dpapi_roundtrip_and_invalid_data(self):
        message=b'test-value-never-a-real-credential'
        cipher=crypt(message)
        self.assertNotIn(message,cipher)
        self.assertEqual(crypt(cipher,decrypt=True),message)
        with self.assertRaises(ValueError): crypt(b'invalid',decrypt=True)


class DesktopAsync(unittest.IsolatedAsyncioTestCase):
    async def test_only_selected_projects_must_validate(self):
        campus=AsyncMock()
        campus.query.return_value={'amount':'2','unit':'元'}
        result=await query_all(campus,config(),'test-token')
        self.assertEqual(set(result),{'electricity','tap_water'})
        self.assertEqual(campus.query.await_count,2)
        campus.query.side_effect=QueryError('auth')
        with self.assertRaises(QueryError): await query_all(campus,config(),'test-token')

    async def test_local_link_permissions_expiry_and_repeat_click(self):
        started=asyncio.Event(); deadline=time.monotonic()+10
        server=None
        app=login_app('a'*43, deadline, started,lambda:f'127.0.0.1:{server.port}')
        server=TestServer(app,host='127.0.0.1')
        async with TestClient(server) as client:
            r=await client.get('/')
            self.assertEqual(r.status,200)
            self.assertEqual(r.headers['Cache-Control'],'no-store')
            self.assertFalse(started.is_set())
            self.assertEqual((await client.post('/login')).status,403)
            headers={'Origin':f'http://127.0.0.1:{server.port}','X-Login-Key':'a'*43}
            bad=dict(headers,Origin='https://evil.test')
            self.assertEqual((await client.post('/login',headers=bad)).status,403)
            self.assertFalse(started.is_set())
            self.assertEqual((await client.post('/login',headers=headers)).status,200)
            self.assertEqual((await client.post('/login',headers=headers)).status,200)
            self.assertTrue(started.is_set())
        app=login_app('a'*43,time.monotonic()-1,asyncio.Event(),lambda:f'127.0.0.1:{server.port}')
        server=TestServer(app,host='127.0.0.1')
        async with TestClient(server) as client:
            self.assertEqual((await client.get('/')).status,403)


if __name__=='__main__': unittest.main()
