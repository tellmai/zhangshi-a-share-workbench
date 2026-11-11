"""Replaceable providers with explicit capability and immutable fetch receipts."""
from __future__ import annotations

import json
import hashlib
import os
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Protocol

from .contracts import HistoryRequest, canonical, digest, in_universe, timestamp, utc_now
from .data import atomic_json, validate_dataset
from .quality import normalize


@dataclass
class ProviderResult:
    status: str
    data: object = None
    reason: str | None = None
    provider_id: str = ""
    source_family: str = ""
    available_at: str | None = None
    evidence_digest: str | None = None
    metadata: dict = field(default_factory=dict)

    def as_dict(self):
        from dataclasses import asdict
        return asdict(self)


class MarketDataProvider(Protocol):
    def get_trade_calendar(self, start: str, end: str) -> ProviderResult: ...
    def get_securities(self, day: str) -> ProviderResult: ...
    def get_daily_bars(self, day: str) -> ProviderResult: ...
    def get_limit_prices(self, day: str) -> ProviderResult: ...
    def get_limit_events(self, day: str) -> ProviderResult: ...
    def get_theme_membership(self, day: str) -> ProviderResult: ...
    def get_history(self, request: HistoryRequest) -> ProviderResult: ...
    def get_minute_bars(self, codes: list[str], start: str, end: str) -> ProviderResult: ...


