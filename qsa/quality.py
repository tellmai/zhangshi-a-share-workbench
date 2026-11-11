"""Capability-specific quality: a matched close never certifies other fields."""
from __future__ import annotations

import copy
from datetime import datetime

from .contracts import Provenance, board_of, in_universe, timestamp, utc_now
from .data import validate_dataset


def normalize(dataset, observed=False):
    value = copy.deepcopy(dataset)
    original = value.get("schema_version")
    value["schema_version"] = 1
    validate_dataset(value, max_bars=20000)
    value["schema_version"] = 2
    meta = value["meta"]
    now = utc_now()
    available = timestamp(meta["available_at"]) if meta.get("available_at") else (now if observed else None)
    meta.update(exchange_timezone="Asia/Shanghai", original_schema_version=original)
    meta["available_at"] = available
    meta.setdefault("ingested_at", now)
    event = meta["trade_date"] + "T15:00:00+08:00"
    provider = meta.get("source", "unknown")
    for stock in value["stocks"]:
        try:
            stock["board_id"] = board_of(stock["code"])
        except ValueError:
            stock["board_id"] = "OTHER"
        for bar in stock.get("bars", []):
            if "provenance" in bar:
                bar["provenance"] = Provenance.model_validate(bar["provenance"]).model_dump()
            else:
                current = bar["date"] == meta["trade_date"]
                verified = ["close"] if current and stock.get("verification", {}).get("status") == "matched" else []
                bar["provenance"] = Provenance(provider_id=provider, source_family=provider,
                    event_time=bar["date"] + "T15:00:00+08:00",
                    available_at=available if current else None, known_at=available if current else None,
                    ingested_at=now, precision="observed" if current and observed else "unknown",
                    verified_fields=verified).model_dump()
        if observed and not stock.get("status_history"):
            bar = next((b for b in stock.get("bars", []) if b["date"] == meta["trade_date"]), None)
            if bar and "is_st" in stock:
                stock["status_history"] = [{"valid_from": meta["trade_date"], "valid_to": None,
                    "known_at": available, "is_st": stock["is_st"], "halted": False if bar["volume_shares"] > 0 else None,
                    "board": stock["board_id"], "source": provider, "basis": "当日可见简称与成交报价；不回填更早状态"}]
    for sector in value.get("sectors", []):
        sector.setdefault("valid_from", sector["asof_date"])
        sector.setdefault("valid_to", None)
        sector.setdefault("known_at", available)
        sector.setdefault("available_at", available)
    if observed:
        meta["calendar_verified"] = bool(meta.get("calendar_verified", False))
    return value


def legacy_view(dataset):
    value = copy.deepcopy(dataset)
    value["schema_version"] = 1
    return value


def calendar_visible(meta, at=None):
    if not meta.get("calendar_verified") or not meta.get("calendar_known_at"):
        return False
    try:
        return timestamp(meta["calendar_known_at"]) <= timestamp(at or utc_now())
    except (ValueError, TypeError):
        return False


def assess(dataset, boards=("MAIN", "CHINEXT"), decision_time=None):
    errors, reasons = [], []
    try:
        value = legacy_view(dataset)
        validate_dataset(value, max_bars=20000)
    except (ValueError, TypeError, KeyError) as error:
        return {"status": "BLOCKED", "reasons": [str(error)], "capabilities": {}, "coverage": {}}
    day = value["meta"]["trade_date"]
    demo = value["meta"].get("mode") == "demo"
    pool = [s for s in value["stocks"] if in_universe(s, boards, demo)]
    quoted, limits, conflicts, historical, known_status, timed, halted = 0, 0, 0, 0, 0, 0, 0
    for stock in pool:
        bars = stock.get("bars", [])
        current = bars[-1] if bars and bars[-1]["date"] == day else None
        if stock.get("verification", {}).get("status") == "conflict":
            conflicts += 1
            continue
        if current:
            quoted += 1
            limits += bool(current.get("up_limit") and current.get("down_limit"))
            stamp = current.get("provenance", {}).get("available_at")
            if stamp:
                try:
                    timed += decision_time is None or timestamp(stamp) <= timestamp(decision_time)
                except ValueError:
                    errors.append("存在无效的可见时间")
        historical += len(bars) >= 60 and all(b.get("adj_factor") for b in bars[-60:])
        states = stock.get("status_history", [])
        from .market_rules import visible_state
        state = visible_state(stock, day, decision_time or day + "T23:59:59+08:00")
        halted += not current and bool(state and state.get("halted") is True)
        known_status += any(s.get("known_at") and s.get("is_st") is not None and s.get("halted") is not None
            and s.get("valid_from", "9999") <= day and (not s.get("valid_to") or day < s["valid_to"])
            and (not decision_time or timestamp(s["known_at"]) <= timestamp(decision_time)) for s in states)
    complete = bool(value["meta"].get("complete")) and bool(pool) and quoted + halted == len(pool)
    calendar = calendar_visible(value["meta"], decision_time)
    sectors = value.get("sectors", [])
    members = bool(sectors) and all(s.get("complete") and s.get("known_at") for s in sectors)
    if not complete:
        reasons.append("研究范围报价覆盖或供应商完整性声明不足")
    if known_status != len(pool) or not pool:
        reasons.append("缺少可追溯的证券状态")
    if limits != quoted or not quoted:
        reasons.append("部分证券缺少当日涨跌停价格")
    if not calendar:
        reasons.append("交易日历未经核验或在决策时点前不可见")
    if historical != len(pool):
        reasons.append("历史补齐范围不足，存在选样偏差")
    if not members:
        reasons.append("题材历史成分或获知时间不完整")
    if conflicts:
        reasons.append(f"{conflicts}只证券存在参考收盘冲突，已排除")
    if not demo and timed != quoted:
        reasons.append("部分行情历史可见时间未知")
    market_ready = complete and calendar and known_status == len(pool) and limits == quoted and timed == quoted
    strict = market_ready and members
    return {"status": "BLOCKED" if errors else "READY" if strict and historical == len(pool) and not demo else "PARTIAL",
        "reasons": errors + reasons + (["合成演示数据"] if demo else []),
        "scope": "主板与创业板研究范围" if set(boards) == {"MAIN", "CHINEXT"} else "所选研究板块",
        "coverage": {"universe": len(pool), "quoted": quoted, "quote_pct": round(100 * quoted / len(pool), 2) if pool else None,
            "limits": limits, "known_halted_without_quote": int(halted), "historical_60": int(historical), "known_status": int(known_status), "timed_quotes": int(timed), "conflicts": conflicts},
        "capabilities": {"daily_research": quoted > 0 and not errors, "market_regime": market_ready and not demo,
            "point_in_time_backtest": strict and historical == len(pool) and not demo,
            "minute_confirmation": False, "orderbook_fill": False,
            "announcement_filter": bool(value["meta"].get("announcements_complete"))},
        "verified_fields_note": "参考收盘一致只核对close字段，不认证其他字段或来源独立性"}


def context(dataset, snapshot_id=None, decision_time=None, quality=None):
    return {"trade_date": dataset["meta"]["trade_date"], "data_asof": dataset["meta"].get("available_at"),
        "decision_time": decision_time, "snapshot_id": snapshot_id,
        "mode": "demo" if dataset["meta"].get("mode") == "demo" else "real",
        "quality": quality or assess(dataset), "exchange_timezone": "Asia/Shanghai"}
