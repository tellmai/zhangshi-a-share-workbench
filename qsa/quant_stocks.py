"""Independent cash accounts for an explicitly chosen, frozen stock list."""
from __future__ import annotations

import bisect
import copy
import re
from collections import Counter

from .data import iso_date
from .quant import config_settings, evaluate, replay, required_bars, universe_ok

MAX_STOCKS = 20


def stock_row(stock, demo=False):
    bars = stock.get("bars", [])
    return {"code": stock["code"], "name": stock["name"], "history_bars": len(bars),
        "first_date": bars[0]["date"] if bars else None, "last_date": bars[-1]["date"] if bars else None,
        "eligible": universe_ok(stock, demo)}


def select_stocks(dataset, values):
    """Resolve codes/names atomically: never quietly omit an invalid selection."""
    if isinstance(values, str):
        values = [v for v in re.split(r"[\s,，;；、]+", values.strip()) if v]
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_STOCKS:
        raise ValueError(f"请指定1—{MAX_STOCKS}只股票，使用代码或完整名称")
    selected = []
    for value in values:
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= 40:
            raise ValueError("股票代码或名称格式无效")
        value = value.strip().upper()
        if re.fullmatch(r"(?:SH|SZ)\d{6}", value): value = value[2:]+"."+value[:2]
        matches = [s for s in dataset["stocks"] if value in (s["code"].upper(), s["name"].upper())
            or (re.fullmatch(r"\d{6}", value) and s["code"].split(".")[0] == value)]
        if len(matches) != 1: raise ValueError(f"{value} 未唯一匹配当前快照，请搜索后选择完整股票代码")
        stock = matches[0]
        if not universe_ok(stock, dataset["meta"].get("mode") == "demo"):
            raise ValueError(f"{stock['name']} 不在当前策略支持范围：沪深主板、创业板，排除当前ST及退市名称")
        if stock["code"] not in [s["code"] for s in selected]: selected.append(stock)
    return selected


def replay_days(dataset, start, end):
    start, end = iso_date(start), iso_date(end)
    if start > end or end > dataset["meta"]["trade_date"]: raise ValueError("日期范围无效或超过快照日期")
    days = [d for d in dataset["meta"]["calendar"] if start <= d <= end]
    if not 3 <= len(days) <= 252: raise ValueError("回放区间需要3—252个已有交易日")
    return start, end, days


def history_coverage(stock, cfg, days):
    bars = stock.get("bars", [])
    dates = [b["date"] for b in bars]
    count = bisect.bisect_right(dates, days[0])
    need = required_bars(cfg)
    relevant = [b for b in bars if b["date"] <= days[-1]]
    issues = []
    if count < need: issues.append(f"起点{days[0]}及以前仅{count}根日线，策略需要至少{need}根预热数据")
    if not dates or dates[-1] < days[-1]: issues.append(f"历史尚未覆盖区间末日{days[-1]}")
    if any(not b.get("adj_factor") for b in relevant[max(0, count-need):]): issues.append("所需历史缺少复权因子")
    available = set(dates)
    return {"history_bars": len(bars), "first_date": dates[0] if dates else None,
        "last_date": dates[-1] if dates else None, "warmup_bars": count, "required_bars": need,
        "missing_sessions": sum(d not in available for d in days), "issues": issues}


def replay_stocks(dataset, config, codes, start, end, progress=lambda *_:None, cancelled=lambda:False):
    cfg = config_settings(config)
    if cfg["min_boards"] or cfg["sector_min"] or cfg["ranking"] == "relay":
        raise ValueError("缺少逐日连板或板块历史，请关闭相关条件后回测")
    selected = select_stocks(dataset, codes)
    start, end, days = replay_days(dataset, start, end)
    execution = {**cfg, "max_positions": 1}
    results = []
    for index, stock in enumerate(selected):
        if cancelled(): raise InterruptedError("运行已取消")
        progress(int(index/len(selected)*95), f"独立回测 {index+1}/{len(selected)} · {stock['name']}")
        coverage = history_coverage(stock, cfg, days)
        identity = {"code": stock["code"], "name": stock["name"], "coverage": coverage}
        if coverage["issues"]:
            results.append({**identity, "status": "skipped", "failure": "；".join(coverage["issues"]), "metrics": None})
            continue
        try:
            subset = {**dataset, "stocks": [copy.deepcopy(stock)]}
            def child_progress(percent, message):
                progress(int((index+percent/100)/len(selected)*95), f"{stock['name']} ({index+1}/{len(selected)}) · {message}")
            result = replay(subset, execution, start, end, child_progress, cancelled)
            counts = Counter()
            for day in days:
                if cancelled(): raise InterruptedError("运行已取消")
                n = bisect.bisect_right([b["date"] for b in stock["bars"]], day)
                if not n or stock["bars"][n-1]["date"] != day: continue
                _, failures = evaluate(stock, stock["bars"][:n], execution)
                if failures: counts[failures[0]] += 1
            result.update(identity, entry_exclusions=dict(counts), signal_days=sum(x["count"] for x in result["signal_counts"]))
            result["reference_name"] = "本股起点收盘持有观察线（复权、不计费）"
            results.append(result)
        except InterruptedError: raise
        except Exception as error:
            results.append({**identity, "status": "error", "failure": str(error)[:500], "metrics": None})
    completed = sum(r["status"] == "completed" for r in results)
    return {"type": "stock_backtest", "stock_backtest_version": "1.0.0",
        "status": "completed" if completed == len(results) else "partial" if completed else "incomplete",
        "start": start, "end": end, "first_session": days[0], "last_session": days[-1],
        "config": cfg, "execution_config": execution, "codes": [s["code"] for s in selected],
        "capital_per_stock": cfg["capital"], "results": results,
        "summary": {"selected": len(results), "completed": completed,
            "skipped": sum(r["status"] == "skipped" for r in results),
            "incomplete": sum(r["status"] == "incomplete" for r in results),
            "errors": sum(r["status"] == "error" for r in results)},
        "warnings": ["每只股票使用独立初始资金、只持有本股；不共享资金，不把各股收益相加作为组合收益。",
            f"本模式固定每个独立账户最多持有1只；原策略的最大持仓{cfg['max_positions']}只仅在组合回测中生效。",
            "所有股票使用相同申请区间；起点预热不足或末日未覆盖时跳过该股，保留原因，不偷偷缩短区间。",
            "中间缺报价可能是停牌或数据缺失，无法区分；暂停信号、延后到期退出，持仓沿用最近有效收盘估值。",
            "信号只用当日及以前数据，下一交易日开盘模拟进入；遵守T+1、100股整数手，按设置扣除费用与滑点。",
            "缺少历史涨跌停价时默认允许非单价日按假设成交，日线不能还原排队或盘中路径；严格模式会跳过缺限价日。",
            "补齐历史须与快照当日OHLC核对；历史价格未逐日跨源认证。持仓复权因子跳变且事件资料不足时中止该股。",
            "股票由用户事后指定，存在选样偏差；历史股票状态、公司行为和涨跌停资料不完整，结果用于研究。"]}
