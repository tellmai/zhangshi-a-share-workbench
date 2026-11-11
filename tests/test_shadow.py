import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from qsa.archive import code_manifest, run_manifest
from qsa.contracts import digest, timestamp
from qsa.data import SnapshotStore
from qsa.mainline import evaluate_day
from qsa.service_v2 import WorkbenchV2
from qsa.version import ENGINE_VERSION
from v2_fixture import frames


class PublishedShadowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.service=WorkbenchV2(SnapshotStore(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def publish(self, count=9, timely=True):
        sample=frames(count)
        prior=[]
        for frame in sample:
            data=copy.deepcopy(frame['dataset'])
            for stock in data['stocks']:
                bars=stock['bars']
                bars[-1]['volume_shares']=sum(b['volume_shares'] for b in bars[-21:-1])/20*.8
            day=frame['trade_date']
            cutoff=timestamp(day+'T19:31:00+08:00')
            published_at=timestamp(day+'T19:31:01+08:00')
            clock=patch('qsa.repository.utc_now',return_value=published_at) if timely else patch('qsa.repository.utc_now',wraps=__import__('qsa.contracts',fromlist=['utc_now']).utc_now)
            with clock:
                snapshot=self.service.repo.register_snapshot(data)
                report=evaluate_day(data,self.service.config(),prior,cutoff,snapshot['snapshot_id'])
                key='published-'+day
                report.update(id=key,manifest={**run_manifest(snapshot['snapshot_id'],self.service.config(),cutoff),'synthetic':True})
                object_id=self.service.repo.object(report,'research-report')
                self.service.repo.put('reports',key,{'id':key,'mode':'real','trade_date':day,
                    'snapshot_id':snapshot['snapshot_id'],'object_id':object_id,'available_at':published_at,
                    'config_hash':digest(self.service.config())},immutable=True)
                for signal in report['candidates']:
                    self.service.repo.put('signals',signal['signal_id'],signal,immutable=True)
            prior.append(report)
        return sample

    def test_shadow_orders_reference_the_exact_published_signal_and_are_idempotent(self):
        sample=self.publish()
        job=self.service.enqueue('shadow',{'date':sample[-1]['trade_date']},spawn=False)
        done=self.service.execute(job['id'])
        self.assertEqual(done['state'],'completed',done.get('error'))
        self.assertEqual(done['result'].get('status'),'completed',done['result'])
        trades=self.service.ledger.trades('shadow')
        self.assertTrue(trades)
        self.assertTrue(all(self.service.repo.get('signals',t['signal_id']) for t in trades))
        another=self.service.enqueue('shadow',{'date':sample[-1]['trade_date'],'request_id':'repeat'},spawn=False)
        self.assertEqual(self.service.execute(another['id'])['state'],'completed')
        self.assertEqual(len(self.service.ledger.trades('shadow')),len(trades))

    def test_late_backfilled_reports_do_not_create_historical_shadow_executions(self):
        sample=self.publish(4,timely=False)
        job=self.service.enqueue('shadow',{'date':sample[-1]['trade_date']},spawn=False)
        done=self.service.execute(job['id'])
        self.assertEqual(done['state'],'completed')
        self.assertEqual(done['result']['status'],'BLOCKED')
        self.assertEqual(self.service.ledger.trades('shadow'),[])

    def test_engine_changes_preserve_and_stop_the_old_shadow_series(self):
        self.service.repo.put('shadow_series','active',{'id':'active','config_hash':digest(self.service.config()),
            'code_hash':'old-engine-code','engine_version':ENGINE_VERSION,'started_on':'2026-01-01'})
        job=self.service.enqueue('shadow',{'mode':'demo'},spawn=False)
        done=self.service.execute(job['id'])
        self.assertEqual(done['state'],'failed')
        self.assertIn('独立data目录',done['error'])
        self.assertEqual(self.service.repo.get('shadow_series','active')['code_hash'],'old-engine-code')

    def test_data_revision_cannot_rewrite_or_duplicate_executed_shadow_history(self):
        sample=self.publish()
        job=self.service.enqueue('shadow',{'date':sample[-1]['trade_date']},spawn=False)
        done=self.service.execute(job['id'])
        self.assertEqual(done['result']['status'],'completed',done)
        before=self.service.ledger.trades('shadow')
        self.assertTrue(before)
        result=self.service.repo.read_object(done['result']['object_id'])
        result['orders'][0]['price']+=.01
        object_id=self.service.repo.object(result,'synthetic-revised-orders')
        revised=self.service.enqueue('shadow',{'date':sample[-1]['trade_date'],'request_id':'revised'},spawn=False)
        with patch.object(self.service,'backtest',return_value={'object_id':object_id,'status':'completed'}):
            rejected=self.service.execute(revised['id'])
        self.assertEqual(rejected['state'],'failed')
        self.assertIn('数据修订',rejected['error'])
        self.assertEqual(self.service.ledger.trades('shadow'),before)

    def test_older_request_does_not_move_shadow_account_back_in_time(self):
        sample=self.publish()
        latest=self.service.enqueue('shadow',{'date':sample[-1]['trade_date']},spawn=False)
        self.assertEqual(self.service.execute(latest['id'])['result']['status'],'completed')
        before=self.service.ledger.trades('shadow')
        earlier=self.service.enqueue('shadow',{'date':sample[-2]['trade_date']},spawn=False)
        done=self.service.execute(earlier['id'])
        self.assertEqual(done['state'],'completed',done.get('error'))
        self.assertEqual(done['result']['status'],'BLOCKED')
        self.assertEqual(self.service.ledger.trades('shadow'),before)


if __name__=='__main__':unittest.main()
