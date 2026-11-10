"""Versioned local API: structured errors and explicit data context."""
from __future__ import annotations

import csv
import io
import json
from datetime import date
from urllib.parse import parse_qs, urlparse

from .application import Response
from .archive import verify_backup
from .contracts import ResearchConfig, canonical, digest, symbol, utc_now
from .providers import register_calendar
from .quality import context, normalize
from .repository import Conflict


def dispatch(service, method, url, payload=None):
    parsed = urlparse(url)
    path = parsed.path.removeprefix("/api/v1/").strip("/")
    params = parse_qs(parsed.query)
    get = lambda name, default=None: params.get(name, [default])[0]
    mode, day = get("mode", "active"), get("date")
    def response(data, status=200, ctx=None):
        return Response(status, {"data": data, "context": ctx or {"exchange_timezone": "Asia/Shanghai", "schema_version": 2}})
    try:
        if method == "GET":
            if path == "health":
                return response(service.health())
            if path == "settings":
                return response(service.config())
            if path == "snapshots":
                return response(service.repo.snapshot_rows("demo" if mode == "demo" else "real")[-500:])
            if path == "jobs":
                return response(service.jobs.list())
            if path.startswith("jobs/"):
                return response(service.jobs.get(path.split("/")[1]))
            if path in ("market", "themes", "candidates", "report"):
                report = service.report(mode, day)
                data = report if path == "report" else report[{"market": "market", "themes": "themes", "candidates": "candidates"}[path]]
                return response(data, ctx=report["context"])
            if path.startswith("themes/"):
                report = service.report(mode, day)
                key = path.split("/")[1]
                theme = next((t for t in report["themes"] if t["id"] == key), None)
                if not theme:
                    return Response(404, {"error": {"code": "NOT_FOUND", "message": "题材未在该日覆盖范围内"}})
                return response(theme["leaders"] if path.endswith("/leaders") else theme, ctx=report["context"])
            if path.startswith("candidates/"):
                key = path.split("/")[1]
                signal = service.repo.get("signals", key)
                if not signal:
                    signal = next((s for s in service.report(mode, day)["candidates"] if s["signal_id"] == key), None)
                if not signal:
                    raise ValueError("候选证据不存在")
                return response(signal)
            if path == "portfolio":
                dataset, snapshot = service.data(mode, day)
                return response(service.ledger.portfolio(dataset, get("account", "manual"), get("asof")), ctx=context(dataset, snapshot["snapshot_id"], quality=snapshot["quality"]))
            if path == "trades":
                return response(service.ledger.trades(get("account", "manual")))
            if path.startswith("trades/"):
                return response(service.repo.revisions("trades", path.split("/")[1]))
            if path == "plans":
                return response({"watchlists": service.repo.list("watchlists"), "plans": service.repo.list("plans")})
            if path == "strategies":
                from .quant import preset_catalog
                legacy_path = service.repo.root / "quant" / "strategies.json"
                legacy = json.loads(legacy_path.read_text(encoding="utf-8")) if legacy_path.exists() else []
                return response({"templates": {"mainline_pullback": {"name": "主线龙头回踩", "config": service.config()},
                    **{k: {"name": v["name"], "config": v["config"], "engine": "legacy-1.0.0"} for k,v in preset_catalog().items()}},
                    "strategies": service.repo.list("strategies"), "legacy_strategies": legacy})
            if path in ("backtests", "experiments"):
                rows = service.repo.list("runs" if path == "backtests" else "experiments", 1000)
                counts = {}
                for row in service.repo.list("experiments", 100000):
                    counts[row["template"]] = counts.get(row["template"], 0) + 1
                return response({"runs": rows, "attempts_by_template": counts, "forward": service.forward_status()})
            if path.startswith("backtests/"):
                row = service.repo.get("runs", path.split("/")[1])
                if not row:
                    raise ValueError("回测结果不存在")
                return response(service.repo.read_object(row["object_id"]))
            if path == "alerts":
                return response(service.alerts.list())
            if path == "audit/events":
                return response(service.repo.audit_events())
            if path == "backups":
                return response([{"id": p.name, "size": p.stat().st_size} for p in sorted((service.repo.home/"backups").glob("*.zip"), reverse=True)])
            if path == "export":
                kind, key, fmt = get("kind", "report"), get("id"), get("format", "json")
                row = service.repo.get("reports" if kind == "report" else "runs", key)
                if not row:
                    raise ValueError("导出记录不存在")
                result = service.repo.read_object(row["object_id"])
                if fmt == "json":
                    return Response(200, json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8"), "application/json; charset=utf-8")
                if fmt == "md" and kind == "report":
                    return Response(200, (service.repo.home/"reports"/key/"report.md").read_bytes(), "text/markdown; charset=utf-8")
                if fmt == "csv":
                    rows = result.get("candidates", []) if kind == "report" else result.get("orders", [])
                    columns = ["code", "name", "score", "role", "theme_name", "status"] if kind == "report" else ["date", "code", "side", "qty", "price", "fees", "reason"]
                    output = io.StringIO(newline="")
                    writer = csv.writer(output); writer.writerow(columns)
                    def safe(value):
                        text = str(value if value is not None else "")
                        return "'"+text if text.startswith(("=", "+", "-", "@", "\t", "\r")) else text
                    for row in rows:
                        writer.writerow([safe(row.get(c)) for c in columns])
                    return Response(200, ("\ufeff"+output.getvalue()).encode("utf-8"), "text/csv; charset=utf-8")
                raise ValueError("不支持的导出格式")
        elif method == "POST":
            if not isinstance(payload, dict):
                raise ValueError("请求内容必须为对象")
            if path == "settings":
                return response(service.save_config(payload.get("config", payload)))
            if path in ("jobs", "scans", "backtests"):
                kind = "scan" if path == "scans" else "backtest" if path == "backtests" else payload.get("kind")
                request = payload.get("payload", {k:v for k,v in payload.items() if k != "kind"})
                job = service.enqueue(kind, request)
                return response({"job_id": job["id"], "job": job}, 202)
            if path.startswith("jobs/"):
                parts = path.split("/")
                key = parts[1]
                if len(parts) == 3 and parts[2] == "cancel":
                    return response(service.jobs.cancel(key))
                if len(parts) == 3 and parts[2] == "retry":
                    from .jobs import launch_worker
                    job = service.jobs.retry(key)
                    launch_worker(service.repo.root, key)
                    return response({"job_id": key, "job": job}, 202)
            if path == "snapshots/import":
                value = normalize(payload["dataset"], observed=False)
                result = service.repo.register_snapshot(value, origin="user-import")
                service.warehouse.publish(value, result["snapshot_id"])
                return response(result)
            if path == "calendars":
                return response(register_calendar(service.repo, payload))
            if path == "trades":
                return response(service.ledger.append_trade(payload), 201)
            if path.startswith("trades/") and path.endswith("/void"):
                return response(service.ledger.void_trade(path.split("/")[1], payload["revision"], payload["reason"]))
            if path == "trades/preview":
                return response(service.ledger.preview_csv(payload["csv"], payload.get("account", "manual")))
            if path == "trades/import":
                return response(service.ledger.commit_csv(payload["preview_id"]))
            if path == "portfolio/cash":
                return response(service.ledger.cash_entry(payload), 201)
            if path == "portfolio/confirm":
                dataset, _ = service.data(payload.get("mode", "active"))
                return response(service.ledger.confirm(dataset, payload.get("account", "manual")))
            if path == "plans/watch":
                mode = payload.get("mode", "real")
                if mode not in ("real", "demo"):
                    raise ValueError("无效清单模式")
                if payload.get("add") or payload.get("remove"):
                    with service.repo.connect(write=True) as db:
                        previous = db.execute("SELECT revision,payload FROM documents WHERE collection='watchlists' AND key=? ORDER BY revision DESC LIMIT 1", (mode,)).fetchone()
                        codes = set(json.loads(previous["payload"])["codes"] if previous else [])
                        if payload.get("add"):codes.add(symbol(payload["add"]))
                        if payload.get("remove"):codes.discard(symbol(payload["remove"]))
                        if len(codes)>1000:raise ValueError("自选最多1000只")
                        revision=previous["revision"]+1 if previous else 1
                        value={"id":mode,"mode":mode,"codes":sorted(codes)}
                        db.execute("INSERT INTO documents VALUES('watchlists',?,?,?,?)",(mode,revision,utc_now(),canonical(value).decode()))
                        service.repo.audit("watch_change",mode,{"revision":revision},db)
                    return response(service.repo.get("watchlists",mode))
                codes = sorted(set(symbol(c) for c in payload.get("codes", [])))
                if len(codes) > 1000:
                    raise ValueError("自选最多1000只")
                return response(service.repo.put("watchlists", mode, {"id": mode, "mode": mode, "codes": codes}))
            if path == "plans":
                return response(save_plan(service, payload))
            if path == "plans/import":
                return response(import_plans(service, payload))
            if path == "strategies":
                name = str(payload.get("name", "")).strip()
                if not 1 <= len(name) <= 80:
                    raise ValueError("策略名称需1至80字")
                template = payload.get("template", "mainline_pullback")
                if template not in ("mainline_pullback", "trend", "volume", "pullback", "relay"):
                    raise ValueError("未知策略模板")
                config = ResearchConfig.model_validate(payload.get("config") or service.config()).model_dump()
                import secrets
                key = payload.get("id") or secrets.token_hex(12)
                previous = service.repo.get("strategies", key)
                return response(service.repo.put("strategies", key, {"id": key, "name": name, "template": template,
                    "config": config, "config_hash": digest(config)}, expected=payload.get("expected_revision")))
            if path.startswith("alerts/") and path.endswith("/read"):
                return response(service.alerts.acknowledge(path.split("/")[1]))
            if path == "scheduler/preview":
                from .scheduler import task_definitions
                return response(task_definitions(service.repo.root))
            if path == "scheduler":
                from .scheduler import configure
                return response(configure(service.repo, payload.get("enabled", False)))
            if path == "backups/verify":
                name = str(payload["id"])
                if not name or name != __import__("pathlib").Path(name).name:
                    raise ValueError("备份编号无效")
                return response(verify_backup(service.repo.home/"backups"/name))
        return Response(404, {"error": {"code": "NOT_FOUND", "message": "接口不存在"}})
    except Conflict as error:
        return Response(409, {"error": {"code": "REVISION_CONFLICT", "message": str(error)}})
    except (ValueError, TypeError, KeyError, OverflowError) as error:
        return Response(400, {"error": {"code": "VALIDATION_ERROR", "message": str(error)[:1500]}})
    except Exception as error:
        service.repo.audit("api_error", path, {"type": type(error).__name__, "message": str(error)[:300]})
        return Response(500, {"error": {"code": "OPERATION_FAILED", "message": f"{type(error).__name__}: {str(error)[:500]}"}})


def save_plan(service, payload):
    mode, day = payload.get("mode", "real"), payload["date"]
    date.fromisoformat(day)
    if mode not in ("real", "demo"):
        raise ValueError("无效清单模式")
    codes = list(dict.fromkeys(symbol(c) for c in payload.get("codes", [])))
    if len(codes) > 10:
        raise ValueError("观察清单最多10只")
    note = str(payload.get("note", ""))
    if len(note) > 10000:
        raise ValueError("清单笔记过长")
    key = mode + ":" + day
    return service.repo.put("plans", key, {"id": key, "mode": mode, "date": day, "codes": codes, "note": note}, expected=payload.get("expected_revision"))


def import_plans(service, payload):
    key = digest(payload)
    old = service.repo.get("client_imports", key)
    if old:
        return old
    source_object_id = service.repo.object(payload, "client-import-original")
    imported, conflicts = [], []
    for mode in ("real", "demo"):
        codes = [symbol(c.split(":", 1)[1]) for c in payload.get("watch", []) if c.startswith(mode+":")]
        existing = service.repo.get("watchlists", mode, {"codes": []})
        combined = sorted(set(existing["codes"]) | set(codes))
        if len(combined) > 1000:
            raise ValueError("合并自选超过1000只")
        service.repo.put("watchlists", mode, {"id": mode, "mode": mode, "codes": combined})
    for plan_id, item in payload.get("plans", {}).items():
        mode, day = plan_id.split(":", 1)
        existing = service.repo.get("plans", plan_id)
        if existing and (existing["codes"] != item.get("codes", []) or existing["note"] != item.get("note", "")):
            conflicts.append(plan_id)
            continue
        save_plan(service, {"mode": mode, "date": day, **item})
        imported.append(plan_id)
    return service.repo.put("client_imports", key, {"id": key, "source_object_id": source_object_id, "imported": imported, "conflicts": conflicts,
        "note": "冲突保留服务端版本；原浏览器资料保留，可人工合并"}, immutable=True)
