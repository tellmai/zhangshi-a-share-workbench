"""HTTP dispatch tests use an in-memory handler, with no socket access."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import Workbench,make_handler


class QuantAPITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.workbench=Workbench(Path(self.temp.name))
        Handler=make_handler(self.workbench);self.handler=Handler.__new__(Handler)
        self.responses=[];self.handler.send=lambda *args:self.responses.append(args)

    def tearDown(self):
        self.workbench.histories.close();self.workbench.reviews.close();self.temp.cleanup()

    def request(self,path,body=None,token=None):
        h=self.handler;h.path=path;h.headers={"Host":"127.0.0.1:8766"}
        if body is None:h.do_GET()
        else:
            raw=json.dumps(body).encode();h.rfile=io.BytesIO(raw)
            h.headers.update({"Content-Length":str(len(raw)),"X-Workbench-Token":token or ""});h.do_POST()
        return self.responses[-1]

    def test_catalog_and_local_assets(self):
        status,payload,*_=self.request('/api/quant/status?mode=demo')
        self.assertEqual(status,200);self.assertEqual(len(payload['presets']),4)
        self.assertEqual(payload['data']['mode'],'demo')
        for path in ('/quant.js','/quant.css'):
            self.assertEqual(self.request(path)[0],200)

    def test_every_mutation_keeps_csrf_protection(self):
        for path in ('/api/quant/strategy','/api/quant/run','/api/quant/replay','/api/quant/cancel','/api/quant/stocks/resolve','/api/quant/stocks/check','/api/reviews/title'):
            self.assertEqual(self.request(path,{},'wrong')[0],403)
        response=self.request('/api/quant/strategy',{'name':'合成策略','config':{}},self.workbench.csrf)
        self.assertEqual(response[0],200);self.assertEqual(response[1]['versions'][0]['version'],1)

    def test_run_and_replay_dispatch_nonblocking(self):
        with patch.object(self.workbench.quant,'start',return_value={'job':{'state':'running'}}) as start:
            self.assertEqual(self.request('/api/quant/run',{'type':'scan'},self.workbench.csrf)[0],202)
            start.assert_called_with({'type':'scan'})
            self.assertEqual(self.request('/api/quant/replay',{'id':'a'*20},self.workbench.csrf)[0],202)
            start.assert_called_with({},replay_id='a'*20)

    def test_export_and_result_paths_are_bounded(self):
        self.assertEqual(self.request('/api/quant/result?id=../../active')[0],400)
        self.assertEqual(self.request('/api/quant/export?id=../../active&format=json')[0],400)
        self.assertEqual(self.request('/api/quant/status?mode=arbitrary')[0],400)
        self.assertEqual(self.request('/api/quant/replay',{},self.workbench.csrf)[0],400)

    def test_review_title_selection_dispatch(self):
        with patch.object(self.workbench.reviews.store,'choose_title',return_value={'id':'new-version'}) as choose:
            result=self.request('/api/reviews/title',{'id':'example','index':1},self.workbench.csrf)
            self.assertEqual(result[0],200)
            choose.assert_called_once_with('example',1)

    def test_stock_search_and_bulk_resolution_have_explicit_scope(self):
        status,payload,*_=self.request('/api/quant/stocks?mode=demo&q=DEMO002')
        self.assertEqual(status,200);self.assertEqual(payload['stocks'][0]['code'],'DEMO002')
        response=self.request('/api/quant/stocks/resolve',{'mode':'demo','codes':'DEMO001, DEMO002'},self.workbench.csrf)
        self.assertEqual(response[0],200);self.assertEqual(len(response[1]['stocks']),2)
        self.assertEqual(self.request('/api/quant/stocks/resolve',{'mode':'demo','codes':'DEMO001 unknown'},self.workbench.csrf)[0],400)
        self.assertEqual(self.request('/api/quant/stocks?mode=other')[0],400)

    def test_selected_stock_run_dispatch_preserves_selection_and_capital_config(self):
        payload={'type':'stock_backtest','mode':'demo','codes':['DEMO001','DEMO002'],'config':{'capital':200000,'max_positions':5}}
        with patch.object(self.workbench.quant,'start',return_value={'job':{'state':'running'}}) as start:
            self.assertEqual(self.request('/api/quant/run',payload,self.workbench.csrf)[0],202)
            start.assert_called_once_with(payload)

    def test_data_check_returns_readiness_and_validates_mode_and_dates(self):
        response=self.request('/api/quant/stocks/check',{'mode':'demo','codes':['DEMO001'],'start':'2026-09-01','end':'2026-10-22'},self.workbench.csrf)
        self.assertEqual(response[0],200);self.assertEqual(response[1]['ready'],1)
        self.assertEqual(self.request('/api/quant/stocks/check',{'mode':'other','codes':['DEMO001']},self.workbench.csrf)[0],400)
        self.assertEqual(self.request('/api/quant/stocks/check',{'mode':'demo','codes':['DEMO001'],'start':'2026-10-22','end':'2026-09-01'},self.workbench.csrf)[0],400)


if __name__=='__main__':unittest.main()
