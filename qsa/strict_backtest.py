"""Point-in-time daily replay, isolated from the preserved V1 sample engine."""
from __future__ import annotations

import copy
from collections import Counter
from decimal import Decimal

from .contracts import ResearchConfig, board_of, money, timestamp
from .mainline import evaluate_day
from .market_rules import dated_cost, visible_state
from .quality import assess, calendar_visible
from .version import ENGINE_VERSION
from .quant import adjustment_changed, config_settings, evaluate, fill_block
from .risk import budget


def point_in_time_frame(dataset, cutoff):
    value = copy.deepcopy(dataset)
    day, cutoff = value["meta"]["trade_date"], timestamp(cutoff)
    if not value["meta"].get("available_at") or timestamp(value["meta"]["available_at"]) > cutoff:
        raise ValueError(f"{day} 快照在决策时点前不可见")
    for stock in value["stocks"]:
        state = visible_state(stock, day, cutoff)
        if not state and board_of(stock["code"]) in ("MAIN", "CHINEXT"):
            raise ValueError(f"{day} {stock['code']}缺少当时可见证券状态")
        if state:
            stock["is_st"] = state.get("is_st")
            # Current names may contain a later ST label; status comes from history.
            stock["name"] = state.get("name") or stock["code"]
        bars = []
        for bar in stock.get("bars", []):
            if bar["date"] > day:
                continue
            known = bar.get("provenance", {}).get("available_at")
            if known and timestamp(known) <= cutoff:
                bars.append(bar)
        stock["bars"] = bars
        stock["announcements"] = [e for e in stock.get("announcements", [])
            if e.get("published_at") and timestamp(e["published_at"]) <= cutoff]
    value["sectors"] = [s for s in value.get("sectors", []) if s.get("known_at")
        and timestamp(s["known_at"]) <= cutoff and s.get("valid_from", day) <= day
        and (not s.get("valid_to") or day < s["valid_to"])]
    return value


