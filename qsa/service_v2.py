"""Application services shared by desktop, HTTP and the standalone worker."""
from __future__ import annotations

import copy
import json
import threading
from datetime import date, datetime, timedelta

from .alerts import Alerts
from .archive import backup, code_manifest, migrate, rotate_backups, run_manifest
from .contracts import HistoryRequest, ResearchConfig, canonical, digest, in_universe, timestamp, utc_now
from .data import SHANGHAI, atomic_json
from .jobs import JobStore, launch_worker
from .ledger import Ledger
from .mainline import evaluate_day
from .providers import FreeProvider, register_calendar
from .quality import assess, calendar_visible, context, legacy_view, normalize
from .repository import Conflict, Repository
from .version import APP_VERSION, ENGINE_VERSION
from .warehouse import Warehouse

KINDS = {"ingest", "scan", "collect", "backfill", "backtest", "shadow", "migrate", "backup", "report"}


class WorkbenchV2:
    def __init__(self, snapshots):
        self.snapshots = snapshots
        self.repo = Repository(snapshots.root)
        self.jobs = JobStore(self.repo)
        self.ledger = Ledger(self.repo)
        self.alerts = Alerts(self.repo)
        self.warehouse = Warehouse(self.repo)
        self._cache = {}
        self._source_cache = {}
        self._report_cache = {}
        self._cache_lock = threading.RLock()

    def config(self):
        return self.ledger.configuration()

    def save_config(self, value):
        if isinstance(value, str):
            from .contracts import load_config
            value = load_config(value)
        config = ResearchConfig.model_validate(value).model_dump()
        return self.repo.put("settings", "research", config)

    def startup_check(self):
        """Resume accepted queued work and describe missed data without backdating it."""
        interrupted = self.jobs.recover()
        queued = [j for j in self.jobs.list(100) if j["state"] == "queued"]
        resumed, failures = [], []
        for job in queued[:4]:
            try:
                launch_worker(self.repo.root, job["id"])
                resumed.append(job["id"])
            except OSError as error:
                failures.append({"job_id": job["id"], "reason": str(error)})
        now = datetime.now(SHANGHAI)
        today = now.date().isoformat()
        sessions = sorted({d for c in self.repo.list("calendars", 100) if c.get("verified")
            for d in c["sessions"] if d < today or (d == today and (now.hour, now.minute) >= (16, 30))})
        expected = sessions[-1] if sessions else None
        actual = self.snapshots.read()["meta"]["trade_date"]
        missing = bool(expected and actual < expected)
        if missing:
            self.alerts.emit("missing-close:"+expected, "收盘资料存在缺口", f"期望{expected}，当前快照{actual}。请核对采集任务；旧日期保持原样。")
        result = {"at": utc_now(), "interrupted": interrupted, "resumed": resumed, "failures": failures,
            "remaining_queued": max(0, len(queued)-4), "expected_trade_date": expected,
            "actual_trade_date": actual, "missing_close": missing, "calendar_known": bool(sessions)}
        self.repo.put("startup_checks", "latest", result)
        return result

    def ensure_snapshot(self, mode="active"):
        if mode not in ("active", "real", "demo"):
            raise ValueError("无效数据模式")
        raw = self.snapshots.read("demo" if mode == "demo" else "active")
        with self._cache_lock:
            previous = self._source_cache.get(mode)
            if previous and previous[0] is raw:
                return previous[1]
        source_hash = digest(raw)
        with self._cache_lock:
            if source_hash in self._cache:
                return self._cache[source_hash]
        with self._cache_lock, self.snapshots.transaction():
            if source_hash in self._cache:
                return self._cache[source_hash]
            result = self._index_source(raw, source_hash, mode)
            self._cache = {source_hash: result}
            self._source_cache[mode] = (raw, result)
            return result

    def _index_source(self, raw, source_hash, mode):
        prior = self.repo.get("source_snapshots", source_hash)
        if prior:
            value = self.repo.read_object(prior["snapshot_id"])
            result = {"snapshot_id": prior["snapshot_id"], "quality": assess(value), "available_at": value["meta"].get("available_at")}
        else:
            value = normalize(raw, observed=False)
            result = self.repo.register_snapshot(value, origin="active" if mode != "demo" else "demo")
            self.repo.put("source_snapshots", source_hash, {"id": source_hash, "snapshot_id": result["snapshot_id"]}, immutable=True)
        return value, result

    def health(self):
        import importlib.util
        import sqlite3
        self.jobs.recover()
        raw, indexed = self.ensure_snapshot()
        return {"version": APP_VERSION, "engine_version": ENGINE_VERSION, "schema_version": 2,
            "data_directory": str(self.repo.root), "sqlite_version": sqlite3.sqlite_version,
            "dependencies": {m: bool(importlib.util.find_spec(m)) for m in ("duckdb", "pydantic", "yaml")},
            "quality": indexed["quality"], "snapshot_date": raw["meta"]["trade_date"],
            "snapshot_load_error": getattr(self.snapshots, "load_error", None),
            "jobs": self.jobs.list(20), "config": self.config(),
            "features": {"minute_confirmation": False, "orderbook_fill": False, "automatic_orders": False,
                "manual_ledger": True, "durable_jobs": True, "parquet": True},
            "scheduler": self.repo.get("settings", "scheduler", {"enabled": False}),
            "startup_check": self.repo.get("startup_checks", "latest"),
            "calendars": self.repo.list("calendars", 20), "provider_capabilities": {
                "daily_quotes": "supported", "historical_range": "supported_with_coverage_check",
                "minute": "unavailable", "orderbook": "unavailable", "corporate_actions": "unavailable",
                "calendar": "supported" if self.repo.list("calendars", 1) else "unavailable"}}

    def data(self, mode="active", day=None):
        if day:
            date.fromisoformat(day)
            rows = self.repo.snapshot_rows("demo" if mode == "demo" else "real", day)
            matches = [r for r in rows if r["trade_date"] == day]
            if not matches:
                raise ValueError("该日没有保存的快照；不会以当前成分回填历史")
            row = matches[-1]
            return self.repo.read_object(row["digest"]), {"snapshot_id": row["digest"], "quality": row["quality"], "available_at": row["available_at"]}
        return self.ensure_snapshot(mode)

    def report(self, mode="active", day=None):
        dataset, record = self.data(mode, day)
        config = self.config()
        token = self.repo.revision_token(("settings", "reports", "trades", "cash", "accounts", "valuations"))
        cache_key = (record["snapshot_id"], digest(config), token, datetime.now(SHANGHAI).date().isoformat())
        with self._cache_lock:
            if cache_key in self._report_cache:
                return copy.deepcopy(self._report_cache[cache_key])
        candidates = [r for r in self.repo.list("reports", 1000)
            if (r.get("source_snapshot_id") == record["snapshot_id"] or r.get("snapshot_id") == record["snapshot_id"]) and r.get("config_hash") == digest(config)]
        if candidates:
            result = self.repo.read_object(candidates[0]["object_id"])
            result["published"] = True
        else:
            result = self.calculate(dataset, record["snapshot_id"], config)
            result["published"] = False
        with self._cache_lock:
            self._report_cache = {cache_key: copy.deepcopy(result)}
        return result

    def calculate(self, dataset, snapshot_id, config=None, decision_time=None):
        config = config or self.config()
        day = dataset["meta"]["trade_date"]
        cutoff = timestamp(decision_time or dataset["meta"].get("available_at") or day + "T19:30:00+08:00")
        if dataset["meta"].get("available_at") and timestamp(dataset["meta"]["available_at"]) > cutoff:
            raise ValueError("快照晚于请求决策时点，禁止读取未来数据")
        mode = "demo" if dataset["meta"].get("mode") == "demo" else "real"
        expected = [d for d in dataset["meta"]["calendar"] if d < day][-2:]
        saved = self.repo.list("reports", 2000)
        prior = []
        for d in expected:
            matches = [r for r in saved if r["trade_date"] == d and r["mode"] == mode and r["config_hash"] == digest(config)
                and r.get("available_at") and timestamp(r["available_at"]) <= cutoff]
            if matches:
                chosen = max(matches, key=lambda r: (r["available_at"], r["id"]))
                prior.append(self.repo.read_object(chosen["object_id"]))
        account = self.ledger.portfolio(dataset, "demo" if mode == "demo" else "manual", asof=cutoff)
        result = evaluate_day(dataset, config, prior, cutoff, snapshot_id, account)
        result["account_context"] = {k: account[k] for k in ("account", "asof", "cash", "equity", "invested",
            "high_water", "positions", "confirmed", "incomplete", "ledger_digest")}
        result["prior_report_ids"] = [r["id"] for r in prior if r.get("id")]
        return result

    def enqueue(self, kind, payload=None, spawn=True):
        if kind not in KINDS:
            raise ValueError("未知任务类型")
        payload = copy.deepcopy(payload or {})
        if payload.get("mode") == "demo" and kind in ("collect", "backfill"):
            raise ValueError("演示模式不请求真实行情；请切换真实模式")
        day = payload.get("date") or datetime.now(SHANGHAI).date().isoformat() if kind in ("collect", "backup", "migrate") else payload.get("date") or self.snapshots.read("demo" if payload.get("mode") == "demo" else "active")["meta"]["trade_date"]
        date.fromisoformat(day)
        payload.setdefault("config", self.config())
        payload["config"] = ResearchConfig.model_validate(payload["config"]).model_dump()
        if kind in ("scan", "report", "ingest", "backfill", "shadow"):
            _, source = self.data(payload.get("mode", "active"), payload.get("date"))
            payload["snapshot_id"] = source["snapshot_id"]
        if kind == "backtest":
            if payload.get("replay_id"):
                run = self.repo.get("runs", payload["replay_id"])
                if not run:
                    raise ValueError("运行不存在")
                frozen = self.repo.read_object(run["input_id"])
                if frozen["manifest"]["engine_version"] != ENGINE_VERSION or frozen["manifest"]["code"]["code_hash"] != code_manifest()["code_hash"]:
                    raise ValueError("代码或引擎版本改变，不能宣称精确复现；请保留原环境")
                payload["input_id"] = run["input_id"]
            else:
                payload["input_id"] = self.freeze_backtest(payload)
        if kind == "backfill":
            # Freeze queue membership before dispatch, independently of prices/ranks.
            dataset = self.repo.read_object(payload["snapshot_id"])
            pool = sorted(s["code"] for s in dataset["stocks"] if in_universe(s, payload["config"]["boards"], dataset["meta"].get("mode") == "demo"))
            if payload.get("codes"):
                from .contracts import symbol
                codes = sorted(set(symbol(c) for c in payload["codes"]))
                if set(codes) - set(pool):
                    raise ValueError("历史补齐代码不在研究范围")
                payload["codes"] = codes
                payload.setdefault("reason", "repair")
            else:
                payload["codes"] = pool
                payload.setdefault("reason", "seed")
            limit = payload.get("batch_size", 100)
            if type(limit) is not int or not 1 <= limit <= 500:
                raise ValueError("补齐批量须为1至500")
            payload["batch_size"] = limit
            start = int(self.repo.get("queue_cursors", "neutral", {}).get("cursor", 0)) % max(1, len(payload["codes"])) if payload["reason"] == "seed" else 0
            payload["queue_start"] = start
            payload["batch_codes"] = payload["codes"][start:start+limit]
        job = self.jobs.enqueue(kind, day, payload, payload.get("snapshot_id") or payload.get("input_id") or APP_VERSION)
        if spawn and job["state"] == "queued":
            try:
                pid = launch_worker(self.repo.root, job["id"])
                self.repo.audit("spawn_worker", job["id"], {"pid": pid})
            except Exception as error:
                owner = self.jobs.claim(job["id"])
                if owner:
                    self.jobs.finish(job["id"], owner, "failed", error=error)
                raise
        return self.jobs.get(job["id"])

    def freeze_backtest(self, payload):
        mode = "demo" if payload.get("mode") == "demo" else "real"
        start, end = payload.get("start"), payload.get("end")
        if not start or not end:
            raise ValueError("严格回测需要明确起止日期")
        if date.fromisoformat(start) > date.fromisoformat(end):
            raise ValueError("回测日期顺序无效")
        available = self.repo.snapshot_rows(mode, end)
        frames = []
        for day in sorted({r["trade_date"] for r in available if start <= r["trade_date"] <= end}):
            cutoff = timestamp(day + "T19:30:00+08:00")
            candidates = [r for r in available if r["trade_date"] == day and r["available_at"] and r["available_at"] <= cutoff]
            if candidates:
                row = max(candidates, key=lambda r: (r["available_at"], r["created_at"], r["digest"]))
                frames.append({"snapshot_id": row["digest"], "trade_date": day, "decision_time": cutoff})
        config = payload["config"]
        manifest = run_manifest(None, config, None, requested_start=start, requested_end=end)
        return self.repo.object({"frames": frames, "config": config, "template": payload.get("template", "mainline_pullback"),
            "start": start, "end": end, "manifest": manifest, "mode": mode,
            "walk_forward": bool(payload.get("walk_forward", False)), "stress": bool(payload.get("stress", False))}, "backtest-input")

    def execute(self, job_id):
        owner = self.jobs.claim(job_id)
        if not owner:
            return self.jobs.get(job_id)
        job = self.jobs.get(job_id)
        stop = threading.Event()
        def beat():
            while not stop.wait(15):
                try:
                    if not self.jobs.heartbeat(job_id, owner):
                        return
                except Exception:
                    return
        thread = threading.Thread(target=beat, daemon=True)
        thread.start()
        def progress(percent, message, checkpoint=None):
            self.jobs.progress(job_id, owner, percent, message, checkpoint)
        try:
            progress(job["progress"], "准备执行" )
            result = self._execute(job, progress)
            progress(99, "校验并保存任务产物")
            return self.jobs.finish(job_id, owner, "completed", result=result)
        except InterruptedError as error:
            return self.jobs.finish(job_id, owner, "cancelled", error=error)
        except Exception as error:
            self.alerts.emit("job:" + job_id, "研究任务未完成", str(error), "C")
            return self.jobs.finish(job_id, owner, "failed", error=f"{type(error).__name__}: {error}")
        finally:
            stop.set()
            thread.join(1)

    def _execute(self, job, progress):
        kind, payload = job["kind"], job["payload"]
        if kind == "migrate":
            return migrate(self.repo, progress, lambda: self.jobs.get(job["id"])["cancel_requested"], payload.get("parquet", False))
        if kind == "backup":
            result = backup(self.repo, progress=progress)
            result["retention"] = rotate_backups(self.repo)
            return result
        if kind == "collect":
            provider = FreeProvider(self.repo)
            result = provider.collect(job["trade_date"], progress, payload.get("options"))
            if result.status != "supported":
                return {"status": result.status, "reason": result.reason}
            dataset = result.data
            progress(96, "发布带时点与证据的标准快照")
            record = self.repo.register_snapshot(dataset, origin="free-v2", observed=True)
            self.warehouse.publish(dataset, record["snapshot_id"])
            # Preserve the old frontend's schema and all existing records.
            with self.snapshots.transaction():
                latest = self.snapshots.read()
                if latest["meta"].get("mode") == "demo" or latest["meta"]["trade_date"] <= dataset["meta"]["trade_date"]:
                    self.snapshots.save(legacy_view(dataset))
            return {**record, "provider": {k: v for k, v in result.as_dict().items() if k != "data"}}
        dataset = self.repo.read_object(payload["snapshot_id"]) if payload.get("snapshot_id") else None
        if kind == "ingest":
            return self.warehouse.publish(dataset, payload["snapshot_id"])
        if kind == "backfill":
            return self.backfill(job, dataset, progress)
        if kind in ("scan", "report"):
            dataset = self.enriched(dataset)
            snapshot = self.repo.register_snapshot(dataset, origin="history-enriched")
            progress(30, "计算范围、主线持续性与龙头证据")
            cutoff = timestamp(payload.get("decision_time") or utc_now())
            if cutoff > utc_now():
                raise ValueError("决策时点不能在未来")
            report = self.calculate(dataset, snapshot["snapshot_id"], payload["config"], cutoff)
            manifest = run_manifest(snapshot["snapshot_id"], payload["config"], report["context"]["decision_time"],
                session_calendar=dataset["meta"]["calendar"], synthetic=bool(dataset["meta"].get("synthetic")),
                account_input_id=self.repo.object(report["account_context"], "account-budget-input"),
                prior_report_ids=report["prior_report_ids"], frozen_at=utc_now())
            report.update(id=job["id"], manifest=manifest)
            progress(85, "冻结榜单、证据卡和运行版本")
            object_id = self.repo.object(report, "research-report")
            mode = report["context"]["mode"]
            self.repo.put("reports", job["id"], {"id": job["id"], "trade_date": job["trade_date"], "mode": mode,
                "snapshot_id": snapshot["snapshot_id"], "source_snapshot_id": payload["snapshot_id"], "object_id": object_id,
                "available_at": utc_now(), "config_hash": digest(payload["config"])}, immutable=True)
            for signal in report["candidates"]:
                self.repo.put("signals", signal["signal_id"], signal, immutable=True)
            self.write_report(report)
            next_days = [d for d in dataset["meta"].get("calendar", []) if d > job["trade_date"]]
            expiry_day = next_days[0] if next_days and calendar_visible(dataset["meta"], report["context"]["decision_time"]) else job["trade_date"]
            expiry = expiry_day + "T23:59:59+08:00"
            for account in ("demo",) if mode == "demo" else ("manual", "shadow"):
                portfolio = self.ledger.portfolio(dataset, account, asof=report["context"]["decision_time"])
                valuation_day = datetime.fromisoformat(portfolio["asof"]).astimezone(SHANGHAI).date().isoformat()
                value_id = f"{account}:{valuation_day}:{portfolio['ledger_digest']}:{snapshot['snapshot_id']}"
                self.repo.put("valuations", value_id, {"id": value_id, "account": account, "date": valuation_day,
                    "equity": portfolio["equity"], "net_contributions": portfolio["net_contributions"],
                    "incomplete": portfolio["incomplete"], "snapshot_id": snapshot["snapshot_id"]}, immutable=True)
                for position in portfolio["positions"]:
                    if position["overdue"]:
                        self.alerts.emit(f"overdue:{account}:{position['code']}:{job['trade_date']}", "持仓到期需复核", position["code"]+"已到最长持有期，T+1及停牌、跌停可能限制退出。", "A", valid_until=expiry)
                    for plan in position.get("exit_plans", []):
                        if plan.get("invalidation_price") and position["mark_price"] <= plan["invalidation_price"] and not position["valuation_stale"]:
                            self.alerts.emit(f"invalidation:{account}:{position['code']}:{job['trade_date']}", "持仓失效条件需复核", position["code"]+"收盘低于已记录失效价，请核对最新行情和可卖数量。", "A", valid_until=expiry)
            self.alerts.emit("report:" + job["id"], "盘后研究结果已保存",
                f"{job['trade_date']} · {len(report['candidates'])}只研究观察 · {report['context']['quality']['status']}", "C")
            return {"report_id": job["id"], "object_id": object_id, "quality": report["context"]["quality"]}
        if kind == "backtest":
            return self.backtest(job, progress)
        if kind == "shadow":
            return self.shadow(job, progress)
        raise ValueError("未识别的任务")

    def enriched(self, dataset):
        result = copy.deepcopy(dataset)
        day = result["meta"]["trade_date"]
        cached = self.repo.list("history_cache", 100000)
        by_code = {}
        for row in cached:
            receipt = row["result"]
            if receipt["status"] == "supported" and row.get("end") == day:
                bars = self.repo.read_object(row["data_id"])
                receipt = {**receipt, "data": bars[-180:], "first_date": bars[0]["date"], "rows": len(bars)}
                code = row.get("code") or receipt.get("metadata", {}).get("code")
                if code and (code not in by_code or receipt["rows"] > by_code[code]["rows"]):
                    by_code[code] = receipt
        for stock in result["stocks"]:
            receipt = by_code.get(stock["code"])
            if not receipt or not stock["bars"]:
                continue
            current = stock["bars"][-1]
            bars = receipt["data"]
            if current["date"] != day or any(abs(current[k]-bars[-1][k]) > .0051 for k in ("open", "high", "low", "close")):
                continue
            known = receipt["available_at"]
            enriched = copy.deepcopy(bars)
            for bar in enriched:
                bar["provenance"] = {"provider_id": receipt["provider_id"], "source_family": receipt["source_family"],
                    "adapter_version": "range-1.0.0", "event_time": timestamp(bar["date"] + "T15:00:00+08:00"),
                    "available_at": known, "known_at": known, "ingested_at": known, "precision": "observed",
                    "verified_fields": [], "evidence_digest": receipt.get("evidence_digest")}
            enriched[-1] = {**current, "adj_factor": bars[-1]["adj_factor"], "provenance": {**enriched[-1]["provenance"], "verified_fields": current.get("provenance", {}).get("verified_fields", [])}}
            stock["bars"] = enriched
            stock["listed_before"] = min(stock.get("listed_before") or receipt["first_date"], receipt["first_date"])
            result["meta"]["calendar"] = sorted(set(result["meta"]["calendar"]) | {b["date"] for b in bars})
            if result["meta"].get("available_at"):
                result["meta"]["available_at"] = max(timestamp(result["meta"]["available_at"]), timestamp(known))
        return result

    def backfill(self, job, dataset, progress):
        payload, day = job["payload"], job["trade_date"]
        codes = payload["codes"]
        cursor = int(job["checkpoint"].get("cursor", 0))
        selected = payload["batch_codes"]
        receipts = list(job["checkpoint"].get("receipts", []))
        provider = FreeProvider(self.repo)
        for index in range(cursor, len(selected)):
            code = selected[index]
            progress(int(index/max(1,len(selected))*90), f"独立历史队列 {index+1}/{len(selected)} · {code}", {"cursor": index, "receipts": receipts})
            req = HistoryRequest(code=code, start=date.fromisoformat(payload.get("start") or (date.fromisoformat(day)-timedelta(days=90)).isoformat()),
                end=date.fromisoformat(day), warmup=int(payload.get("warmup", 120)), reason=payload["reason"])
            result = provider.get_history(req)
            receipts.append({"code": code, "status": result.status, "reason": result.reason, "provider": result.provider_id,
                "bars": len(result.data or []), "evidence_digest": result.evidence_digest, "metadata": result.metadata})
            progress(int((index+1)/max(1,len(selected))*90), f"已记录{index+1}/{len(selected)}个历史结果", {"cursor": index+1, "receipts": receipts})
        if payload["reason"] == "seed":
            self.repo.put("queue_cursors", "neutral", {"cursor": (payload["queue_start"]+len(selected)) % max(1,len(codes)), "date": day})
        return {"requested": len(selected), "success": sum(r["status"] == "supported" for r in receipts), "receipts": receipts,
            "sampling": "按证券代码，不按当天强弱；失败与覆盖不足保留"}

    def backtest(self, job, progress):
        from .strict_backtest import experiment_summary, replay
        frozen = self.repo.read_object(job["payload"]["input_id"])
        descriptors = frozen["frames"]
        loader = lambda item: self.repo.read_object(item["snapshot_id"])
        result = replay(descriptors, frozen["config"], frozen["template"], progress,
            lambda: self.jobs.get(job["id"])["cancel_requested"], loader=loader,
            requested_start=frozen["start"], requested_end=frozen["end"])
        result["evaluation"] = experiment_summary(result, frozen["config"]["holding_days"])
        if frozen["stress"] and result["status"] == "completed":
            result["stress"] = []
            for multiple in (2, 3):
                cfg = {**frozen["config"], "commission_bps": frozen["config"]["commission_bps"]*multiple,
                    "minimum_commission": frozen["config"]["minimum_commission"]*multiple,
                    "slippage_bps": frozen["config"]["slippage_bps"]*multiple}
                stress = replay(descriptors, cfg, frozen["template"], lambda p,m: progress(90, f"成本压力{multiple}倍 · {m}"), loader=loader)
                result["stress"].append({"cost_multiple": multiple, "status": stress["status"], "metrics": stress["metrics"], "failure": stress.get("failure")})
        if frozen["walk_forward"]:
            folds = []
            for cursor in range(120, len(descriptors), 60):
                window = descriptors[cursor:min(cursor+60, len(descriptors))]
                if len(window) < 20:
                    continue
                fold = replay(window, frozen["config"], frozen["template"], lambda p,m: progress(95, "滚动验证 · " + m), loader=loader)
                folds.append({"start": window[0]["trade_date"], "end": window[-1]["trade_date"], "metrics": fold["metrics"], "status": fold["status"]})
            result["walk_forward"] = {"locked_config": True, "history_sessions": 120, "test_sessions": 60, "folds": folds,
                "note": "固定配置滚动样本外运行，不使用测试窗寻优；窗口不足不伪造结果"}
        result.update(id=job["id"], manifest=frozen["manifest"], input_id=job["payload"]["input_id"])
        object_id = self.repo.object(result, "backtest-result")
        record = {"id": job["id"], "input_id": job["payload"]["input_id"], "object_id": object_id, "status": result["status"],
            "template": frozen["template"], "config_hash": digest(frozen["config"]), "mode": frozen["mode"],
            "start": frozen["start"], "end": frozen["end"], "metrics": result["metrics"], "replayed_from": job["payload"].get("replay_id")}
        self.repo.put("runs", job["id"], record, immutable=True)
        self.repo.put("experiments", job["id"], {**record, "hypothesis": job["payload"].get("hypothesis", "锁定规则的盘后主线研究"), "evaluation": result["evaluation"]}, immutable=True)
        return {"run_id": job["id"], "object_id": object_id, "status": result["status"]}

    def shadow(self, job, progress):
        cfg = job["payload"]["config"]
        code = code_manifest()
        series = self.repo.get("shadow_series", "active")
        if series and (series["config_hash"] != digest(cfg) or series.get("code_hash") != code["code_hash"] or series.get("engine_version") != ENGINE_VERSION):
            raise Conflict("影子账户配置或引擎已改变；旧序列保留，请使用新的独立data目录建立研究账户")
        published = {}
        for row in self.repo.list("reports", 100000):
            if row["mode"] != "real" or row["trade_date"] > job["trade_date"] or row["config_hash"] != digest(cfg):
                continue
            report = self.repo.read_object(row["object_id"])
            manifest = report["manifest"]
            if manifest["engine_version"] != ENGINE_VERSION or manifest["code"]["code_hash"] != code["code_hash"]:
                continue
            cutoff = report["context"]["decision_time"]
            decision_day = datetime.fromisoformat(cutoff).astimezone(SHANGHAI).date().isoformat()
            saved_day = datetime.fromisoformat(row["_saved_at"]).astimezone(SHANGHAI).date().isoformat()
            if decision_day != row["trade_date"] or saved_day != row["trade_date"]:
                continue
            old = published.get(row["trade_date"])
            if not old or (row["_saved_at"], row["id"]) > (old["saved_at"], old["report_id"]):
                published[row["trade_date"]] = {"trade_date": row["trade_date"], "snapshot_id": row["snapshot_id"],
                    "decision_time": cutoff, "report_id": row["id"], "saved_at": row["_saved_at"]}
        descriptors = [published[d] for d in sorted(published)]
        if not descriptors:
            return {"status": "BLOCKED", "reason": "没有当前引擎当日及时发布的冻结研究；补做旧日报告不生成历史影子成交", "recorded_trades": 0}
        start = descriptors[0]["trade_date"]
        manifest = run_manifest(None, cfg, None, source="published-report-shadow",
            published_report_ids=[d["report_id"] for d in descriptors])
        input_id = self.repo.object({"frames": descriptors, "config": cfg, "start": start, "end": job["trade_date"],
            "template": "mainline_pullback", "mode": "real", "walk_forward": False, "stress": False, "manifest": manifest}, "shadow-input")
        synthetic_job = {**job, "payload": {**job["payload"], "input_id": input_id}}
        summary = self.backtest(synthetic_job, progress)
        result = self.repo.read_object(summary["object_id"])
        if result["status"] != "completed":
            return {**summary, "reason": result.get("failure"), "recorded_trades": 0}
        self.repo.put("shadow_series", "active", {"id": "active", "config_hash": digest(cfg), "code_hash": code["code_hash"],
            "engine_version": ENGINE_VERSION, "started_on": start}, immutable=True)
        count = 0
        for order in result["orders"]:
            receipt = digest({k: order.get(k) for k in ("date", "code", "side", "qty", "price", "signal_id")})
            self.ledger.append_trade({"account": "shadow", "code": order["code"], "side": order["side"], "qty": order["qty"],
                "price": order["price"], "fees": order["fees"], "executed_at": order["executed_at"], "signal_id": order.get("signal_id"),
                "receipt": receipt, "note": order["reason"]})
            count += 1
        return {**summary, "recorded_trades": count, "account": "shadow"}

    def write_report(self, report):
        folder = self.repo.home / "reports" / report["id"]
        folder.mkdir(parents=True, exist_ok=True)
        atomic_json(folder / "report.json", report)
        lines = [f"# 涨势盘后研究 {report['context']['trade_date']}", "", f"质量：{report['context']['quality']['status']}；环境：{report['market']['state']}",
            "", report["scope"], "", "## 主线"]
        lines += [f"- {s['name']} · {s['type']} 第{s['rank']} · 强度{s['score']} · {'持续主线' if s['persistent'] else '当日强势/持续性待确认'}" for s in report["themes"] if s["rank"] <= 3]
        lines += ["", "## 龙头研究观察"]
        for c in report["candidates"][:10]:
            lines += [f"- {c['name']} {c['code']} · {c['role']} · {c['score']}分", "  依据：" + "；".join(c["reasons"]),
                "  未满足：" + "；".join(c["exclusions"]), "  风险：" + "；".join(c["risk_flags"])]
        lines += ["", "## 数据限制"] + ["- " + r for r in report["context"]["quality"]["reasons"]]
        lines += ["", "研究结果用于观察，未启用自动委托；实际成交在独立账本登记。"]
        (folder / "report.md").write_text("\n".join(lines), encoding="utf-8")

    def forward_status(self):
        reports = self.repo.list("reports", 100000)
        groups = {}
        for row in reports:
            if row["mode"] != "real":
                continue
            value = self.repo.read_object(row["object_id"])
            if value["manifest"].get("synthetic"):
                continue
            key = digest({"config": value["manifest"]["config_hash"], "code": value["manifest"]["code"]["code_hash"], "engine": value["manifest"]["engine_version"]})
            item = groups.setdefault(key, {"series": key, "dates": set(), "eligible_dates": set(), "calendar": set(), "config_hash": row["config_hash"], "code_hash": value["manifest"]["code"]["code_hash"]})
            item["calendar"].update(value["manifest"].get("session_calendar", []))
            item["dates"].add(row["trade_date"])
            # Only reports actually available before the next session can count.
            decision = datetime.fromisoformat(value["context"]["decision_time"]).astimezone(SHANGHAI)
            saved_day = datetime.fromisoformat(row["_saved_at"]).astimezone(SHANGHAI).date().isoformat()
            if saved_day == row["trade_date"] and decision.date().isoformat() == row["trade_date"] and value["context"]["quality"]["status"] == "READY":
                item["eligible_dates"].add(row["trade_date"])
        output = []
        for g in groups.values():
            streak = longest = 0
            for d in sorted(x for x in g["calendar"] if min(g["dates"]) <= x <= max(g["dates"])):
                streak = streak+1 if d in g["eligible_dates"] else 0
                longest = max(longest, streak)
            output.append({**{k:v for k,v in g.items() if k not in ("dates", "eligible_dates", "calendar")},
                "observed_dates": sorted(g["dates"]), "eligible_dates": sorted(g["eligible_dates"]),
                "eligible_sessions": len(g["eligible_dates"]), "consecutive_sessions": streak, "longest_streak": longest,
                "required_sessions": 30, "complete": streak >= 30, "strategy_validated": False})
        return output
