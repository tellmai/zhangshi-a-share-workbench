"""Short-term statistics remain descriptive and independent of stock selection."""
import copy
import tempfile
import unittest
from pathlib import Path

from app import Workbench, make_handler
from qsa.analytics import analyze, stock_row
from qsa.data import make_demo


class ShortlineTests(unittest.TestCase):
    def test_confirmed_counts_do_not_fill_unknown_limit_states(self):
        data = make_demo()
        unknown = data['stocks'][0]
        unknown['limit'] = {}
        unknown['bars'][-1].pop('up_limit', None)
        unknown['bars'][-1].pop('down_limit', None)
        rows = [stock_row(s, data['meta']['trade_date']) for s in data['stocks']]
        valid = [r for r in rows if not r['is_st'] and r['current'] and not r['exclusions'] and r['verification'].get('status') != 'conflict']
        metrics = analyze(data)['market']
        self.assertIsNone(metrics['limit_up'])
        self.assertIsNone(metrics['broken_rate'])
        self.assertEqual(metrics['limit_up_confirmed'], sum(r['limit_status'] == 'U' for r in valid))
        self.assertEqual(metrics['limit_status_known'], len(valid) - 1)
        self.assertLess(metrics['limit_status_coverage'], 100)
        strict = analyze(data, {'min_score': 100, 'min_amount': 1e12})
        self.assertFalse(strict['candidates'])
        self.assertEqual(metrics, strict['market'])

    def test_complete_counts_match_existing_totals_and_empty_scope_has_no_coverage(self):
        data = make_demo()
        metrics = analyze(data)['market']
        self.assertEqual(metrics['limit_status_coverage'], 100)
        self.assertEqual(metrics['limit_up_confirmed'], metrics['limit_up'])
        self.assertEqual(metrics['limit_down_confirmed'], metrics['limit_down'])
        self.assertEqual(metrics['broken_confirmed'], metrics['broken'])
        data = copy.deepcopy(data)
        for stock in data['stocks']:
            stock['bars'] = []
        self.assertIsNone(analyze(data)['market']['limit_status_coverage'])

    def test_shortline_assets_are_local_and_served_by_existing_handler(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            workbench = Workbench(Path(directory))
            try:
                handler_type = make_handler(workbench)
                handler = handler_type.__new__(handler_type)
                handler.headers = {'Host': '127.0.0.1:8766'}
                responses = []
                handler.send = lambda *args: responses.append(args)
                for asset in ('/shortline.js', '/shortline.css'):
                    handler.path = asset
                    handler.do_GET()
                    self.assertEqual(responses[-1][0], 200)
                    self.assertIsInstance(responses[-1][1], bytes)
                html = (Path(__file__).parents[1] / 'static' / 'index.html').read_text(encoding='utf-8')
                self.assertLess(html.index('/shortline.js'), html.index('/app.js'))
            finally:
                workbench.histories.close()
                workbench.reviews.close()


if __name__ == '__main__':
    unittest.main()
