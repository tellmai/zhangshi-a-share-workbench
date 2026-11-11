import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from qsa.data import SnapshotStore
from qsa.providers import FreeProvider, ProviderResult
from qsa.quality import legacy_view
from qsa.service_v2 import WorkbenchV2
from v2_fixture import frames


class ResearchRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.store = SnapshotStore(self.temp.name)
        self.service = WorkbenchV2(self.store)
        self.dataset = frames(1)[0]['dataset']
        self.store.save(legacy_view(self.dataset))

    def tearDown(self):
        self.temp.cleanup()

    def test_history_revisions_invalidate_scan_dedupe_and_preserve_old_report(self):
        service = self.service
        first = service.enqueue('scan', spawn=False)
        done = service.execute(first['id'])
        self.assertEqual(done['state'], 'completed', done.get('error'))
        original = service.repo.read_object(done['result']['object_id'])
        self.assertEqual(service.enqueue('scan', spawn=False)['id'], first['id'])
        stock = self.dataset['stocks'][0]
        request = {'code': stock['code'], 'start': stock['bars'][-4]['date'],
                   'end': self.dataset['meta']['trade_date'], 'warmup': 0}
        with patch.object(FreeProvider, '_range', return_value=(stock['bars'], {'synthetic': True})):
            result = FreeProvider(service.repo).get_history(request)
        self.assertEqual(result.status, 'supported', result.metadata)
        second = service.enqueue('scan', spawn=False)
        self.assertNotEqual(second['id'], first['id'])
        self.assertEqual(service.enqueue('scan', spawn=False)['id'], second['id'])
        self.assertEqual(service.execute(second['id'])['state'], 'completed')
        self.assertEqual(service.repo.read_object(done['result']['object_id']), original)

    def test_successful_backfill_publishes_a_separate_persisted_research_job(self):
        service = self.service
        stock = self.dataset['stocks'][0]
        job = service.enqueue('backfill', {'codes': [stock['code']], 'warmup': 0,
            'start': stock['bars'][-4]['date']}, spawn=False)
        with patch.object(FreeProvider, '_range', return_value=(stock['bars'], {'synthetic': True})):
            done = service.execute(job['id'])
        self.assertEqual(done['state'], 'completed', done.get('error'))
        self.assertEqual(done['result']['success'], 1)
        research = service.jobs.get(done['result']['research_job_id'])
        self.assertEqual(research['kind'], 'scan')
        self.assertEqual(research['state'], 'completed', research.get('error'))
        self.assertIsNotNone(service.repo.get('reports', research['id']))

    def test_position_and_watch_queues_preserve_neutral_cursor_and_request_reasons(self):
        service = self.service
        day = self.dataset['meta']['trade_date']
        service.ledger.append_trade({'code': '600005.SH', 'side': 'buy', 'qty': 100,
            'price': 10, 'fees': 5, 'executed_at': day+'T10:00:00+08:00'})
        service.repo.put('watchlists', 'real', {'mode': 'real', 'codes': ['600004.SH']})
        job = service.enqueue('backfill', {'batch_size': 2}, spawn=False)
        self.assertEqual(job['payload']['batch_codes'], ['600005.SH', '600004.SH', '600001.SH', '600002.SH'])
        requests = []
        def missing(request):
            requests.append((request.code, request.reason))
            return ProviderResult('unavailable', reason='synthetic missing')
        with patch.object(FreeProvider, 'get_history', side_effect=missing):
            done = service.execute(job['id'])
        self.assertEqual(done['state'], 'completed', done.get('error'))
        self.assertEqual(requests, [('600005.SH', 'position'), ('600004.SH', 'watch'),
            ('600001.SH', 'seed'), ('600002.SH', 'seed')])
        self.assertNotIn('research_job_id', done['result'])
        self.assertEqual(service.repo.get('queue_cursors', 'neutral')['cursor'], 2)
        repair = service.enqueue('backfill', {'codes': ['600003.SH']}, spawn=False)
        self.assertEqual(repair['payload']['batch_codes'], ['600003.SH'])
        self.assertEqual(repair['payload']['code_reasons'], {'600003.SH': 'repair'})

    def test_latest_history_receipt_wins_over_a_longer_older_series(self):
        service = self.service
        stock = self.dataset['stocks'][0]
        def cache(key, bars, known):
            service.repo.put('history_cache', key, {'code': stock['code'],
                'end': self.dataset['meta']['trade_date'], 'data_id': service.repo.object(bars),
                'result': {'status': 'supported', 'provider_id': key, 'source_family': 'synthetic',
                    'available_at': known}})
        cache('long-old', stock['bars'], '2026-11-10T10:00:00Z')
        newer = copy.deepcopy(stock['bars'][-60:])
        newer[-2]['volume_shares'] += 100
        cache('short-new', newer, '2026-11-11T10:00:00Z')
        result = service.enriched(self.dataset)['stocks'][0]['bars']
        self.assertEqual(len(result), 60)
        self.assertEqual(result[-2]['volume_shares'], newer[-2]['volume_shares'])
        self.assertEqual(result[-2]['provenance']['provider_id'], 'short-new')

    def test_active_demo_seed_rejects_real_history_requests(self):
        self.store.save(self.store.demo)
        with patch.object(FreeProvider, 'get_history', side_effect=AssertionError('must not fetch')):
            with self.assertRaisesRegex(ValueError, '演示快照'):
                self.service.enqueue('backfill', spawn=False)


if __name__ == '__main__':
    unittest.main()
