"""Update progress uses synthetic providers and in-memory dispatch, with no sockets."""
import copy
import json
import io
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from app import Workbench, make_handler
from qsa.free_provider import fetch_free, fetch_sina
from tests.test_stock_history import fixture


class SyncProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.workbench = Workbench(Path(self.temp.name))
        self.data, _ = fixture()
        self.data['meta'].update(history_target_count=2, history_success_count=1)
        self.workbench.store.save(copy.deepcopy(self.data))

    def tearDown(self):
        if hasattr(self.workbench, 'sync_worker'):
            self.workbench.sync_worker.join(5)
        self.workbench.histories.close()
        self.workbench.reviews.close()
        self.temp.cleanup()

    def wait(self):
        self.workbench.sync_worker.join(5)
        self.assertFalse(self.workbench.sync_worker.is_alive())

    def test_inflight_counts_duplicate_guard_and_success_after_save(self):
        reached, release = threading.Event(), threading.Event()

        def provider(options, root, progress, *, detailed_progress):
            detailed_progress(70, '已处理历史 · 合成股票',
                              {'stage': 'history', 'completed': 1, 'total': 2,
                               'succeeded': 1, 'failed': 0, 'unit': '只'})
            reached.set()
            if not release.wait(5):
                raise TimeoutError('test timed out')
            return copy.deepcopy(self.data)

        with patch('qsa.free_provider.fetch_free', side_effect=provider):
            self.workbench.sync({})
            try:
                self.assertTrue(reached.wait(3))
                handler_type = make_handler(self.workbench)
                handler = handler_type.__new__(handler_type)
                handler.path = '/api/status'
                handler.headers = {'Host': '127.0.0.1:8766'}
                responses = []
                handler.send = lambda status, payload: responses.append((status, copy.deepcopy(payload)))
                handler.do_GET()
                status, payload = responses[-1]
                self.assertEqual(status, 200)
                job = payload['job']
                self.assertEqual((job['state'], job['stage'], job['progress']), ('running', 'history', 70))
                self.assertEqual(job['detail']['completed'], 1)
                self.assertTrue(job['started_at'])
                self.assertIsNone(job['ended_at'])
                with self.assertRaisesRegex(ValueError, '正在运行'):
                    self.workbench.sync({})
            finally:
                release.set()
            self.wait()
        job = self.workbench.job
        self.assertEqual((job['state'], job['progress']), ('done', 100))
        self.assertTrue(job['ended_at'])
        self.assertEqual(job['detail'], {'history_total': 2, 'history_success': 1})
        self.assertTrue((self.workbench.store.root / 'snapshots' / job['snapshot']).exists())

    def test_failed_save_keeps_progress_below_100_and_previous_snapshot(self):
        before = (self.workbench.store.root / 'active.json').read_bytes()

        def fail_save(dataset):
            self.assertEqual(self.workbench.job['progress'], 99)
            self.assertEqual(self.workbench.job['state'], 'running')
            raise OSError('synthetic disk failure')

        with patch('qsa.free_provider.fetch_free', return_value=copy.deepcopy(self.data)), \
                patch.object(self.workbench.store, 'save', side_effect=fail_save):
            self.workbench.sync({})
            self.wait()
        self.assertEqual(self.workbench.job['state'], 'error')
        self.assertEqual(self.workbench.job['progress'], 99)
        self.assertIn('synthetic disk failure', self.workbench.job['message'])
        self.assertTrue(self.workbench.job['ended_at'])
        self.assertEqual((self.workbench.store.root / 'active.json').read_bytes(), before)

    def test_bad_dataset_never_finishes_or_replaces_active(self):
        before = copy.deepcopy(self.workbench.store.active)
        with patch('qsa.free_provider.fetch_free', return_value={'schema_version': 0}):
            self.workbench.sync({})
            self.wait()
        self.assertEqual(self.workbench.job['state'], 'error')
        self.assertLess(self.workbench.job['progress'], 100)
        self.assertEqual(self.workbench.store.active, before)

    def test_review_refresh_has_a_fresh_timed_job_and_the_same_progress_protocol(self):
        self.workbench.job.update(id='old', state='done', ended_at='old timestamp', detail={'completed': 100})

        def provider(options, root, progress, *, detailed_progress):
            self.assertNotEqual(self.workbench.job['id'], 'old')
            self.assertIsNone(self.workbench.job['ended_at'])
            self.assertEqual(self.workbench.job['detail'], {})
            detailed_progress(50, '历史处理中', {'stage': 'history', 'completed': 0, 'total': 2})
            return copy.deepcopy(self.data)

        with patch('qsa.free_provider.fetch_free', side_effect=provider):
            self.workbench.review_refresh(self.data['meta']['trade_date'])
        self.assertEqual(self.workbench.job['state'], 'done')
        self.assertEqual(self.workbench.job['progress'], 100)
        self.assertTrue(self.workbench.job['started_at'])

    def test_fallback_events_identify_the_new_round_and_legacy_callbacks_still_work(self):
        def eastmoney(options, root, report):
            report(60, '历史处理中', {'stage': 'history', 'completed': 1, 'total': 2})
            raise ValueError('synthetic source failure')

        def sina(options, root, report, reason):
            report(13, '核验腾讯报价', {'stage': 'quotes'})
            return copy.deepcopy(self.data)

        events, legacy = [], []
        with patch('qsa.free_provider.fetch_eastmoney', side_effect=eastmoney), \
                patch('qsa.free_provider.fetch_sina', side_effect=sina):
            fetch_free({}, self.workbench.store.root, lambda *_: None,
                       detailed_progress=lambda *args: events.append(args))
            fetch_free({}, self.workbench.store.root, lambda percent, message: legacy.append((percent, message)))
        self.assertTrue(any(e[2]['stage'] == 'fallback' for e in events))
        self.assertEqual(events[-1][0], 13)
        self.assertEqual(events[-1][2]['source'], 'sina')
        self.assertEqual(events[-1][2]['attempt'], 2)
        self.assertTrue(legacy)

    def test_sync_post_returns_the_accepted_job_without_an_extra_status_request(self):
        handler_type = make_handler(self.workbench)
        handler = handler_type.__new__(handler_type)
        raw = json.dumps({}).encode()
        handler.path = '/api/sync'
        handler.headers = {'Host': '127.0.0.1:8766', 'Content-Length': str(len(raw)),
                           'X-Workbench-Token': self.workbench.csrf}
        handler.rfile = io.BytesIO(raw)
        responses = []
        handler.send = lambda *args: responses.append(args)
        with patch.object(self.workbench, 'sync', return_value={'id': 'new', 'state': 'running', 'progress': 0}):
            handler.do_POST()
        self.assertEqual(responses[-1][0], 202)
        self.assertEqual(responses[-1][1]['job']['id'], 'new')

    def test_provider_counts_only_finished_histories_and_separates_failures(self):
        _, bars = fixture()
        day = self.data['meta']['trade_date']
        clock = [''] * 31
        clock[30] = day.replace('-', '') + '150000'
        rows = [{'symbol': 'sh600838', 'name': '合成甲'}, {'symbol': 'sh600001', 'name': '合成乙'}]
        quotes = {r['symbol'][2:] + '.SH': {'name': r['name'], 'bar': copy.deepcopy(bars[-1])} for r in rows}
        events = []

        def history(code, *args):
            before = events[-1][2]
            self.assertEqual(before['completed'], len([e for e in events if e[1].startswith('已处理历史')]))
            if code == '600001.SH':
                raise ValueError('synthetic history unavailable')
            return copy.deepcopy(bars), 'synthetic', []

        with patch('qsa.free_provider.get_text', return_value='v_sh000001="' + '~'.join(clock) + '";'), \
                patch('qsa.free_provider.sina_members', return_value=rows), \
                patch('qsa.free_provider.sina_boards', return_value=[{'id': 'board', 'name': '合成行业'}]), \
                patch('qsa.free_provider.tencent_quotes', return_value=quotes), \
                patch('qsa.free_provider.sina_dated_reference', return_value={}), \
                patch('qsa.free_provider.get_limit_pool', return_value={}), \
                patch('qsa.source_adapters.history_chain', side_effect=history), \
                patch('qsa.free_provider.time.sleep'):
            data = fetch_sina({'date': day, 'industry_limit': 1, 'theme_limit': 0}, self.workbench.store.root,
                              lambda *args: events.append(args), 'synthetic test')
        completed = [e[2] for e in events if e[1].startswith('已处理历史')]
        self.assertEqual([d['completed'] for d in completed], [1, 2])
        self.assertEqual(completed[-1]['succeeded'], 1)
        self.assertEqual(completed[-1]['failed'], 1)
        self.assertEqual(data['meta']['history_success_count'], 1)


if __name__ == '__main__':
    unittest.main()
