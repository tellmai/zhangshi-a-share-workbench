"""Synthetic adapters and in-memory HTTP; no external network in unit tests."""
import copy
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import Workbench,make_handler
from qsa.data import SnapshotStore
from qsa.free_provider import fetch_free
from qsa.source_config import DEFAULTS,read_settings,save_settings
from qsa.source_adapters import check_bars,history_chain,library_history,run_worker
from qsa.source_service import SourceService
from tests.test_stock_history import fixture


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.root=Path(self.temp.name)
        self.data,self.bars=fixture();self.day=self.data['meta']['trade_date'];self.code='600838.SH'
        self.store=SnapshotStore(self.root);self.store.save(self.data)

    def tearDown(self):self.temp.cleanup()

    def test_settings_persist_without_accepting_secrets_or_arbitrary_endpoints(self):
        before=copy.deepcopy(DEFAULTS)
        saved=save_settings(self.root,dict(market_provider='sina',history_order=['baostock','tencent']))
        self.assertEqual(read_settings(self.root),saved);self.assertEqual(DEFAULTS,before)
        for bad in ({'password':'x'},{'cookie':'x'},{'endpoint':'https://x'}, {'history_order':[]}, {'history_order':['tencent','tencent']},{'history_order':['unknown']},{'library_timeout':True}):
            with self.assertRaises(ValueError):save_settings(self.root,bad)

    def test_market_preference_and_disabled_fallback_are_respected(self):
        save_settings(self.root,{'market_provider':'sina','market_fallback':False})
        with patch('qsa.free_provider.fetch_sina',return_value=copy.deepcopy(self.data)) as sina,patch('qsa.free_provider.fetch_eastmoney') as em:
            result=fetch_free({},self.root,lambda *_:None)
            sina.assert_called_once();em.assert_not_called();self.assertEqual(result['meta']['market_route'],'sina')
        with patch('qsa.free_provider.fetch_sina',side_effect=ValueError('synthetic')),patch('qsa.free_provider.fetch_eastmoney') as em:
            with self.assertRaises(ValueError):fetch_free({},self.root,lambda *_:None)
            em.assert_not_called()

    def test_failed_market_route_falls_back_and_retains_reason(self):
        with patch('qsa.free_provider.fetch_eastmoney',side_effect=ValueError('synthetic failure')),patch('qsa.free_provider.fetch_sina',return_value=copy.deepcopy(self.data)):
            r=fetch_free({},self.root,lambda *_:None)
            self.assertEqual(r['meta']['market_route'],'sina')
            self.assertEqual(r['meta']['provider_attempts'][0]['status'],'error')

    def test_history_fallback_rejects_conflict_and_preserves_current_quote(self):
        save_settings(self.root,{'history_order':['tencent','baostock']})
        bad=copy.deepcopy(self.bars);bad[-1]['high']+=1
        before=copy.deepcopy(self.data)
        evidence=self.root/'evidence';evidence.mkdir()
        with patch('qsa.free_provider.history_tencent',return_value=bad),patch('qsa.source_adapters.library_history',return_value=self.bars):
            bars,name,failures=history_chain(self.code,self.day,self.root,evidence,
                lambda bars,_:check_bars(self.code,self.day,bars,self.data['stocks'][0]['bars'][-1]))
            self.assertEqual(name,'BaoStock历史日线');self.assertEqual(len(failures),1)
            self.assertEqual(bars,self.bars);self.assertEqual(self.data,before)

    def test_library_cache_is_separate_by_source_and_avoids_duplicate_calls(self):
        with patch('qsa.source_adapters.run_worker',return_value={'bars':self.bars}) as worker:
            for _ in range(2):library_history('baostock',self.code,self.day,self.root/'history')
            self.assertEqual(worker.call_count,1)
            library_history('akshare',self.code,self.day,self.root/'history')
            self.assertEqual(worker.call_count,2)
            self.assertTrue((self.root/'history'/'baostock'/f'{self.code}-{self.day}.json').exists())

    def test_stale_missing_factor_and_bad_ohlc_history_are_rejected(self):
        for case in ('stale','factor','ohlc','nan'):
            bars=copy.deepcopy(self.bars)
            if case=='stale':bars.pop()
            if case=='factor':bars[-10]['adj_factor']=None
            if case=='ohlc':bars[-1]['high']=.01
            if case=='nan':bars[-1]['close']=float('nan')
            with self.assertRaises(ValueError):check_bars(self.code,self.day,bars)

    def test_worker_timeout_is_bounded_without_exposing_process_input(self):
        with patch('qsa.source_adapters.subprocess.run',side_effect=subprocess.TimeoutExpired('worker',30)):
            with self.assertRaisesRegex(ValueError,'超过30秒'):run_worker({'provider':'baostock'},30)

    def test_diagnostics_leave_snapshot_unchanged_and_save_actual_failure(self):
        service=SourceService(self.store);before=(self.root/'active.json').read_bytes()
        with patch('qsa.source_service.run_worker',side_effect=ValueError('synthetic timeout')):
            service.start({'provider':'tencent','date':self.day});service.worker.join()
        self.assertEqual((self.root/'active.json').read_bytes(),before)
        self.assertEqual(service.status()['health']['tencent']['state'],'error')

    def test_ifind_login_status_is_not_data_ready_and_credentials_are_not_saved(self):
        service=SourceService(self.store)
        with patch('qsa.source_service.installed',return_value=True),patch('qsa.source_service.run_worker',return_value={'message':'login verified only','scope':'identity only'}):
            service.start({'provider':'ifind','date':self.day,'username':'private-user-123','password':'private-secret-456'});service.worker.join()
        self.assertEqual(service.status()['health']['ifind']['state'],'login_only')
        for p in self.root.rglob('*.json'):
            text=p.read_text(encoding='utf-8');self.assertNotIn('private-user-123',text);self.assertNotIn('private-secret-456',text)

    def test_invalid_probe_does_not_launch_worker(self):
        service=SourceService(self.store)
        with patch('qsa.source_service.run_worker') as worker:
            for payload in ({'provider':'unknown'},{'code':'../../file'},{'date':'2099-01-01'},{'provider':'tencent','password':'x'}):
                with self.assertRaises(ValueError):service.start(payload)
            worker.assert_not_called()

    def test_source_routes_keep_csrf_and_dispatch_without_sockets(self):
        work=Workbench(self.root);Handler=make_handler(work);h=Handler.__new__(Handler);replies=[];h.send=lambda *x:replies.append(x)
        try:
            h.path='/api/sources';h.headers={'Host':'127.0.0.1:8766'};h.do_GET();self.assertEqual(replies[-1][0],200)
            for route in ('/api/sources/settings','/api/sources/test'):
                h.path=route;h.headers.update({'Content-Length':'2','X-Workbench-Token':'wrong'});h.rfile=io.BytesIO(b'{}');h.do_POST();self.assertEqual(replies[-1][0],403)
            for asset in ('/sources.js','/sources.css'):
                h.path=asset;h.do_GET();self.assertEqual(replies[-1][0],200)
        finally:work.histories.close();work.reviews.close()


if __name__=='__main__':unittest.main()
