"""On-demand history for any quoted stock, independent of candidate screening."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .analytics import stock_detail
from .data import atomic_json, now_iso, validate_dataset


def quote_identity(dataset, code):
    stock = next((s for s in dataset["stocks"] if s["code"] == code), None)
    if not stock or not stock["bars"] or stock["bars"][-1]["date"] != dataset["meta"]["trade_date"]:
        raise ValueError("该证券没有目标交易日的有效报价")
    current = stock["bars"][-1]
    payload = [dataset["meta"]["trade_date"], dataset["meta"].get("fetched_at"), dataset["meta"].get("mode"),
               code, {k:v for k,v in current.items() if k != "adj_factor"}, stock.get("verification")]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def merge_history(dataset, code, bars, source, evidence=None):
    """Validate before replacing anything; preserve the independently dated quote."""
    result = copy.deepcopy(dataset)
    stock = next((s for s in result["stocks"] if s["code"] == code), None)
    if not stock or not stock["bars"]:
        raise ValueError("证券不存在或没有收盘行情")
    day = result["meta"]["trade_date"]
    current = copy.deepcopy(stock["bars"][-1])
    if not bars or bars[-1]["date"] != day or current["date"] != day:
        raise ValueError("历史日线未更新到目标交易日")
    if any(abs(bars[-1][key]-current[key]) > .005001 for key in ("open", "high", "low", "close")):
        raise ValueError("历史末根OHLC与已核验的当日行情冲突")
    if any(b.get("adj_factor") is None for b in bars):
        raise ValueError("历史复权因子不完整，不能拼接为前复权日线")
    current["adj_factor"] = bars[-1]["adj_factor"]
    stock["bars"] = copy.deepcopy(bars[:-1]) + [current]
    # This proves history existed by this date, not the actual listing date.
    stock["listed_before"] = min(stock.get("listed_before") or bars[0]["date"], bars[0]["date"])
    stock["history_fetch"] = {"status": "complete", "date": day, "source": source, "sessions": len(bars),
                              "fetched_at": now_iso(), "evidence_directory": evidence,
                              "note": "当日OHLC已与快照核对；历史逐日价格未完成跨源认证"}
    result["meta"]["calendar"] = sorted(set(result["meta"]["calendar"]) | {b["date"] for b in bars})
    result["meta"]["history_updated_at"] = now_iso()
    result["meta"]["history_on_demand"] = sorted(set(result["meta"].get("history_on_demand", [])) | {code})
    return validate_dataset(result)


def fetch_history(dataset, code, root, progress=lambda *_: None, validate_enriched=None):
    from .source_adapters import history_chain
    day = dataset["meta"]["trade_date"]
    cache = root / "history"
    cache.mkdir(parents=True, exist_ok=True)
    evidence = root / "evidence" / f"history-{day}-{code}-{secrets.token_hex(4)}"
    evidence.mkdir(parents=True)
    relative = str(evidence.relative_to(root))
    def validate(bars,name):
        enriched=merge_history(dataset,code,bars,name,relative)
        if validate_enriched: validate_enriched(enriched)
    bars,provider,failures=history_chain(code,day,root,evidence,validate,progress)
    atomic_json(evidence / "validated-bars.json", bars)
    atomic_json(evidence / "result.json", {"code":code,"date":day,"source":provider,
        "completed_at":now_iso(),"sessions":len(bars),"previous_attempts":failures})
    return bars,provider,relative


class StockHistoryService:
    def __init__(self, snapshots, lock=None):
        self.snapshots = snapshots
        self.lock = lock or threading.RLock()
        self.jobs = {}
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="stock-history")

    def _key(self, dataset, code):
        return (code, quote_identity(dataset, code))

    def status(self, code, mode="active", day=None):
        if mode not in ("active", "demo"):
            raise ValueError("无效的数据模式")
        with self.lock:
            dataset = self.snapshots.read(mode)
            if day and dataset["meta"]["trade_date"] != day:
                raise ValueError("行情日期已切换，请重新打开股票")
            detail = stock_detail(dataset, code)
            key = self._key(dataset, code)
            return {"job": dict(self.jobs.get(key, {"state": "idle"})), "detail": detail}

    def start(self, payload):
        code, mode = payload.get("code", ""), payload.get("mode", "active")
        if mode not in ("active", "demo"):
            raise ValueError("无效的数据模式")
        if mode == "demo":
            raise ValueError("演示数据不请求真实历史")
        if not isinstance(code, str) or not re.fullmatch(r"(?:6\d{5}\.SH|[03]\d{5}\.SZ)", code):
            raise ValueError("证券代码格式无效")
        with self.lock:
            dataset = self.snapshots.read(mode)
            if dataset["meta"].get("mode") == "demo":
                raise ValueError("请先同步真实行情")
            day = dataset["meta"]["trade_date"]
            if payload.get("date") and day != payload["date"]:
                raise ValueError("行情日期已切换，请重新打开股票")
            key = self._key(dataset, code)
            if not stock_detail(dataset, code)["needs_history"]:
                return {"job": {"state": "done", "message": "已有可用历史行情"}, "detail": stock_detail(dataset, code)}
            old = self.jobs.get(key)
            if old and old["state"] in ("queued", "running"):
                return {"job": dict(old)}
            if sum(j["state"] in ("queued", "running") for j in self.jobs.values()) >= 12:
                raise ValueError("历史补齐队列较忙，请稍后重试")
            job = {"state": "queued", "message": "已排队，正在准备历史行情", "code": code, "date": day, "started_at": now_iso()}
            self.jobs[key] = job
            # Keep requests bounded over a long-running desktop session.
            if len(self.jobs) > 128:
                self.jobs = {k:v for k,v in self.jobs.items() if v["state"] in ("queued", "running") or k == key}
            original = copy.deepcopy(dataset)

        def progress(message):
            with self.lock:
                job.update(state="running", message=message)

        def worker():
            try:
                bars, source, evidence = fetch_history(original, code, self.snapshots.root, progress)
                with self.lock:
                    latest = self.snapshots.read()
                    if self._key(latest, code) != key:
                        raise ValueError("请求期间行情快照已改变，历史缓存已保留；请重新打开股票")
                    # Merge into the latest snapshot, retaining other completed stocks.
                    enriched = merge_history(latest, code, bars, source, evidence)
                    filename = self.snapshots.save(enriched)
                    job.update(state="done", message=f"历史补齐完成：{len(bars)}根日线", snapshot=filename, finished_at=now_iso())
            except Exception as error:
                with self.lock:
                    job.update(state="error", message=f"历史暂未补齐：{str(error)[:420]}。现有行情已保留，可重试。", finished_at=now_iso())
        self.pool.submit(worker)
        return {"job": dict(job)}

    def close(self):
        self.pool.shutdown(wait=False, cancel_futures=True)