class FreeProvider:
    id = "public-free-chain"
    family = "腾讯/新浪/东方财富，按字段保留实际上游"

    def __init__(self, repository, snapshot=None):
        self.repo, self.snapshot = repository, snapshot

    def unsupported(self, reason):
        return ProviderResult("unavailable", reason=reason, provider_id=self.id, source_family=self.family)

    def get_trade_calendar(self, start, end):
        records = self.repo.list("calendars", 100)
        covered = [r for r in records if r["start"] <= start and r["end"] >= end and r.get("verified")]
        if not covered:
            from .source_adapters import run_worker
            try:
                raw=run_worker({"provider":"baostock","action":"calendar","start":start,"end":end,"date":end},30)
                rows=raw.get("rows",[])
                if not rows or rows[0].get("calendar_date") != start or rows[-1].get("calendar_date") != end:
                    raise ValueError("日历未覆盖声明的完整区间")
                receipt=self.repo.object(raw,"calendar-evidence")
                value=register_calendar(self.repo,{"start":start,"end":end,"sessions":raw['sessions'],
                    "source":raw['source'],"known_at":utc_now(),"verified":True,
                    "evidence_note":"供应商日历字段和完整区间校验通过；原始证据 "+receipt})
                covered=[value]
            except Exception as error:
                return self.unsupported("日历接口未通过："+str(error)[:250]+"；可导入已核验日历")
        row = max(covered, key=lambda r: r["known_at"])
        return ProviderResult("supported", [d for d in row["sessions"] if start <= d <= end], provider_id=row["source"], source_family=row["source"], available_at=row["known_at"], metadata={"calendar_id": row["id"]})

    def collect(self, day, progress=lambda *_: None, options=None):
        from .free_provider import fetch_free
        from .review import trading_day
        if trading_day(day) is False:
            return ProviderResult("unavailable", reason="已知休市日，跳过采集", provider_id=self.id)
        # Price-based history acquisition remains available in the old workflow only.
        policy = {"date": day, "industry_limit": 100, "theme_limit": 30, "history_limit": 0, **(options or {})}
        policy["history_limit"] = 0
        raw = fetch_free(policy, self.repo.root, progress)
        raw["meta"]["security_master_complete"] = raw["meta"].get("complete") is True
        raw["meta"]["security_master_known_at"] = utc_now()
        raw["meta"]["history_sampling"] = "disabled; separate neutral symbol queue"
        raw["meta"].setdefault("notes", []).append("V2基础采集不按涨幅补齐历史；证券代码队列独立积累历史")
        value = normalize(raw, observed=True)
        calendar = self.get_trade_calendar(day[:4] + "-01-01", day[:4] + "-12-31")
        if calendar.status == "supported" and day in calendar.data:
            value["meta"]["calendar"] = sorted(set(value["meta"]["calendar"]) | set(calendar.data))
            value["meta"].update(calendar_verified=True, calendar_source=calendar.provider_id, calendar_known_at=calendar.available_at)
        evidence_files = {}
        if raw["meta"].get("evidence_directory"):
            folder = self.repo.safe_path(raw["meta"]["evidence_directory"])
            for path in folder.rglob("*"):
                if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(self.repo.root):
                    evidence_files[str(path.relative_to(self.repo.root))] = hashlib.sha256(path.read_bytes()).hexdigest()
        receipt = self.repo.object({"request": {"date": day, "options": policy}, "response": raw,
            "raw_file_sha256": evidence_files, "fetched_at": utc_now(), "adapter": "free-v2.0.0"}, "provider-evidence")
        value["meta"]["evidence_digest"] = receipt
        for stock in value["stocks"]:
            for bar in stock.get("bars", []):
                if not bar["provenance"].get("evidence_digest"):
                    bar["provenance"]["evidence_digest"] = receipt
        value["meta"]["available_at"] = utc_now()
        self.snapshot = value
        return ProviderResult("supported", value, provider_id=self.id, source_family=self.family,
            available_at=value["meta"]["available_at"], evidence_digest=receipt,
            metadata={"independent_verification": "close-only", "history_limit": 0})

    def _slice(self, day, kind):
        value = self.snapshot
        if not value or value["meta"]["trade_date"] != day:
            return self.unsupported("未取得该日采集快照，不用当前数据回填历史")
        if kind == "securities":
            data = [{k: v for k, v in s.items() if k != "bars"} for s in value["stocks"]]
        elif kind == "bars":
            data = [{"code": s["code"], **b} for s in value["stocks"] for b in s["bars"] if b["date"] == day]
        elif kind == "limits":
            data = [{"code": s["code"], "up_limit": b.get("up_limit"), "down_limit": b.get("down_limit")} for s in value["stocks"] for b in s["bars"] if b["date"] == day]
        elif kind == "events":
            data = [{"code": s["code"], **s["limit"]} for s in value["stocks"] if s.get("limit")]
        else:
            data = value.get("sectors", [])
        return ProviderResult("supported" if data else "unavailable", data,
            reason=None if data else "本次采样未取得该数据集", provider_id=self.id,
            source_family=self.family, available_at=value["meta"].get("available_at"))

    def get_securities(self, day): return self._slice(day, "securities")
    def get_daily_bars(self, day): return self._slice(day, "bars")
    def get_limit_prices(self, day): return self._slice(day, "limits")
    def get_limit_events(self, day): return self._slice(day, "events")
    def get_theme_membership(self, day): return self._slice(day, "memberships")
    def get_minute_bars(self, codes, start, end): return self.unsupported("免费适配尚无已核验分钟及竞价时间契约，盘中确认禁用")
    def get_orderbook(self, codes, at): return self.unsupported("未接入逐笔与委托队列，不模拟排队成交")
    def get_corporate_actions(self, start, end): return self.unsupported("未取得可核实公司行为流水")

    def get_history(self, request):
        req = request if isinstance(request, HistoryRequest) else HistoryRequest.model_validate(request)
        # Over-fetch for holidays; exact warmup coverage is checked below.
        beginning = req.start - timedelta(days=max(30, req.warmup * 2 + 20))
        output, failures = None, []
        for provider in ("eastmoney", "tencent"):
            started = utc_now()
            key = digest({"provider": provider, "request": req.model_dump(mode="json"), "adapter": "range-1.0.0"})
            cached = self.repo.get("history_cache", key)
            if cached:
                saved = dict(cached["result"])
                saved["data"] = self.repo.read_object(cached["data_id"])
                return ProviderResult(**saved)
            health = self.repo.get("provider_health", provider, {})
            if health.get("until", 0) > time.time():
                failures.append({"provider": provider, "error": "连续失败后的熔断窗口"})
                continue
            try:
                bars, evidence = self._range(provider, req.code, beginning.isoformat(), req.end.isoformat())
                need = sum(b["date"] <= req.start.isoformat() for b in bars)
                if need < req.warmup or not bars or bars[-1]["date"] < req.end.isoformat():
                    raise ValueError(f"历史覆盖不足：起点前{need}根，需要{req.warmup}根；末日{bars[-1]['date'] if bars else '无'}")
                validate_dataset({"schema_version": 1, "meta": {"trade_date": req.end.isoformat(), "source": provider,
                    "calendar": [b["date"] for b in bars]}, "stocks": [{"code": req.code, "name": req.code, "bars": bars}], "sectors": []}, max_bars=20000)
                if any(not b.get("adj_factor") for b in bars):
                    raise ValueError("复权序列不完整")
                result = ProviderResult("supported", bars, provider_id=provider,
                    source_family="东方财富" if provider == "eastmoney" else "腾讯", available_at=utc_now(),
                    evidence_digest=self.repo.object({"request": req.model_dump(mode="json"), "started_at": started,
                        "response": evidence, "failures": failures}, "range-history"),
                    metadata={"code": req.code, "historical_available_at": None, "historical_limits": "unknown", "warmup_bars": need,
                        "reason": req.reason, "source_consistency": "single-provider-raw-and-adjusted"})
                data_id = self.repo.object(bars, "history-series")
                from .warehouse import Warehouse
                partition = Warehouse(self.repo).publish_history(req.code, bars, result.as_dict())
                self.repo.put("history_cache", key, {"id": key, "code": req.code, "end": req.end.isoformat(),
                    "rows": len(bars), "data_id": data_id, "parquet_id": partition["id"], "result": {**result.as_dict(), "data": None}}, immutable=True)
                self.repo.put("provider_health", provider, {"id": provider, "failures": 0, "until": 0})
                return result
            except Exception as error:
                failures.append({"provider": provider, "error": str(error)[:300]})
                count = health.get("failures", 0) + 1
                self.repo.put("provider_health", provider, {"id": provider, "failures": count, "until": time.time() + 120 if count >= 3 else 0})
        return ProviderResult("unavailable", reason="所有区间历史通道均未通过覆盖检查", provider_id=self.id,
            metadata={"failures": failures, "request": req.model_dump(mode="json")})

    def _range(self, provider, code, start, end):
        from .free_provider import get_json, number, share_volume, normalization_record
        evidence, series = {}, {}
        if provider == "eastmoney":
            for adjust in (0, 1):
                payload = get_json("https://push2his.eastmoney.com/api/qt/stock/kline/get", {
                    "secid": ("1." if code.endswith(".SH") else "0.") + code[:6], "klt": 101, "fqt": adjust,
                    "beg": start.replace("-", ""), "end": end.replace("-", ""), "lmt": 10000,
                    "fields1": "f1,f2,f3,f4,f5,f6", "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"})
                evidence[str(adjust)] = payload
                series[adjust] = [r.split(",") for r in (payload.get("data") or {}).get("klines", [])]
                time.sleep(1)
            adjusted = {r[0]: float(r[2]) for r in series[1] if len(r) >= 11}
            bars = []
            for row in series[0]:
                if len(row) < 11 or not start <= row[0] <= end:
                    continue
                dt, op, close, high, low, volume, amount, _, pct, _, turnover = row[:11]
                op, close, high, low, volume, amount, pct, turnover = map(float, (op, close, high, low, volume, amount, pct, turnover))
                if min(op, close, high, low) <= 0 or pct <= -100:
                    continue
                bars.append(dict(date=dt, open=op, high=high, low=low, close=close, pre_close=close/(1+pct/100),
                    volume_shares=share_volume(volume, amount, low, high), amount_yuan=amount, turnover_rate=turnover,
                    adj_factor=adjusted[dt]/close if adjusted.get(dt) else None))
                bars[-1]["normalization"] = normalization_record(volume, amount, bars[-1]["volume_shares"], amount)
            return sorted(bars, key=lambda b: b["date"]), evidence
        symbol = ("sh" if code.endswith(".SH") else "sz") + code[:6]
        # Tencent caps a single window; traverse older windows with overlap and
        # require identical raw and adjusted overlap before concatenating.
        cursor, merged, factors = end, {}, {}
        for _ in range(40):
            window = {}
            for adjust in ("", "qfq"):
                payload = get_json("https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get", {"param": f"{symbol},day,,{cursor},640,{adjust}"})
                evidence[cursor + ":" + adjust] = payload
                content = (payload.get("data") or {}).get(symbol, {})
                window[adjust] = content.get(adjust + "day") or content.get("day") or []
                time.sleep(1)
            raw, adjusted = window[""], {r[0]: float(r[2]) for r in window["qfq"] if len(r) >= 5}
            if not raw:
                break
            oldest = raw[0][0]
            for i, row in enumerate(raw):
                if i == 0 or len(row) < 9 or row[0] > end:
                    continue
                dt, op, close, high, low, volume = row[:6]
                op, close, high, low, volume = map(float, (op, close, high, low, volume))
                if min(op, close, high, low, volume) <= 0:
                    continue
                amount = float(row[8]) * 10000
                factor = adjusted.get(dt, 0) / close
                prev_adj = adjusted.get(raw[i-1][0])
                pre_close = close / (adjusted[dt]/prev_adj) if adjusted.get(dt) and prev_adj else float(raw[i-1][2])
                bar = dict(date=dt, open=op, high=high, low=low, close=close, pre_close=pre_close,
                    volume_shares=share_volume(volume, amount, low, high), amount_yuan=amount,
                    turnover_rate=number(row[7]), adj_factor=factor or None)
                bar["normalization"] = normalization_record(volume, float(row[8]), bar["volume_shares"], amount)
                if dt in merged and (any(abs(merged[dt][k]-bar[k]) > .0051 for k in ("open", "high", "low", "close"))
                        or abs((factors[dt] or 0) - factor) > .011/close):
                    raise ValueError("腾讯分段复权锚或原始价格不一致，不拼接")
                merged[dt], factors[dt] = bar, factor
            if oldest <= start or oldest >= cursor:
                break
            cursor = raw[2][0] if len(raw) > 3 else oldest
        return [merged[d] for d in sorted(merged) if start <= d <= end], evidence


def register_calendar(repository, value):
    required = {"start", "end", "sessions", "source", "known_at", "verified"}
    if not isinstance(value, dict) or not required.issubset(value):
        raise ValueError("日历需提供start,end,sessions,source,known_at,verified")
    start, end = date.fromisoformat(value["start"]), date.fromisoformat(value["end"])
    if start > end or type(value["verified"]) is not bool or not value["source"]:
        raise ValueError("日历范围或来源无效")
    sessions = value["sessions"]
    if not isinstance(sessions, list) or sessions != sorted(set(sessions)) or not sessions:
        raise ValueError("日历会话须有序、非空、不重复")
    if any(not start <= date.fromisoformat(d) <= end for d in sessions):
        raise ValueError("日历会话超出声明范围")
    known = timestamp(value["known_at"])
    if known > utc_now():
        raise ValueError("日历获知时间不能在未来")
    row = {k: value[k] for k in required}
    row["known_at"] = known
    row["id"] = digest(row)
    row["evidence_note"] = str(value.get("evidence_note", "用户核验导入；不自动认证来源"))[:1000]
    return repository.put("calendars", row["id"], row, immutable=True)
