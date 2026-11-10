"""Exercise frozen GUI and worker against an isolated copy of the real snapshot."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from datetime import datetime

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--bundle",default=str(ROOT/"dist"/"v1.0.0"/"Zhangshi"))
    args=parser.parse_args()
    bundle=Path(args.bundle).resolve()
    scratch=ROOT/"build"/"package-qa"/datetime.now().strftime("%Y%m%d-%H%M%S%f")
    data=scratch/"data";data.mkdir(parents=True)
    shutil.copy2(ROOT/"data"/"active.json",data/"active.json")
    checksum=hashlib.sha256((data/"active.json").read_bytes()).hexdigest()
    records=[]
    def worker(*arguments):
        command=[str(bundle/"ZhangshiWorker.exe"),"--workbench-worker","--data-dir",str(data),*arguments]
        proc=subprocess.run(command,capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=180,
            creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        if proc.returncode:raise RuntimeError(proc.stderr[-3000:]+proc.stdout[-3000:])
        result=json.loads(proc.stdout)
        records.append({"command":list(arguments),"result":result})
        if result.get("state")=="failed":raise RuntimeError(result.get("error"))
        return result
    status=worker("status")
    assert status["dependencies"]["duckdb"] and status["dependencies"]["pydantic"]
    assert status["data_directory"]==str(data.resolve())
    gui_report=scratch/"desktop.json"
    proc=subprocess.run([str(bundle/"Zhangshi.exe"),"--data-dir",str(data),"--self-test",str(gui_report)],timeout=120)
    desktop=json.loads(gui_report.read_text(encoding="utf-8"))
    assert proc.returncode==0 and desktop["ok"],desktop
    migrated=worker("migrate","--parquet")
    assert not migrated["result"]["failures"]
    # The native window has already exited; the independent worker still produces research.
    scanned=worker("scan")
    assert scanned["result"]["report_id"]
    saved=worker("backup")["result"]
    worker("verify","--backup",saved["path"])
    recovered=worker("restore","--backup",saved["path"],"--destination",str(scratch/"restored"))
    assert hashlib.sha256((data/"active.json").read_bytes()).hexdigest()==checksum
    assert hashlib.sha256((scratch/"restored"/"active.json").read_bytes()).hexdigest()==checksum
    info=json.loads((bundle/"_internal"/"build-info.json").read_text(encoding="utf-8"))
    result={"ok":True,"platform":platform.platform(),"version":status["version"],"python_runtime":"frozen Python3.12",
        "bundle":str(bundle),"isolated_data":str(data),"desktop":desktop,"worker_after_gui_exit":True,
        "original_snapshot_unchanged":True,"build":info,"steps":records,
        "clean_windows_machine":False,"installer_executed":False,
        "note":"同机隔离目录验证，不替代干净Windows参考机验收。未修改正式账户或安装注册信息。"}
    output=ROOT/"docs"/"validation-v2-desktop.json"
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"ok":True,"desktop":desktop,"worker_steps":len(records),"restore":recovered,"output":str(output)},ensure_ascii=False,indent=2))


if __name__=="__main__":main()