def replay(frames, config=None, template="mainline_pullback", progress=lambda *_: None, cancelled=lambda: False,
           *, loader=None, requested_start=None, requested_end=None):
    cfg = ResearchConfig.model_validate(config or {}).model_dump()
    if template not in ("mainline_pullback", "trend", "volume", "pullback", "relay"):
        raise ValueError("严格回测策略无效")
    frame_day = lambda f: f.get("trade_date") or f["dataset"]["meta"]["trade_date"]
    load = lambda f: f["dataset"] if "dataset" in f else loader(f)
    ordered = sorted(frames, key=frame_day)
    if len(ordered) < 3:
        return {"status": "BLOCKED", "metrics": None, "failure": "至少需要3个真实交易日的可见快照", "curve": [], "trades": [], "orders": []}
    days = [frame_day(f) for f in ordered]
    if len(set(days)) != len(days):
        raise ValueError("每个交易日只能选择一个可见版本")
    first = load(ordered[0])
    calendar = [d for d in first["meta"]["calendar"] if (requested_start or days[0]) <= d <= (requested_end or days[-1])]
    if days != calendar or not calendar_visible(first["meta"], ordered[0].get("decision_time") or days[0]+"T19:30:00+08:00"):
        return {"status": "BLOCKED", "metrics": None, "failure": "缺少区间内完整、已核验交易日快照", "curve": [], "trades": [], "orders": []}
    cash = money(cfg["capital"])
    positions, pending, curve, trades, orders, reports = {}, [], [], [], [], []
    skips, fee_totals = Counter(), Counter()
    high_water = cash
    failure = None
    legacy_cfg = config_settings({"template": template if template != "mainline_pullback" else "pullback"})

    def costs(value, side, day):
        item = dated_cost(value, side, day, cfg)
        for k, v in item.items():
            fee_totals[k] += v
        return money(sum(item.values()))

    def sell(code, bar, price, why, day, index):
        nonlocal cash
        p = positions[code]
        state = visible_state(stocks.get(code, {}), day, day + "T09:30:00+08:00")
        if not state or state.get("halted") is not False:
            skips["卖出时开盘前状态未知或停牌"] += 1
            p["exit_pending"] = why
            return False
        if p["entry_date"] >= day:
            skips["T+1"] += 1
            return False
        blocked = fill_block(bar, "sell", True, price)
        if blocked:
            skips[blocked] += 1
            p["exit_pending"] = why
            return False
        proceeds = money(Decimal(str(price)) * p["qty"])
        fees = costs(proceeds, "sell", day)
        cash += proceeds - fees
        pnl = money(proceeds - fees - p["basis"])
        orders.append({"date": day, "executed_at": day + "T09:30:00+08:00" if "开盘" in why or why == "持有期到期" else day + "T15:00:00+08:00",
            "code": code, "side": "sell", "qty": p["qty"], "price": float(money(price)), "fees": float(fees), "reason": why,
            "signal_id": p["signal_id"], "time_precision": "日线保守情景，非真实盘中成交时刻"})
        trades.append({"code": code, "entry_date": p["entry_date"], "exit_date": day, "qty": p["qty"],
            "entry_price": p["entry_price"], "exit_price": price, "pnl": float(pnl),
            "return_pct": float(pnl / p["basis"] * 100) if p["basis"] else None, "reason": why,
            "holding_sessions": index - p["entry_index"], "signal_id": p["signal_id"]})
        del positions[code]
        return True

    for index, frame in enumerate(ordered):
        if cancelled():
            raise InterruptedError("回测已取消")
        day = days[index]
        progress(int(index / len(days) * 95), f"严格时点回放 {index+1}/{len(days)} · {day}")
        cutoff = frame.get("decision_time") or day + "T19:30:00+08:00"
        try:
            dataset = point_in_time_frame(load(frame), cutoff)
            quality = assess(dataset, cfg["boards"], cutoff)
            if not quality["capabilities"].get("point_in_time_backtest"):
                raise ValueError("严格历史输入不足：" + "；".join(quality["reasons"]))
        except (ValueError, TypeError, KeyError) as error:
            failure = f"{day} {error}"
            break
        stocks = {s["code"]: s for s in dataset["stocks"]}
        bars = {c: s["bars"][-1] for c, s in stocks.items() if s["bars"] and s["bars"][-1]["date"] == day}
        for code, p in positions.items():
            state = visible_state(stocks.get(code, {}), day, cutoff)
            bar = bars.get(code)
            if state and state.get("halted") is True:
                continue
            if not bar:
                failure = f"{day} {code}持仓行情缺失且无法确认为停牌，完整绩效中止"
                break
            if adjustment_changed({"adj_factor": p["last_factor"], "close": p["mark"]}, bar):
                failure = f"{day} {code}出现复权跳变，缺少经核验公司行为账本，完整绩效中止"
                break
        if failure:
            break
        for code, p in list(positions.items()):
            bar = bars.get(code)
            if bar and (index >= p["due_index"] or p.get("exit_pending")):
                sell(code, bar, bar["open"], p.get("exit_pending") or "持有期到期", day, index)
        # Only signals frozen at the preceding decision can drive today's entry.
        equity_at_open = cash + sum((money(p["mark"] * p["qty"]) for p in positions.values()), Decimal(0))
        for signal in pending:
            code, bar = signal["code"], bars.get(signal["code"])
            state = visible_state(stocks.get(code, {}), day, day + "T09:30:00+08:00")
            if not state or state.get("is_st") or state.get("halted") is not False:
                skips["开盘前状态未知、风险警示或停牌"] += 1
                continue
            if bar and signal.get("reference_bar") and adjustment_changed(signal["reference_bar"], bar):
                skips["信号后复权跳变，缺少可比价格证据"] += 1
                continue
            blocked = fill_block(bar, "buy", True)
            if blocked:
                skips[blocked] += 1
                continue
            account = {"equity": float(equity_at_open), "cash": float(cash), "invested": float(equity_at_open-cash),
                "positions": [{"code": c, "overlap_group": p.get("overlap_group")} for c, p in positions.items()], "high_water": float(high_water), "confirmed": True}
            allocation = budget(code, day, bar["open"], signal["invalidation_price"], cfg, account, signal["regime"], signal.get("overlap_group"))
            qty = allocation["shares"]
            if not qty:
                skips["；".join(allocation["reasons"])] += 1
                continue
            notional = money(Decimal(str(bar["open"])) * qty)
            fee = costs(notional, "buy", day)
            cash -= notional + fee
            if cash < 0:
                raise ArithmeticError("资金预算错误：现金透支")
            positions[code] = {"qty": qty, "basis": notional + fee, "entry_date": day, "entry_index": index,
                "due_index": index + cfg["holding_days"], "entry_price": bar["open"], "mark": bar["open"],
                "last_factor": bar["adj_factor"], "stop": signal["invalidation_price"], "signal_id": signal["signal_id"],
                "overlap_group": signal.get("overlap_group")}
            orders.append({"date": day, "executed_at": day + "T09:30:00+08:00", "code": code, "side": "buy", "qty": qty,
                "price": bar["open"], "fees": float(fee), "reason": "上一交易日收盘信号，次日开盘情景", "signal_id": signal["signal_id"]})
        for code, p in list(positions.items()):
            bar = bars.get(code)
            if not bar or p["entry_date"] == day:
                continue
            if bar["open"] <= p["stop"]:
                sell(code, bar, bar["open"], "止损跳空，按开盘价情景", day, index)
            elif bar["low"] <= p["stop"]:
                sell(code, bar, p["stop"], "日线止损触发，成交路径为保守假设", day, index)
        for code, p in positions.items():
            if code in bars:
                p["mark"] = bars[code]["close"]
                p["mark_date"] = day
                p["last_factor"] = bars[code]["adj_factor"]
        invested = sum((money(p["mark"] * p["qty"]) for p in positions.values()), Decimal(0))
        equity = money(cash + invested)
        high_water = max(high_water, equity)
        curve.append({"date": day, "cash": float(cash), "equity": float(equity), "positions": len(positions),
            "net_value": float(equity / money(cfg["capital"])), "drawdown_pct": float((1-equity/high_water)*100)})
        report = evaluate_day(dataset, cfg, reports[-2:], cutoff, frame.get("snapshot_id"))
        reports.append(report)
        pending = []
        if template == "mainline_pullback":
            for signal in report["signals"]:
                pending.append({**signal, "regime": report["market"]["state"]})
        else:
            for code, stock in stocks.items():
                if board_of(code) not in cfg["boards"] or stock.get("is_st") or code not in bars:
                    continue
                b = bars[code]
                row, failures = evaluate(stock, stock["bars"], legacy_cfg,
                    boards=(stock.get("limit") or {}).get("consecutive"), sector_score=None)
                if row and not failures:
                    stop = b["close"] * (1 - legacy_cfg["stop_loss_pct"] / 100)
                    pending.append({"code": code, "score": row["score"], "invalidation_price": stop,
                        "signal_id": f"{day}:{code}:{template}", "regime": report["market"]["state"], "overlap_group": None})
        pending.sort(key=lambda s: (-s["score"], s["code"]))
        for signal in pending:
            if signal["code"] in bars:
                signal["reference_bar"] = {k: bars[signal["code"]][k] for k in ("close", "adj_factor")}
        if template == "mainline_pullback":
            active = {t["id"] for t in report["themes"] if t["persistent"]}
            for code, p in positions.items():
                bars_p = stocks.get(code, {}).get("bars", [])
                if len(bars_p) >= 10 and all(b.get("adj_factor") for b in bars_p[-10:]):
                    factor = bars_p[-1]["adj_factor"]
                    ma10 = sum(b["close"] * b["adj_factor"] / factor for b in bars_p[-10:]) / 10
                    if bars_p[-1]["close"] < ma10:
                        p["exit_pending"] = "前一收盘跌破MA10，次日开盘退出"
                signal = next((s for prev in reversed(reports) for s in prev["candidates"] if s["signal_id"] == p["signal_id"]), None)
                if signal and signal["theme_id"] not in active:
                    p["exit_pending"] = "主线持续性失效，次日开盘退出"
    metrics = None
    if curve and not failure:
        metrics = {"total_return_pct": round((curve[-1]["net_value"] - 1) * 100, 4),
            "max_drawdown_pct": round(max(x["drawdown_pct"] for x in curve), 4), "final_equity": curve[-1]["equity"],
            "closed_trades": len(trades), "win_rate_pct": round(100 * sum(t["pnl"] > 0 for t in trades)/len(trades), 2) if trades else None,
            "total_fees": float(money(sum(fee_totals.values()))), "open_positions": len(positions),
            "mean_trade_pnl": round(sum(t["pnl"] for t in trades)/len(trades), 2) if trades else None}
    return {"status": "incomplete" if failure and curve else "BLOCKED" if failure else "completed", "failure": failure,
        "engine": "strict-point-in-time-"+ENGINE_VERSION, "template": template, "config": cfg, "metrics": metrics,
        "start": days[0], "end": days[-1], "computed_through": curve[-1]["date"] if curve else None,
        "curve": curve, "trades": trades, "orders": orders, "skips": dict(skips), "fees": dict(fee_totals),
        "open_positions": [{"code": c, **{k: float(v) if isinstance(v, Decimal) else v for k, v in p.items()}} for c, p in positions.items()],
        "cash_baseline": [{"date": d, "net_value": 1.0} for d in days],
        "research_only": True, "fees_confirmed": cfg["fees_confirmed"],
        "warnings": ["保守日线成交仍为研究情景，不代表真实成交。", "期末不强制清仓；单价涨停买入与跌停卖出跳过。",
            "分钟确认、回封排队与公告有效性不由日线回测证明。", "未确认实际费率时，费用与净值均为研究假设。"]}


def experiment_summary(result, hold_days=5):
    curve, trades = result.get("curve", []), result.get("trades", [])
    n = len(curve)
    train_end, validation_end = int(n * .6), int(n * .8)
    windows = {"research": curve[:max(0, train_end-hold_days)],
        "validation": curve[min(n, train_end+hold_days):max(0, validation_end-hold_days)],
        "test": curve[min(n, validation_end+hold_days):]}
    segments = {}
    for name, rows in windows.items():
        segments[name] = {"start": rows[0]["date"] if rows else None, "end": rows[-1]["date"] if rows else None,
            "sessions": len(rows), "return_pct": round((rows[-1]["equity"]/rows[0]["equity"]-1)*100, 4) if len(rows) >= 2 else None}
    # These are descriptive windows, not a model trained on the test window.
    return {"segments": segments, "embargo_sessions": hold_days, "event_count": len(trades),
        "sample_sufficient": len(trades) >= 200 and segments["test"]["sessions"] >= 60,
        "status": "RESEARCH_ONLY", "note": "时间分组用于描述；walk-forward任务会逐窗口独立重跑，未自动寻优或认证策略有效。"}
