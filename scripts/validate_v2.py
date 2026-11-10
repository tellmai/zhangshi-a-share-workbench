"""Real-snapshot compatibility, migration, restore and read-latency evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PROBE = r'''
import json,sys
sys.path.insert(0,sys.argv[1])
from qsa.analytics import analyze
from qsa.quant import config_settings,scan,replay
d=json.load(open(sys.argv[2],encoding='utf-8'))
out={}
for style in ('balanced','relay','trend'):
 a=analyze(d,{'style':style})
 out[style]={k:a[k] for k in ('stocks','sectors','candidates','market','quality')}
for t in ('trend','volume','pullback','relay'):
 a=scan(d,config_settings({'template':t}))
 out['scan_'+t]=a
c=config_settings({'ma_alignment':False,'breakout_days':0,'min_volume_ratio':0,'min_return5':-50,'min_amount':0,'close_position_min':0,'holding_days':2})
days=d['meta']['calendar'][-10:]
out['replay']=replay(d,c,days[0],days[-1])
print(json.dumps(out,ensure_ascii=False,sort_keys=True,allow_nan=False))
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default=str(ROOT/"data"))
    parser.add_argument("--output", default=str(ROOT/"docs"/"validation-v2.json"))
    args = parser.parse_args()
    from qsa.archive import backup, code_manifest, migrate, restore
    from qsa.data import SnapshotStore
    from qsa.service_v2 import WorkbenchV2
    data = Path(args.data_dir).resolve()
    active = data/"active.json"
    source_hash = hashlib.sha256(active.read_bytes()).hexdigest()
    baseline = ROOT/"build"/"baseline-0.6.0"
    extracted = baseline/"extracted"
    with zipfile.ZipFile(baseline/"source.zip") as archive:
        for name in archive.namelist():
            target = (extracted/name).resolve()
            if not target.is_relative_to(extracted.resolve()):
                raise ValueError("基线文件越界")
        archive.extractall(extracted)
    outputs = []
    for root in (extracted, ROOT):
        proc = subprocess.run([sys.executable,"-X","utf8","-c",PROBE,str(root),str(active)],cwd=root,
            capture_output=True,text=True,encoding="utf-8",timeout=120)
        if proc.returncode:
            raise RuntimeError(proc.stderr[-3000:])
        outputs.append(json.loads(proc.stdout))
    if outputs[0] != outputs[1]:
        changed = [k for k in outputs[0] if outputs[0][k] != outputs[1][k]]
        raise AssertionError("旧引擎基线改变："+str(changed))
    old_files = {str(p.relative_to(data)):hashlib.sha256(p.read_bytes()).hexdigest() for p in data.rglob("*.json")
        if "v2" not in p.relative_to(data).parts and "desktop-profile" not in p.relative_to(data).parts}
    service = WorkbenchV2(SnapshotStore(data))
    first = migrate(service.repo)
    second = migrate(service.repo)
    assert not first["failures"] and not second["imported"], (first, second)
    dataset, index = service.ensure_snapshot()
    start = time.perf_counter()
    partition = service.warehouse.publish(dataset, index["snapshot_id"])
    report = service.report()
    calculation_seconds = time.perf_counter()-start
    reads = {}
    from qsa.router_v2 import dispatch
    for route in ("health","jobs","report","portfolio","plans","experiments"):
        durations=[]
        for _ in range(8):
            start=time.perf_counter()
            response=dispatch(service,"GET","/api/v1/"+route)
            assert response.status==200, response.payload
            durations.append((time.perf_counter()-start)*1000)
        reads[route]={"median_ms":round(statistics.median(durations),2),"p95_ms":round(sorted(durations)[-1],2),"samples":len(durations)}
    saved=backup(service.repo)
    target=ROOT/"build"/"restore-drill"/saved["id"].removesuffix(".zip")
    recovered=restore(saved["path"], target)
    restored=WorkbenchV2(SnapshotStore(target))
    assert restored.report()["candidates"]==report["candidates"]
    assert hashlib.sha256((target/"active.json").read_bytes()).hexdigest()==source_hash
    assert all(hashlib.sha256((data/name).read_bytes()).hexdigest()==sha for name,sha in old_files.items())
    result={"ok":True,"code":code_manifest(),"platform":platform.platform(),"python":sys.version,"logical_cpus":os.cpu_count(),
        "data_directory":str(data),"trade_date":dataset["meta"]["trade_date"],"original_active_sha256":source_hash,
        "legacy_golden":{"equal":True,"groups":list(outputs[0]),"sha256":hashlib.sha256(json.dumps(outputs[0],sort_keys=True).encode()).hexdigest(),
            "closed_trades":outputs[0]["replay"]["metrics"]["closed_trades"] if outputs[0]["replay"].get("metrics") else None},
        "migration":{"first":first,"repeat":second,"unchanged_original_json_files":len(old_files)},"parquet":partition,
        "quality":report["context"]["quality"],"daily_calculation_seconds":round(calculation_seconds,3),"read_latency":reads,
        "backup":saved,"restore":recovered,"forward_30_sessions_complete":False,"strategy_profitability_validated":False,
        "note":"参考当前机器实测；网络等待不计入。P95取8次样本最大值，非大规模负载统计。"}
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({k:v for k,v in result.items() if k not in ("code","parquet")},ensure_ascii=False,indent=2))


if __name__=="__main__":main()
