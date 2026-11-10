"""V2 command line and frozen worker entry; no UI or HTTP listener required."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv=None):
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if stream and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="涨势V2 数据、研究与后台任务")
    parser.add_argument("--data-dir", default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    execute = sub.add_parser("execute"); execute.add_argument("--job-id", required=True)
    due = sub.add_parser("due"); due.add_argument("--kind", choices=["collect", "retry", "report", "backfill", "backup"])
    schedule = sub.add_parser("schedule"); schedule.add_argument("--enable", action="store_true"); schedule.add_argument("--disable", action="store_true")
    restored = sub.add_parser("restore"); restored.add_argument("--backup", required=True); restored.add_argument("--destination", required=True)
    verify = sub.add_parser("verify"); verify.add_argument("--backup", required=True)
    calendar = sub.add_parser("calendar"); calendar.add_argument("--file", required=True)
    for kind in ("ingest", "migrate", "scan", "collect", "backfill", "report", "backtest", "shadow", "backup"):
        action = sub.add_parser(kind)
        action.add_argument("--date")
        action.add_argument("--mode", choices=["active", "demo"], default="active")
        action.add_argument("--config")
        action.add_argument("--parquet", action="store_true")
        action.add_argument("--start"); action.add_argument("--end")
        action.add_argument("--batch-size", type=int, default=100)
        action.add_argument("--codes", nargs="+")
        action.add_argument("--template", default="mainline_pullback", choices=["mainline_pullback", "trend", "volume", "pullback", "relay"])
        action.add_argument("--walk-forward", action="store_true"); action.add_argument("--stress", action="store_true")
        action.add_argument("--replay-id")
    args = parser.parse_args(argv)
    from qsa.data import SHANGHAI, SnapshotStore
    from qsa.desktop import data_directory
    from qsa.service_v2 import WorkbenchV2
    service = WorkbenchV2(SnapshotStore(data_directory(args.data_dir)))
    if args.command == "status":
        result = service.health()
    elif args.command == "execute":
        result = service.execute(args.job_id)
    elif args.command == "verify":
        from qsa.archive import verify_backup
        result = verify_backup(args.backup)
    elif args.command == "restore":
        from qsa.archive import restore
        result = restore(args.backup, args.destination)
    elif args.command == "calendar":
        from qsa.providers import register_calendar
        result = register_calendar(service.repo, json.loads(Path(args.file).read_text(encoding="utf-8-sig")))
    elif args.command == "schedule":
        from qsa.scheduler import configure, task_definitions
        result = configure(service.repo, args.enable) if args.enable or args.disable else task_definitions(service.repo.root)
    elif args.command == "due":
        from qsa.review import trading_day
        day = datetime.now(SHANGHAI).date().isoformat()
        service.jobs.recover()
        result = {"date": day, "jobs": [], "skipped": []}
        kinds = [args.kind] if args.kind else ["collect", "report", "backfill", "backup"]
        for item in kinds:
            if item != "backup" and trading_day(day) is not True:
                result["skipped"].append({"kind": item, "reason": "休市或交易日历待核验"})
                continue
            kind = "collect" if item == "retry" else item
            try:
                job = service.enqueue(kind, {"date": day}, spawn=False)
                if job["state"] in ("failed", "interrupted", "cancelled"):
                    service.jobs.retry(job["id"])
                result["jobs"].append(service.execute(job["id"]))
            except Exception as error:
                service.alerts.emit(f"due:{day}:{kind}", "计划任务未能启动", str(error))
                result["jobs"].append({"kind": kind, "state": "failed", "error": str(error)})
        if "report" in kinds and trading_day(day) is True:
            try:
                job = service.enqueue("shadow", {"date": day}, spawn=False)
                result["jobs"].append(service.execute(job["id"]))
            except Exception as error:
                result["jobs"].append({"kind": "shadow", "state": "failed", "error": str(error)})
        service.alerts.deliver()
    else:
        payload = {k:v for k,v in vars(args).items() if k not in ("command", "data_dir") and v is not None}
        if args.config:
            from qsa.contracts import load_config
            payload["config"] = load_config(Path(args.config).read_text(encoding="utf-8-sig"))
        else:
            payload.pop("config", None)
        if args.command != "backfill":
            for key in ("batch_size", "codes"):
                payload.pop(key, None)
        job = service.enqueue(args.command, payload, spawn=False)
        result = service.execute(job["id"])
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 1 if result.get("state") == "failed" else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
