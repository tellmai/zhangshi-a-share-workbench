"""Evidence-matched execution comparison and factual realized-PnL grouping."""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from .contracts import money, timestamp
from .ledger import trade_day


def review(repository, ledger, portfolio):
    account, cutoff = portfolio["account"], timestamp(portfolio["asof"])
    reference_account = "shadow" if account == "manual" else None
    actual = [t for t in ledger.trades(account) if t["executed_at"] <= cutoff]
    shadow = [t for t in ledger.trades(reference_account) if t["executed_at"] <= cutoff] if reference_account else []
    grouped = {}
    for kind, trades in (("actual", actual), ("shadow", shadow)):
        for row in trades:
            key = (row.get("signal_id") or "outside:"+row["id"], row["code"], row["side"])
            grouped.setdefault(key, {"actual": [], "shadow": []})[kind].append(row)

    def aggregate(rows):
        qty = sum(t["qty"] for t in rows)
        notional = sum((money(Decimal(str(t["price"]))*t["qty"]) for t in rows), Decimal(0))
        fees = sum((money(t["fees"]) for t in rows), Decimal(0))
        return {"qty": qty, "vwap": float(notional/qty) if qty else None, "notional": float(notional),
            "fees": float(fees), "dates": sorted({trade_day(t["executed_at"]) for t in rows}),
            "trade_ids": [t["id"] for t in rows]}

    rows = []
    for (signal_key, code, side), buckets in sorted(grouped.items()):
        signal_id = None if signal_key.startswith("outside:") else signal_key
        signal = repository.get("signals", signal_id) if signal_id else None
        a, s = aggregate(buckets["actual"]), aggregate(buckets["shadow"])
        status = "COMPARABLE"
        if not signal_id: status = "OUTSIDE_PLAN"
        elif not signal: status = "SIGNAL_UNKNOWN"
        elif signal["code"] != code: status = "SIGNAL_MISMATCH"
        elif signal.get("decision_time") and any(t["executed_at"] < timestamp(signal["decision_time"]) for t in buckets["actual"]): status = "BEFORE_SIGNAL"
        elif not reference_account: status = "ACCOUNT_ONLY"
        elif not a["qty"]: status = "NO_ACTUAL_RECORD"
        elif not s["qty"]: status = "NO_SHADOW_RECORD"
        elif a["notional"] <= 0 or s["notional"] <= 0: status = "INVALID_PRICE_EVIDENCE"
        elif len(a["dates"]) != 1 or a["dates"] != s["dates"]: status = "DIFFERENT_EXECUTION_DATE"
        comparable = status == "COMPARABLE"
        direction = 1 if side == "buy" else -1
        reference_price = Decimal(str(s["notional"]))/s["qty"] if comparable else None
        delta = direction*(Decimal(str(a["notional"]))/a["qty"]-reference_price) if comparable else None
        rows.append({"signal_id": signal_id, "code": code, "side": side, "status": status,
            "strategy_id": (signal or {}).get("strategy_id"), "actual": a, "shadow": s,
            "planned_shares": (signal or {}).get("budget", {}).get("shares") if side == "buy" else None,
            "quantity_difference": a["qty"]-s["qty"] if a["qty"] and s["qty"] else None,
            "adverse_price_bps": round(float(delta/reference_price*10000), 4) if comparable else None,
            "adverse_price_amount": float(money(delta*a["qty"])) if comparable else None,
            "unit_fee_difference": round(a["fees"]/a["qty"]-s["fees"]/s["qty"], 6) if comparable else None})
    strategies = defaultdict(lambda: {"closed_executions": 0, "qty": 0, "pnl": Decimal(0)})
    buys = {t["id"]: t for t in actual if t["side"] == "buy"}
    for realized in portfolio["realized"]:
        if realized["pnl"] is None:
            continue
        ids = realized.get("source_signal_ids", [])
        sources = [repository.get("signals", key) for key in ids]
        if not ids:
            key = "OUTSIDE_PLAN"
        elif realized.get("has_unlinked_source") or len(ids) != 1:
            key = "MIXED_SOURCES"
        elif not sources[0] or sources[0].get("code") != realized["code"]:
            key = "SIGNAL_UNKNOWN"
        elif sources[0].get("decision_time") and any(buys[t]["executed_at"] < timestamp(sources[0]["decision_time"])
                for t in realized.get("source_trade_ids", []) if t in buys):
            key = "BEFORE_SIGNAL"
        else:
            key = sources[0].get("strategy_id") or "SIGNAL_UNKNOWN"
        version = str(sources[0].get("strategy_version") or "unknown") if len(sources) == 1 and sources[0] and key not in ("SIGNAL_UNKNOWN", "BEFORE_SIGNAL", "MIXED_SOURCES") else "unknown"
        strategies[(key,version)]["closed_executions"] += 1
        strategies[(key,version)]["qty"] += realized["qty"]
        strategies[(key,version)]["pnl"] += money(realized["pnl"])
    return {"account": account, "asof": cutoff, "reference_account": reference_account,
        "rows": rows, "comparable_groups": sum(r["status"] == "COMPARABLE" for r in rows),
        "strategy_realized": [{"strategy_id": key[0], "strategy_version": key[1], **value, "pnl": float(money(value["pnl"]))} for key,value in sorted(strategies.items())],
        "account_incomplete": portfolio["incomplete"],
        "note": "只比较同一冻结信号、同一证券方向和同一执行日；正偏差表示实际价格较不利。影子费用含研究滑点，与实际费用分别展示。盈亏按买入来源归组，混合来源单列，属于关联统计。"}
