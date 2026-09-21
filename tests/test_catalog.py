# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CardsClaim contributors
import unittest
from unittest.mock import AsyncMock
from cardsclaim.api import QueryError
from cardsclaim.desktop.catalog import Catalog, parse_binding, parse_choices, selection_params


def detail(scene, bound=1, item=1):
    return {'code': 200, 'sceneinfo': scene, 'feeitem': {'feeitemid': item, 'bindStatus': bound}}


class CatalogTests(unittest.TestCase):
    def test_school_binding_is_separate_from_selector_default(self):
        scene = 'campus:c2#$#校区乙;building:b3#$#三栋;room:r8#$#808'
        self.assertEqual(parse_binding(detail(scene), 'electricity'), {'campus':'c2','building':'b3','room':'r8'})
        self.assertEqual(parse_binding(detail(scene, bound=0), 'electricity'), {})
        self.assertEqual(parse_binding(detail(scene+';room:r9'), 'electricity'), {})
        self.assertEqual(parse_binding(detail('campus:c2;building:b3'), 'electricity'), {})

    def test_phone_binding_and_option_schema(self):
        self.assertEqual(parse_binding(detail('telPhone:13800000000',item=7),'liwang'), {'telPhone':'13800000000'})
        data={'code':200,'map':{'total':[{'code':'campus','level':1,'name':'校区'}],
                              'data':[{'name':'校区乙','value':'c2'}]}}
        self.assertEqual(parse_choices(data), [{'name':'校区乙','value':'c2'}])
        with self.assertRaises(QueryError):parse_choices({'code':200,'map':{'total':[]}})
        with self.assertRaises(QueryError):parse_choices({'code':401})
        with self.assertRaises(QueryError):parse_choices({'code':200,'map':{'data':[{'name':'A','value':'x'},{'name':'B','value':'x'}]}})

    def test_queries_only_build_parameters_for_complete_selection(self):
        expected={'campus':'c','building':'b','room':'r','feeitemid':'5','level':'3','type':'IEC'}
        self.assertEqual(selection_params('tap_water',expected),expected)
        with self.assertRaises(ValueError):selection_params('electricity',{'campus':'c','building':'b'})
        with self.assertRaises(ValueError):selection_params('liwang',{'telPhone':'bad'})


class CatalogAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_binding_selects_matching_codes_not_first_options(self):
        catalog=Catalog(None,'fake')
        catalog.bound=AsyncMock(return_value={'campus':'c2','building':'b2','room':'r2'})
        catalog.choices=AsyncMock(side_effect=[
            [{'name':'A','value':'c1'},{'name':'B','value':'c2'}],
            [{'name':'一栋','value':'b1'},{'name':'二栋','value':'b2'}],
            [{'name':'101','value':'r1'},{'name':'102','value':'r2'}]])
        result=await catalog.initial({'items':['electricity'],'params':{}})
        row=result['electricity']
        self.assertEqual(row['selected'],{'campus':'c2','building':'b2','room':'r2'})
        self.assertEqual(row['source'],'学校已绑定')

    async def test_unbound_or_stale_binding_never_selects_first_room(self):
        for binding in ({},{'campus':'missing','building':'b','room':'r'}):
            catalog=Catalog(None,'fake')
            catalog.bound=AsyncMock(return_value=binding)
            catalog.choices=AsyncMock(return_value=[{'name':'Only campus','value':'c1'}])
            row=(await catalog.initial({'items':['electricity'],'params':{}}))['electricity']
            self.assertEqual(row['selected'],{})
            self.assertEqual(catalog.choices.await_count,1)

    async def test_cascading_request_uses_only_required_parent_codes(self):
        catalog=Catalog(None,'fake')
        catalog.request=AsyncMock(return_value={'code':200,'map':{'data':[]}})
        await catalog.choices('electricity','room',{'campus':'c','building':'b','room':'old'})
        data=catalog.request.call_args.kwargs['data']
        self.assertEqual(data,{'feeitemid':'1','type':'select','level':'2','campus':'c','building':'b'})
