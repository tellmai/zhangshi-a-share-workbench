"""Bounded optional-library workers and a validated, auditable history fallback."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from .data import atomic_json, validate_dataset
from .source_config import read_settings

PROJECT=Path(__file__).resolve().parents[1]
LABELS={'tencent':'腾讯历史日线','eastmoney':'东方财富历史日线','baostock':'BaoStock历史日线','akshare':'AKShare/东方财富历史日线'}
_health={}
_lock=threading.RLock()


def run_worker(task,timeout=30):
    env={**os.environ,'PYTHONIOENCODING':'utf-8'}
    try:
        command=[str(Path(sys.executable).parent/'ZhangshiWorker.exe'),'--source-worker'] if getattr(sys,'frozen',False) else [sys.executable,str(PROJECT/'scripts'/'source_worker.py')]
        result=subprocess.run(command,
            input=json.dumps(task,ensure_ascii=False),capture_output=True,text=True,encoding='utf-8',
            timeout=timeout,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    except subprocess.TimeoutExpired:
        raise ValueError(f'来源调用超过{timeout}秒，已停止本次子进程') from None
    try:payload=json.loads(result.stdout)
    except (ValueError,TypeError):raise ValueError('来源子进程未返回有效结果；请检查本机依赖') from None
    if not payload.get('ok'):raise ValueError(payload.get('error','来源调用失败'))
    return payload['data']


def check_bars(code,day,bars,quote=None):
    if not bars or bars[-1]['date']!=day:raise ValueError('日线没有更新到目标交易日，不以旧数据代替')
    if any(b.get('adj_factor') is None for b in bars):raise ValueError('日线复权信息缺失')
    if quote and any(abs(bars[-1][k]-quote[k])>.005001 for k in ('open','high','low','close')):raise ValueError('历史末根OHLC与已核验收盘报价冲突')
    candidate={'schema_version':1,'meta':{'trade_date':day,'source':'来源适配器校验','calendar':sorted({b['date'] for b in bars})},
        'stocks':[{'code':code,'name':'历史数据校验','bars':bars}],'sectors':[]}
    validate_dataset(candidate)


def library_history(provider,code,day,cache,evidence=None,timeout=30):
    if provider not in ('baostock','akshare') or not re.fullmatch(r'(?:6\d{5}\.SH|[03]\d{5}\.SZ)',code):raise ValueError('无效的历史来源或证券代码')
    path=Path(cache)/provider/f'{code}-{day}.json'
    if path.exists():
        bars=json.loads(path.read_text(encoding='utf-8'));check_bars(code,day,bars);return bars
    result=run_worker({'provider':provider,'action':'history','code':code,'date':day},timeout)
    bars=result['bars'];check_bars(code,day,bars)
    atomic_json(path,bars)
    if evidence:atomic_json(Path(evidence)/(provider+'-raw.json'),result)
    return bars


def history_chain(code,day,root,evidence,validator,progress=lambda *_:None):
    from .free_provider import history,history_tencent
    root=Path(root);settings=read_settings(root);failures=[]
    evidence=Path(evidence)/'history'/code
    evidence.mkdir(parents=True,exist_ok=True)
    for provider in settings['history_order']:
        label=LABELS[provider];key=(str(root.resolve()),provider)
        with _lock:health=_health.get(key,{})
        if health.get('until',0)>time.time():
            failures.append({'source':label,'error':'连续失败后暂避120秒，其他来源继续'});continue
        progress('正在读取'+label+'，校验日期、日线与复权')
        try:
            if provider=='tencent':bars=history_tencent(code,day,root/'history',evidence=evidence)
            elif provider=='eastmoney':bars=history(code,day,root/'history',evidence=evidence)
            else:bars=library_history(provider,code,day,root/'history',evidence,settings['library_timeout'])
            validator(bars,label)
            atomic_json(evidence/'result.json',{'code':code,'date':day,'source':label,'sessions':len(bars),'previous_attempts':failures})
            atomic_json(evidence/'validated-bars.json',bars)
            with _lock:_health[key]={'failures':0,'until':0}
            return bars,label,failures
        except Exception as error:
            failures.append({'source':label,'error':f'{type(error).__name__}: {str(error)[:180]}'})
            with _lock:
                count=_health.get(key,{}).get('failures',0)+1
                _health[key]={'failures':count,'until':time.time()+120 if count>=3 else 0}
    atomic_json(Path(evidence)/'history-chain-failures.json',failures)
    raise ValueError('；'.join(f"{r['source']}：{r['error']}" for r in failures))
