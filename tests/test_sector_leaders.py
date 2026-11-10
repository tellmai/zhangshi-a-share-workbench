import copy
import math
import unittest

from qsa.analytics import analyze, daily_sector_leaders
from qsa.data import make_demo, validate_dataset


class SectorLeaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.demo = make_demo()

    def inputs(self):
        data = copy.deepcopy(self.demo)
        day = data['meta']['trade_date']
        sectors = [dict(id='slow', name='高分但涨幅较低', type='industry', score=99, avg_pct=2.,
            breadth=95., amount_yuan=2e9, complete=True, asof_date=day, classification='合成同分类', members=[]),
            dict(id='fast', name='当日领涨', type='industry', score=65, avg_pct=6.,
            breadth=85., amount_yuan=1e9, complete=True, asof_date=day, classification='合成同分类', members=[]),
            dict(id='down', name='下跌方向', type='industry', score=40, avg_pct=-1.,
            breadth=20., amount_yuan=1e9, complete=True, asof_date=day, classification='合成同分类', members=[])]
        return data, sectors

    def test_daily_return_takes_priority_over_custom_score_and_negative_filler(self):
        data, sectors = self.inputs()
        before = copy.deepcopy(sectors)
        result = daily_sector_leaders(data, sectors, 'industry')
        self.assertEqual([s['id'] for s in result['rows']], ['fast', 'slow'])
        self.assertEqual(result['positive_count'], 2)
        self.assertEqual(result['available_count'], 3)
        self.assertEqual(sectors, before)
        self.assertFalse(result['complete_universe'])

    def test_missing_limit_metrics_do_not_hide_reliable_daily_price_leader(self):
        data, sectors = self.inputs()
        sectors[1].update(score=None, limit_up=None)
        result = daily_sector_leaders(data, sectors, 'industry')
        self.assertEqual(result['rows'][0]['id'], 'fast')
        self.assertIsNone(result['rows'][0]['score'])

    def test_stale_incomplete_and_nonfinite_sectors_cannot_enter_daily_list(self):
        data, sectors = self.inputs()
        sectors[1]['complete'] = False
        sectors[0]['asof_date'] = '2020-01-01'
        sectors[2]['avg_pct'] = math.nan
        self.assertEqual(daily_sector_leaders(data, sectors, 'industry')['rows'], [])

    def add_full_list(self, data):
        day = data['meta']['trade_date']
        data['meta']['provider_sector_coverage'] = {'industry':dict(asof_date=day, source='合成板块指数', expected_count=3, complete=True)}
        data['provider_sector_ranking'] = [dict(id=code, name=name, type='industry', pct_chg=pct,
            asof_date=day, source='合成板块指数') for code, name, pct in
            [('slow','已展开方向',2.), ('unexpanded','全榜领涨但未展开',9.), ('down','下跌方向',-3.)]]

    def test_complete_dated_full_list_can_show_an_unexpanded_leader(self):
        data, sectors = self.inputs()
        self.add_full_list(data)
        result = daily_sector_leaders(data, sectors, 'industry')
        self.assertTrue(result['complete_universe'])
        self.assertEqual(result['basis'], '供应商板块指数涨幅')
        self.assertEqual(result['rows'][0]['id'], 'unexpanded')
        self.assertFalse(result['rows'][0]['expanded'])
        self.assertEqual(result['rows'][0]['members'], [])
        self.assertEqual([r['daily_pct'] for r in result['rows']], [9., 2.])
        self.assertNotIn('fast', [r['id'] for r in result['rows']])

    def test_partial_undated_stale_duplicate_or_mixed_source_list_falls_back(self):
        for case in ('partial','undated','stale','duplicate','source','nonfinite'):
            data, sectors = self.inputs()
            self.add_full_list(data)
            records = data['provider_sector_ranking']
            if case=='partial': records.pop()
            if case=='undated': records[0].pop('asof_date')
            if case=='stale': records[0]['asof_date']='2020-01-01'
            if case=='duplicate': records[0]['id']=records[1]['id']
            if case=='source': records[0]['source']='不同口径'
            if case=='nonfinite': records[0]['pct_chg']=math.inf
            result = daily_sector_leaders(data, sectors, 'industry')
            self.assertFalse(result['complete_universe'], case)
            self.assertEqual(result['rows'][0]['id'], 'fast', case)

    def test_industries_and_themes_never_share_a_provider_ranking(self):
        data, sectors = self.inputs()
        self.add_full_list(data)
        result = daily_sector_leaders(data, [], 'theme')
        self.assertFalse(result['complete_universe'])
        self.assertEqual(result['rows'], [])

    def test_sector_leaders_are_independent_of_stock_style_and_screen_thresholds(self):
        baseline = analyze(self.demo)['sector_leaders']
        for style in ('balanced','relay','trend'):
            result = analyze(self.demo, dict(style=style, min_score=100, min_amount=1e12))
            self.assertEqual(result['sector_leaders'], baseline)
        self.assertTrue(all(s['daily_pct']>0 for s in baseline['rows']))

    def test_invalid_provider_metadata_is_rejected_before_analysis(self):
        for malformed in (None, [], {'industry':None}, {'industry':dict(complete=True, expected_count=True)}):
            data=copy.deepcopy(self.demo)
            data['meta']['provider_sector_coverage']=malformed
            with self.assertRaises(ValueError): validate_dataset(data)
        data=copy.deepcopy(self.demo)
        self.add_full_list(data)
        validate_dataset(data)
        data['provider_sector_ranking'][0]['pct_chg']=math.nan
        with self.assertRaises(ValueError): validate_dataset(data)

    def test_legacy_undated_list_loads_but_cannot_become_a_current_full_list(self):
        data, sectors=self.inputs()
        self.add_full_list(data)
        data['meta'].pop('provider_sector_coverage')
        for row in data['provider_sector_ranking']: row.pop('asof_date')
        validate_dataset(data)
        self.assertFalse(daily_sector_leaders(data,sectors,'industry')['complete_universe'])


if __name__=='__main__':unittest.main()
