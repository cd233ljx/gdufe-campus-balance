import copy
import unittest
from unittest.mock import AsyncMock
from cardsclaim.desktop.card_overview import parse_cards, query
from cardsclaim.api import QueryError


def payload():
    return {'code': 200, 'success': True, 'data': {'retcode': '0', 'card': [{
        'db_balance': 1234, 'unsettle_amount': -100, 'elec_accamt': 500,
        'lostflag': 0, 'freezeflag': 0, 'expdate': '20271231', 'cardname': '测试校园卡',
        'cert': 'must-not-retain', 'name': 'must-not-retain', 'bankacc': 'must-not-retain'}]}}


class CardOverviewTests(unittest.TestCase):
    def test_cents_unsettled_and_field_minimization(self):
        card = parse_cards(payload())[0]
        self.assertEqual(card['balance'], '11.34')
        self.assertEqual(card['unsettled'], '-1.00')
        self.assertEqual(card['electronic'], '5.00')
        self.assertEqual(card['expiry'], '2027-12-31')
        self.assertNotIn('must-not-retain', repr(card))
        self.assertEqual(card['status'], '未挂失 · 未冻结')

    def test_missing_amount_does_not_become_zero(self):
        for value in (None, True, '1234', float('nan')):
            data = payload()
            data['data']['card'][0]['db_balance'] = value
            with self.assertRaises(QueryError): parse_cards(data)

    def test_missing_cards_and_business_auth_errors(self):
        for data in ({'code':401}, {'code':200, 'data':{'retcode':'1'}}, {'code':200, 'data':{'retcode':'0','card':None}}):
            with self.assertRaises(QueryError): parse_cards(data)
        self.assertEqual(parse_cards({'code':200, 'data':{'retcode':'0','card':[]}}), [])

    def test_multiple_cards_no_implicit_total_or_wrong_status(self):
        data = payload()
        second = copy.deepcopy(data['data']['card'][0])
        second.update(lostflag=1, freezeflag=1, expdate='unknown')
        data['data']['card'].append(second)
        cards = parse_cards(data)
        self.assertEqual(len(cards), 2)
        self.assertEqual(cards[1]['status'], '已挂失 · 已冻结')
        self.assertEqual(cards[1]['expiry'], '日期格式待确认')
