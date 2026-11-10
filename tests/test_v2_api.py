import copy
import io
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app import Workbench, make_handler
from qsa.desktop import DesktopBridge
from qsa.providers import FreeProvider, ProviderResult, register_calendar
from qsa.quality import legacy_view
from qsa.scheduler import task_definitions, task_xml
from v2_fixture import frames


class V2APITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.work=Workbench(Path(self.temp.name))
        kind=make_handler(self.work)
        self.handler=kind.__new__(kind)
        self.responses=[]
        self.handler.send=lambda *args:self.responses.append(args)

    def tearDown(self):
        self.work.histories.close();self.work.reviews.close();self.temp.cleanup()

    def request(self,path,body=None,token=True):
        h=self.handler;h.path=path;h.headers={"Host":"127.0.0.1:8766"}
        if body is None:h.do_GET()
        else:
            raw=json.dumps(body).encode();h.rfile=io.BytesIO(raw)
            h.headers.update({"Content-Length":str(len(raw)),"X-Workbench-Token":self.work.csrf if token else 'bad'})
            h.do_POST()
        return self.responses[-1]

    def test_v2_contract_assets_and_same_native_dispatch(self):
        status,body,*_=self.request('/api/v1/health')
        self.assertEqual(status,200)
        self.assertEqual(body['data']['config']['capital'],20000)
        self.assertFalse(body['data']['features']['minute_confirmation'])
        native=DesktopBridge(self.work).request('/api/v1/health')
        self.assertEqual(native['data'],body)
        for path in ('/workbench.js','/workbench.css'):
            self.assertEqual(self.request(path)[0],200)
        self.assertEqual(self.request('/api/v1/trades',{},False)[0],403)

    def test_settings_revision_does_not_rewrite_legacy_or_trade_opening_cash(self):
        service=self.work.v2
        before=self.work.store.demo.copy()
        self.request('/api/v1/trades',{'code':'600001','side':'buy','qty':100,'price':10,'executed_at':'2026-10-23T10:00:00+08:00'})
        self.assertEqual(self.request('/api/v1/settings',{'config':{'capital':30000}})[0],200)
        self.assertEqual(service.ledger.account()['opening_cash'],20000)
        self.assertEqual(self.work.store.demo,before)
        bad=self.request('/api/v1/settings',{'config':{'capital':True}})
        self.assertEqual(bad[0],400)
        self.assertEqual(bad[1]['error']['code'],'VALIDATION_ERROR')

    def test_shared_plan_and_client_import_preserve_conflicts(self):
        path='/api/v1/plans'
        first=self.request(path,{'date':'2026-10-23','mode':'real','codes':['600001'],'note':'server'})
        self.assertEqual(first[0],200)
        stale=self.request(path,{'date':'2026-10-23','mode':'real','codes':['600002'],'note':'stale','expected_revision':0})
        self.assertEqual(stale[0],409)
        imported=self.request(path+'/import',{'watch':['real:600001.SH'],'plans':{'real:2026-10-23':{'codes':['600002.SH'],'note':'client'}}})
        self.assertEqual(imported[1]['data']['conflicts'],['real:2026-10-23'])
        self.assertEqual(self.work.v2.repo.get('plans','real:2026-10-23')['note'],'server')

    def test_cached_budget_observes_cash_changes_from_another_process(self):
        from qsa.ledger import Ledger
        from qsa.repository import Repository
        service=self.work.v2
        first=service.report('demo')
        self.assertTrue(first['candidates'])
        risk=first['candidates'][0]['budget']['risk_budget']
        Ledger(Repository(self.work.store.root)).cash_entry({'account':'demo','amount':10000,'at':first['context']['decision_time']})
        second=service.report('demo')
        self.assertGreater(second['candidates'][0]['budget']['risk_budget'],risk)
        second['candidates'].clear()
        self.assertTrue(service.report('demo')['candidates'])

    def test_future_cash_cannot_change_a_historical_budget(self):
        service=self.work.v2
        first=service.report('demo')
        service.ledger.cash_entry({'account':'demo','amount':10000})
        second=service.report('demo')
        self.assertEqual(first['candidates'],second['candidates'])

    def test_durable_demo_scan_produces_frozen_report_and_exports(self):
        service=self.work.v2
        job=service.enqueue('scan',{'mode':'demo'},spawn=False)
        done=service.execute(job['id'])
        self.assertEqual(done['state'],'completed',done.get('error'))
        report=service.repo.read_object(done['result']['object_id'])
        self.assertEqual(report['context']['mode'],'demo')
        self.assertEqual(service.forward_status(),[])
        for format in ('json','md','csv'):
            r=self.request('/api/v1/export?kind=report&id='+job['id']+'&format='+format)
            self.assertEqual(r[0],200)
            self.assertIsInstance(r[1],bytes)
        self.assertEqual(self.request('/api/v1/export?id=../../missing')[0],400)

    def test_startup_resumes_accepted_queued_jobs_without_new_market_requests(self):
        service=self.work.v2
        job=service.enqueue('scan',{'mode':'demo'},spawn=False)
        with patch('qsa.service_v2.launch_worker',return_value=123) as launch:
            result=service.startup_check()
        self.assertEqual(result['resumed'],[job['id']])
        self.assertEqual(launch.call_count,1)
        self.assertFalse(result['calendar_known'])

    def test_frozen_backtest_replay_never_calls_provider(self):
        service=self.work.v2
        data=frames(8)
        for frame in data:service.repo.register_snapshot(frame['dataset'])
        request={'start':data[0]['trade_date'],'end':data[-1]['trade_date'],'template':'trend'}
        job=service.enqueue('backtest',request,spawn=False)
        first=service.execute(job['id'])
        self.assertEqual(first['state'],'completed',first.get('error'))
        result=service.repo.read_object(first['result']['object_id'])
        self.assertEqual(result['status'],'completed',result.get('failure'))
        with patch.object(FreeProvider,'collect',side_effect=AssertionError('replay must not fetch')):
            second_job=service.enqueue('backtest',{'replay_id':job['id']},spawn=False)
            second=service.execute(second_job['id'])
        self.assertEqual(second['state'],'completed',second.get('error'))
        replayed=service.repo.read_object(second['result']['object_id'])
        for key in ('curve','trades','orders','metrics'):self.assertEqual(result[key],replayed[key])

    def test_neutral_queue_is_price_independent_and_checkpoints_resume(self):
        service=self.work.v2
        frame=frames(1)[0]
        self.work.store.save(legacy_view(frame['dataset']))
        job=service.enqueue('backfill',{'batch_size':3},spawn=False)
        self.assertEqual(job['payload']['batch_codes'],['600001.SH','600002.SH','600003.SH'])
        calls=[]
        def history(req):
            calls.append(req.code)
            return ProviderResult('unavailable',reason='unit-test missing history',provider_id='fixture')
        with patch.object(FreeProvider,'get_history',side_effect=history):
            done=service.execute(job['id'])
        self.assertEqual(done['state'],'completed',done.get('error'))
        self.assertEqual(calls,job['payload']['batch_codes'])
        next_job=service.enqueue('backfill',{'batch_size':3,'request_id':'next'},spawn=False)
        self.assertEqual(next_job['payload']['batch_codes'],['600004.SH','600005.SH'])

    def test_collect_disables_price_based_history_and_retains_actual_availability(self):
        service=self.work.v2
        value=legacy_view(frames(1)[0]['dataset'])
        value['meta'].pop('available_at',None)
        with patch('qsa.free_provider.fetch_free',return_value=value) as fetch,patch.object(FreeProvider,'get_trade_calendar',return_value=ProviderResult('unavailable')):
            result=FreeProvider(service.repo).collect(value['meta']['trade_date'])
        self.assertEqual(fetch.call_args.args[0]['history_limit'],0)
        self.assertIsNotNone(result.available_at)
        self.assertEqual(result.data['meta']['evidence_digest'],result.evidence_digest)
        self.assertEqual(result.data['meta']['history_sampling'],'disabled; separate neutral symbol queue')

    def test_scheduler_xml_is_timezone_aware_and_does_not_install_on_preview(self):
        definitions=task_definitions(self.work.store.root)
        self.assertEqual(len(definitions),5)
        collect=next(d for d in definitions if d['kind']=='collect')
        self.assertIn('08:30',collect['start_utc'])
        xml=task_xml(collect).decode('utf-16')
        self.assertIn('InteractiveToken',xml)
        self.assertIn('LeastPrivilege',xml)
        with patch('subprocess.run',side_effect=AssertionError('preview must not register')):
            self.assertEqual(self.request('/api/v1/scheduler/preview',{})[0],200)


if __name__=='__main__':unittest.main()
