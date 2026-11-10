"""The native transport runs core handlers without listening on any socket."""
import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import Workbench
from qsa.desktop import DesktopBridge,desktop_html,data_directory


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.work=Workbench(Path(self.temp.name));self.bridge=DesktopBridge(self.work)

    def tearDown(self):
        self.work.histories.close();self.work.reviews.close();self.temp.cleanup()

    def test_native_status_and_quant_use_in_memory_dispatch(self):
        with patch('socket.socket',side_effect=AssertionError('desktop must not listen or connect to a local server')):
            r=self.bridge.request('/api/status')
            self.assertEqual(r['status'],200);self.assertTrue(r['data']['desktop'])
            self.assertEqual(r['data']['version'],'0.6.0')
            checked=self.bridge.request('/api/quant/stocks/check',{'mode':'demo','codes':['DEMO001'],'start':'2026-09-01','end':'2026-10-22'})
        self.assertEqual(checked['data']['ready'],1)

    def test_native_mutations_keep_same_config_validation(self):
        saved=self.bridge.request('/api/quant/strategy',{'name':'桌面测试策略','config':{}})
        self.assertEqual(saved['status'],200)
        invalid=self.bridge.request('/api/quant/strategy',{'name':'测试','config':{'capital':-1}})
        self.assertEqual(invalid['status'],400)
        self.assertEqual(len(self.work.quant.store.strategies()),1)

    def test_bridge_blocks_external_urls_and_core_handlers_block_traversal(self):
        for path in ('https://example.com/api/status','file:///secret','/secret'):
            self.assertEqual(self.bridge.request(path)['status'],400)
        self.assertEqual(self.bridge.request('/api/quant/result?id=../../active')['status'],400)

    def test_json_artifact_and_blob_exports_use_native_save(self):
        with patch.object(self.bridge,'_save',return_value={'saved':True}) as save:
            self.bridge.save_artifact('/api/snapshot?mode=demo','行情.json')
            name,raw=save.call_args.args
            self.assertEqual(name,'行情.json');self.assertEqual(json.loads(raw)['schema_version'],1)
            self.bridge.save_file('策略.csv',base64.b64encode('中文数据'.encode()).decode())
            self.assertEqual(save.call_args.args[1],'中文数据'.encode())

    def test_html_uses_local_assets_and_bridge_precedes_app_boot(self):
        html=desktop_html()
        self.assertNotIn('src="/',html);self.assertNotIn('href="/',html)
        self.assertLess(html.index('desktop.js'),html.index('app.js'))
        self.assertEqual(data_directory(self.temp.name),Path(self.temp.name).resolve())

    def test_frozen_worker_is_separate_console_entry_not_recursive_gui(self):
        from qsa.source_adapters import run_worker
        with (patch('qsa.source_adapters.sys.frozen',True,create=True),patch('qsa.source_adapters.sys.executable',str(Path(self.temp.name)/'Zhangshi.exe')),
             patch('qsa.source_adapters.subprocess.run') as run):
            run.return_value.stdout='{"ok":true,"data":{"scope":"test"}}'
            self.assertEqual(run_worker({'provider':'tencent'}),{'scope':'test'})
            self.assertEqual(Path(run.call_args.args[0][0]).name,'ZhangshiWorker.exe')
            self.assertEqual(run.call_args.args[0][1],'--source-worker')

    def test_worker_protocol_remains_utf8_under_a_chinese_console_encoding(self):
        script=Path(__file__).resolve().parents[1]/'scripts/source_worker.py'
        task={'provider':'unknown','action':'test','date':'2026-10-23','symbol':'中文测试'}
        process=subprocess.run([sys.executable,str(script)],input=json.dumps(task,ensure_ascii=False).encode('utf-8'),
            capture_output=True,env={**os.environ,'PYTHONIOENCODING':'gbk'},timeout=10)
        self.assertEqual(process.returncode,0)
        value=json.loads(process.stdout.decode('utf-8'))
        self.assertFalse(value['ok']);self.assertIn('不支持',value['error'])


if __name__=='__main__':unittest.main()
