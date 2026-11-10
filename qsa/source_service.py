"""Read-only connection diagnostics; account secrets never enter settings or logs."""
from __future__ import annotations

import copy
import json
import re
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime

from .data import SHANGHAI,atomic_json,iso_date,now_iso
from .source_adapters import check_bars,run_worker
from .source_config import CATALOG,installed,read_settings,save_settings


class SourceService:
    def __init__(self,snapshots):
        self.snapshots=snapshots;self.root=snapshots.root;self.lock=threading.RLock()
        self.job={'state':'idle','message':'可测试单个来源，或检测全部免费通道','progress':0}

    def status(self):
        with self.lock:
            path=self.root/'sources'/'health.json'
            health=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
            return {'settings':read_settings(self.root),'catalog':[{**value,'id':key,'installed':installed(value['module'])} for key,value in CATALOG.items()],
                'health':health,'job':dict(self.job),'snapshot_date':self.snapshots.read()['meta']['trade_date'],
                'notes':['接口采样通过不代表全市场字段全部可用；测试不替换行情快照。','AKShare/东财与直连东财属于同一上游，不能算两个独立核验来源。',
                    '同花顺公开板块仅独立研究；普通客户端账号不等于iFinD数据接口权限。']}

    def settings(self,value):
        with self.lock:
            if self.job['state']=='running':raise ValueError('连接测试进行中，请完成后保存设置')
            return save_settings(self.root,value)

    def start(self,payload):
        if not isinstance(payload,dict) or set(payload)-{'provider','date','code','symbol','username','password'}:raise ValueError('连接测试参数无效')
        provider=payload.get('provider','all')
        if provider not in (*CATALOG,'all'):raise ValueError('未知数据来源')
        day=iso_date(payload.get('date') or self.snapshots.read()['meta']['trade_date'])
        if day>datetime.now(SHANGHAI).date().isoformat():raise ValueError('测试日期不能在未来')
        code=payload.get('code') or '600838.SH'
        if not isinstance(code,str) or not re.fullmatch(r'(?:6\d{5}\.SH|[03]\d{5}\.SZ)',code):raise ValueError('测试证券代码格式无效')
        symbol=payload.get('symbol') or '半导体'
        if not isinstance(symbol,str) or not 1<=len(symbol)<=30 or any(ord(c)<32 for c in symbol):raise ValueError('同花顺行业名称无效')
        task={'provider':provider,'date':day,'code':code,'symbol':symbol,'action':'probe'}
        if provider=='ifind':
            if not installed('iFinDPy'):raise ValueError('未检测到官方iFinDPy，请先按官方说明安装SDK')
            for key in ('username','password'):
                if not isinstance(payload.get(key),str) or not 1<=len(payload[key])<=256:raise ValueError('请在本机页面填写iFinD数据接口账号和密码')
                task[key]=payload[key]
        elif payload.get('username') or payload.get('password'):raise ValueError('免费来源不接收账号密码')
        with self.lock:
            if self.job['state']=='running':raise ValueError('已有连接测试在运行')
            run_id=secrets.token_hex(8)
            self.job={'state':'running','id':run_id,'progress':0,'message':'正在检测来源及数据日期','date':day}
        def worker():
            folder=self.root/'evidence'/f'sources-{day}-{run_id}';folder.mkdir(parents=True)
            ids=[x for x in CATALOG if x!='ifind'] if provider=='all' else [provider]
            completed=0
            with ThreadPoolExecutor(max_workers=3) as pool:
                pending={pool.submit(self.probe,{**task,'provider':p},folder):p for p in ids}
                for future in as_completed(pending):
                    p=pending[future]
                    try:result=future.result()
                    except Exception as error:result={'state':'error','message':'检测未完成：'+type(error).__name__,'checked_at':now_iso()}
                    with self.lock:
                        health_path=self.root/'sources'/'health.json'
                        health=json.loads(health_path.read_text(encoding='utf-8')) if health_path.exists() else {}
                        health[p]=result;atomic_json(health_path,health)
                        completed+=1;self.job.update(progress=round(completed/len(ids)*100),message=f'已检测{completed}/{len(ids)}个来源 · '+CATALOG[p]['name'])
            with self.lock:self.job.update(state='done',progress=100,message='检测完成，请按每个来源的日期、范围和状态判断',finished_at=now_iso())
            # Drop references to credentials after the one-off SDK worker exits.
            task.pop('username',None);task.pop('password',None)
        self.worker=threading.Thread(target=worker,daemon=True,name='source-diagnostics');self.worker.start()
        return {'job':dict(self.job)}

    def probe(self,task,folder):
        provider=task['provider'];start=time.monotonic();settings=read_settings(self.root)
        base={'checked_at':now_iso(),'requested_date':task['date'],'code':task['code']}
        if not installed(CATALOG[provider]['module']):return {**base,'state':'needs_setup','message':'本机尚未安装所需库或官方SDK'}
        try:
            data=run_worker(task,100 if provider=='ifind' else settings['library_timeout'])
            atomic_json(folder/(provider+'.json'),data)
            if provider=='ifind':
                result={**base,'state':'login_only','message':data['message'],'scope':data['scope']}
            elif 'bars' in data:
                check_bars(task['code'],task['date'],data['bars'])
                result={**base,'state':'success','message':f"目标日历史采样通过，{len(data['bars'])}根日线",'data_date':data['bars'][-1]['date'],'scope':data.get('scope','单股历史，不代表全市场可用'),'sample':data['bars'][-3:]}
            elif provider=='ths':
                rows=data.get('rows',[])
                if not rows or rows[-1].get('日期')!=task['date']:raise ValueError('公开板块指数未更新到目标日期，未加入当日排名')
                result={**base,'state':'research_only','message':data['symbol']+'同日行业指数可读，仅用于独立研究','data_date':rows[-1]['日期'],'scope':data['scope'],'sample':rows[-5:]}
            else:
                result={**base,'state':'success','message':'目标日收盘字段采样通过','data_date':task['date'],'scope':data.get('scope','单股采样'),'sample':data}
            result['evidence']=str((folder/(provider+'.json')).relative_to(self.root))
        except Exception as error:
            message=str(error)[:260]
            if provider=='ifind':
                for key in ('username','password'):
                    if task.get(key):message=message.replace(task[key],'[已隐藏]')
            result={**base,'state':'error','message':message,'scope':'未通过本次采样；现有行情保留'}
        return {**result,'elapsed_seconds':round(time.monotonic()-start,2)}
