"""One repeatable offline regression command, without touching user data."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description="运行Python与前端回归")
    parser.add_argument("--node", default=shutil.which("node"))
    args = parser.parse_args()
    if not args.node:
        parser.error("需要Node执行前端检查；请安装Node或传入--node完整路径")
    folder = ROOT / "build" / "validation"
    folder.mkdir(parents=True, exist_ok=True)
    scratch = ROOT / "build" / "tmp"
    scratch.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "TEMP": str(scratch), "TMP": str(scratch), "PYTHONUTF8": "1"}
    commands = [[sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"]]
    commands += [[args.node, str(p)] for p in sorted((ROOT / "tests").glob("test_*.cjs"))]
    records = []
    for index, command in enumerate(commands):
        start = time.perf_counter()
        proc = subprocess.run(command, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        name = "python" if index == 0 else Path(command[-1]).stem
        (folder / (name + ".log")).write_text(proc.stdout, encoding="utf-8")
        records.append({"name": name, "ok": proc.returncode == 0, "seconds": round(time.perf_counter()-start, 3),
            "checks": proc.stdout.count("PASS:"), "log": str(folder / (name + ".log"))})
        print(name, "PASS" if proc.returncode == 0 else "FAIL", flush=True)
        if proc.returncode:
            print(proc.stdout[-5000:])
    result = {"ok": all(r["ok"] for r in records), "python": sys.version, "checks": records}
    (folder / "regression.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
