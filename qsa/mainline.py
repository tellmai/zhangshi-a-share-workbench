"""Causal mainline persistence and explainable leader observations."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta

from .analytics import analyze
from .contracts import ResearchConfig, board_of, digest, timestamp
from .quality import assess, context, legacy_view
from .quant import config_settings, evaluate
from .risk import budget


def scope_dataset(dataset, boards):
    value = legacy_view(dataset)
    demo = value["meta"].get("mode") == "demo"
    def belongs(code):
        try:
            board = board_of(code)
            return board in boards or (demo and board == "DEMO")
        except ValueError:
            return False
    value["stocks"] = [s for s in value["stocks"] if belongs(s["code"])]
    for sector in value.get("sectors", []):
        sector["original_member_count"] = len(sector["members"])
        sector["members"] = [c for c in sector["members"] if belongs(c)]
    value["meta"]["expected_count"] = len(value["stocks"])
    value["meta"]["universe"] = "所选主板/创业板研究范围；沿用实际采集覆盖标记"
    return value


def evaluate_day(dataset, config=None, prior=(), decision_time=None, snapshot_id=None, portfolio=None):
    cfg = ResearchConfig.model_validate(config or {}).model_dump()
    day = dataset["meta"]["trade_date"]
    cutoff = timestamp(decision_time or dataset["meta"].get("available_at") or day + "T19:30:00+08:00")
    quality = assess(dataset, cfg["boards"], cutoff)
    scoped = scope_dataset(dataset, cfg["boards"])
    if not scoped["stocks"]:
        return {"context": context(dataset, snapshot_id, cutoff, quality), "market": {"state": "UNCERTAIN"},
            "themes": [], "candidates": [], "signals": [], "weak_to_strong": {"enabled": False, "reason": "缺少分钟和竞价证据"}, "config": cfg}
    analyses = {kind: analyze(scoped, {"sector_type": kind, "style": "balanced"}) for kind in ("industry", "theme")}
    m = analyses["industry"]["market"]
    state = "UNCERTAIN"
    if quality["capabilities"].get("market_regime"):
        breadth = (m["breadth"] or 0) / 100
        state = "STRONG" if breadth >= cfg["breadth_strong"] and (m["limit_up"] or 0) >= m["active_count"] * cfg["limit_density"] else "ROTATION" if breadth >= cfg["breadth_rotation"] else "WEAK"
    expected_days = [d for d in dataset["meta"]["calendar"] if d <= day][-3:]
    prior_by_day = {r["context"]["trade_date"]: r for r in prior
        if r.get("context", {}).get("decision_time") and timestamp(r["context"]["decision_time"]) <= cutoff
        and r["context"]["trade_date"] in expected_days and r["context"]["trade_date"] < day}
    history_complete = len(expected_days) == 3 and all(d == day or d in prior_by_day for d in expected_days)
    sectors, candidates = [], {}
    source_stocks = {s["code"]: s for s in scoped["stocks"]}
    for kind, analysis in analyses.items():
        ranked = sorted([s for s in analysis["sectors"] if s["score"] is not None], key=lambda s: (-s["score"], s["id"]))
        lookup = {s["code"]: s for s in analysis["stocks"]}
        for rank, sector in enumerate(ranked, 1):
            prior_ranks = []
            for prev_day in expected_days:
                if prev_day == day:
                    prior_ranks.append({"date": day, "rank": rank})
                elif prev_day in prior_by_day:
                    match = next((t for t in prior_by_day[prev_day].get("themes", [])
                        if t["id"] == sector["id"] and t["classification"] == sector["classification"] and t["type"] == kind), None)
                    prior_ranks.append({"date": prev_day, "rank": match["rank"] if match else None})
            hits = sum(r["rank"] is not None and r["rank"] <= 3 for r in prior_ranks)
            item = {**sector, "rank": rank, "rank_history": prior_ranks, "top3_days": hits,
                "persistent": bool(history_complete and hits >= 2 and rank <= 3),
                "history_complete": history_complete, "overlap_group": sector["id"], "leaders": []}
            members = sorted([lookup[c] for c in sector["members"] if c in lookup and lookup[c]["selected"]], key=lambda r: (-r["score"], r["code"]))
            for pos, stock in enumerate(members, 1):
                previous_positions = []
                for previous in prior_by_day.values():
                    t = next((t for t in previous.get("themes", []) if t["id"] == sector["id"]
                        and t["classification"] == sector["classification"]), None)
                    previous_positions += [l["rank"] for l in (t or {}).get("leaders", []) if l["code"] == stock["code"]]
                times = 1 + sum(p <= 3 for p in previous_positions)
                role = "CORE_LEADER" if pos == 1 and history_complete and times >= 2 else "EMERGING_LEADER" if pos <= 3 and not previous_positions else "CATCH_UP" if pos <= 3 else "FOLLOWER"
                if any(event.get("risk") for event in source_stocks[stock["code"]].get("announcements", [])):
                    role = "HIGH_RISK"
                item["leaders"].append({"code": stock["code"], "name": stock["name"], "rank": pos, "score": stock["score"], "role": role})
                if pos > 3 or rank > 3:
                    continue
                source = source_stocks[stock["code"]]
                cfg_legacy = config_settings({"template": "pullback"})
                _, failures = evaluate(source, source["bars"], cfg_legacy)
                if not item["persistent"]:
                    failures.append("持续主线尚未确认或三日记录不足")
                if state in ("WEAK", "UNCERTAIN"):
                    failures.append("市场环境不允许新增计划")
                if role == "HIGH_RISK":
                    failures.append("存在明确公告风险")
                bars = source["bars"]
                anchor = bars[-1].get("adj_factor")
                stop = min(b["low"] * b["adj_factor"] / anchor for b in bars[-3:]) - .01 if len(bars) >= 3 and anchor and all(b.get("adj_factor") for b in bars[-3:]) else None
                candidate = {"code": stock["code"], "name": stock["name"], "board": source.get("board_id") or board_of(stock["code"]),
                    "score": stock["score"], "components": stock["components"], "penalties": stock["penalties"],
                    "theme_id": sector["id"], "theme_name": sector["name"], "theme_type": kind, "theme_rank": rank,
                    "role": role, "leader_rank": pos, "persistent": item["persistent"], "overlap_group": item["overlap_group"],
                    "strategy_id": "mainline_pullback", "strategy_version": "1.0.0", "setup_passed": not failures,
                    "status": "OBSERVE" if not failures and quality["status"] == "READY" and dataset["meta"].get("announcements_complete") else "RESEARCH_ONLY",
                    "reasons": stock["reasons"], "risk_flags": stock["risk_flags"], "exclusions": failures,
                    "announcement_status": "checked" if dataset["meta"].get("announcements_complete") else "待人工核验",
                    "close": bars[-1]["close"], "invalidation_price": round(stop, 2) if stop else None,
                    "confirmation": "主线保持持续强势，回踩MA10上下3%且20日均量倍数0.3至1.2；次日重新核验价格与事件。",
                    "invalidation": "跌破三日结构低点、收盘退出条件成立、主线失效或持有到期；T+1及跌停可能延后退出。",
                    "decision_time": cutoff, "valid_until": None, "snapshot_id": snapshot_id,
                    "config_hash": digest(cfg), "historical_statistics": None}
                next_days = [d for d in dataset["meta"]["calendar"] if d > day]
                candidate["valid_until"] = timestamp(next_days[0] + "T15:00:00+08:00") if next_days else None
                candidate["validity_note"] = "次一交易日收盘失效；日历无未来会话时待核验，不推送交易确认"
                if stop:
                    candidate["budget"] = budget(stock["code"], day, bars[-1]["close"], stop, cfg, portfolio, state, item["overlap_group"])
                else:
                    candidate["budget"] = {"shares": 0, "status": "BLOCKED", "reasons": ["缺少结构低点或复权证据"]}
                candidate["signal_id"] = digest({"snapshot": snapshot_id, "day": day, "code": stock["code"], "strategy": "mainline_pullback:1.0.0", "config": cfg, "decision_time": cutoff})[:32]
                if stock["code"] not in candidates or (candidate["persistent"], candidate["score"], -candidate["theme_rank"]) > (candidates[stock["code"]]["persistent"], candidates[stock["code"]]["score"], -candidates[stock["code"]]["theme_rank"]):
                    candidates[stock["code"]] = candidate
            sectors.append(item)
    # Overlap is an annotation; neither membership nor source ranking is rewritten.
    for kind in ("industry", "theme"):
        peers = [s for s in sectors if s["type"] == kind]
        for i, sector in enumerate(peers):
            a = set(sector["members"])
            for parent in peers[:i]:
                if parent["classification"] != sector["classification"]:
                    continue
                b = set(parent["members"])
                if a | b and len(a & b) / len(a | b) >= cfg["overlap_threshold"]:
                    sector["overlap_group"] = parent["overlap_group"]
                    break
    for row in candidates.values():
        sector = next(t for t in sectors if t["id"] == row["theme_id"])
        row["overlap_group"] = sector["overlap_group"]
        if row.get("invalidation_price"):
            row["budget"] = budget(row["code"], day, row["close"], row["invalidation_price"], cfg, portfolio, state, row["overlap_group"])
    ordered = sorted(candidates.values(), key=lambda c: (not c["setup_passed"], -c["score"], c["code"]))
    return {"context": context(dataset, snapshot_id, cutoff, quality), "config": cfg,
        "market": {**m, "state": state, "gate_note": "透明规则环境判断，未验证收益优势"},
        "themes": sectors, "candidates": ordered, "signals": [c for c in ordered if c["setup_passed"]],
        "weak_to_strong": {"enabled": False, "phase": "L0观察", "reason": "分钟及竞价的可见路径尚未核验，未启用盘中确认"},
        "scope": "已覆盖范围排名；行业与题材分榜，固定综合共振权重30/20/15/20/15"}
