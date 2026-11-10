from __future__ import annotations

import math
import statistics
from datetime import date

DEFAULTS = {"style": "balanced", "sector_type": "industry", "min_amount": 300000000, "min_turnover": 2, "max_turnover": 30, "min_score": 60, "min_sector_score": 40, "include_one_word": False}
WEIGHTS = {
    "balanced": {"sector": 30, "price": 20, "volume": 15, "trend": 20, "status": 15},
    "relay": {"sector": 30, "price": 15, "volume": 10, "trend": 10, "status": 35},
    "trend": {"sector": 30, "price": 15, "volume": 15, "trend": 35, "status": 5},
}


def clamp(value, low=0, high=1):
    return max(low, min(high, value))


def mean(values):
    return statistics.fmean(values) if values else None


def rounded(value, places=2):
    return round(value, places) if value is not None and math.isfinite(value) else None


def settings(options=None):
    result = {**DEFAULTS, **{k: v for k, v in (options or {}).items() if k in DEFAULTS}}
    if result["style"] not in WEIGHTS or result["sector_type"] not in ("industry", "theme"):
        raise ValueError("无效的筛选风格或板块类型")
    for key in ("min_amount", "min_turnover", "max_turnover", "min_score", "min_sector_score"):
        value = float(result[key])
        if not math.isfinite(value) or value < 0 or value > (1e12 if key == "min_amount" else 100):
            raise ValueError(f"筛选参数 {key} 超出范围")
        result[key] = value
    if result["min_turnover"] > result["max_turnover"]:
        raise ValueError("最低换手率不能高于最高换手率")
    if not isinstance(result["include_one_word"], bool):
        raise ValueError("一字板选项须为布尔值")
    return result


def ema(values, span):
    answer = [values[0]]
    alpha = 2 / (span + 1)
    for value in values[1:]:
        answer.append(alpha * value + (1 - alpha) * answer[-1])
    return answer


def indicators(bars):
    if not bars:
        return {}
    result = {"sessions": len(bars), "volume_ratio": None, "ma5": None, "ma10": None, "ma20": None, "return5": None, "return20": None, "breakout20": None, "extension20": None, "macd": None, "up_down_volume": None}
    if len(bars) >= 21:
        base = mean([b["volume_shares"] for b in bars[-21:-1]])
        result["volume_ratio"] = rounded(bars[-1]["volume_shares"] / base, 3) if base else None
        pos = [b["volume_shares"] for b in bars[-20:] if b["close"] > b["open"]]
        neg = [b["volume_shares"] for b in bars[-20:] if b["close"] < b["open"]]
        result["up_down_volume"] = {"positive_days": len(pos), "negative_days": len(neg), "ratio": rounded(mean(pos) / mean(neg), 3) if pos and neg and mean(neg) else None}
    if any(b.get("adj_factor") is None for b in bars):
        return result
    anchor = bars[-1]["adj_factor"]
    closes = [b["close"] * b["adj_factor"] / anchor for b in bars]
    for span in (5, 10, 20):
        if len(closes) >= span:
            result[f"ma{span}"] = mean(closes[-span:])
    for span in (5, 20):
        if len(closes) > span:
            result[f"return{span}"] = rounded((closes[-1] / closes[-span-1] - 1) * 100)
    if len(closes) >= 21:
        result["breakout20"] = closes[-1] > max(b["high"] * b["adj_factor"] / anchor for b in bars[-21:-1]) + .000001
        result["extension20"] = rounded((closes[-1] / result["ma20"] - 1) * 100)
    if len(closes) >= 120:
        fast, slow = ema(closes, 12), ema(closes, 26)
        diff = [a - b for a, b in zip(fast, slow)]
        dea = ema(diff, 9)
        hist = [2 * (a - b) for a, b in zip(diff, dea)]
        result["macd"] = {"diff": rounded(diff[-1], 4), "dea": rounded(dea[-1], 4), "hist": rounded(hist[-1], 4), "expanding": hist[-1] > hist[-2], "above_zero": diff[-1] > 0 and dea[-1] > 0, "seed": "第一根复权收盘价；EMA12/26/9；柱=2×(DIFF−DEA)"}
    return result


def stock_row(stock, day):
    bars = stock.get("bars", [])
    current = bars[-1] if bars and bars[-1]["date"] == day else None
    tech = indicators(bars)
    row = {k: stock.get(k) for k in ("code", "name", "exchange", "board", "industry", "list_date", "listed_before")}
    row.update({"technical": tech, "current": current, "verification": stock.get("verification", {"status": "single_source"}), "limit": stock.get("limit") or {}, "previous_limit": stock.get("previous_limit") or {}, "announcements": stock.get("announcements", []), "is_st": bool(stock.get("is_st") or "ST" in stock["name"].upper() or "退" in stock["name"]), "sector_names": [], "risk_flags": [], "reasons": [], "exclusions": [], "components": [], "penalties": [], "score": None, "selected": False, "sector_score": None, "role": "待观察", "limit_status": None, "consecutive": None, "one_word": False})
    if not current:
        row["exclusions"].append("分析日无有效日线（可能停牌或数据缺失）")
        return row
    row["pct_chg"] = rounded((current["close"] / current["pre_close"] - 1) * 100)
    status = row["limit"].get("status")
    upper, lower = current.get("up_limit"), current.get("down_limit")
    if upper is not None:
        inferred = "U" if abs(current["close"] - upper) < .0051 else ("Z" if current["high"] >= upper - .0051 else "N")
        if status == "U" and inferred != "U":
            row["exclusions"].append("涨停榜单与日线价格冲突")
        if status == "D" and lower is not None and abs(current["close"] - lower) > .0051:
            row["exclusions"].append("跌停榜单与日线价格冲突")
        if not status or status == "N":
            status = inferred
    if lower is not None and abs(current["close"] - lower) < .0051:
        status = "D"
    row["limit_status"] = status
    row["consecutive"] = row["limit"].get("consecutive") if status == "U" else 0
    row["one_word"] = bool(status == "U" and current["high"] - current["low"] < .0051)
    return row


def sector_metrics(dataset, rows, kind):
    lookup = {r["code"]: r for r in rows}
    sectors = []
    for sector in dataset.get("sectors", []):
        if sector["type"] != kind:
            continue
        all_members = sector["members"]
        members = [lookup[c] for c in all_members if c in lookup and not lookup[c]["is_st"]]
        available = [r for r in members if r["current"] and r["verification"].get("status") != "conflict" and not r["exclusions"]]
        nonst_known = sum(c in lookup for c in all_members)
        unknown_count = sum(c not in lookup for c in all_members)
        coverage = len(available) / (len(members) + unknown_count) if members or unknown_count else 0
        complete = bool(sector.get("complete") and nonst_known == len(all_members) and coverage >= .95 and len(available) >= 5)
        avg = mean([r["pct_chg"] for r in available])
        breadth = sum(r["pct_chg"] > 0 for r in available) / len(available) if available else 0
        up = sum(r["limit_status"] == "U" for r in available)
        limit_coverage = sum(r["limit_status"] is not None for r in available) / len(available) if available else 0
        # Compare exactly the same current constituent set on the prior session.
        today_total, previous_total, comparable = 0, 0, 0
        day_index = dataset["meta"]["calendar"].index(dataset["meta"]["trade_date"])
        previous_day = dataset["meta"]["calendar"][day_index - 1] if day_index else None
        source_lookup = {s["code"]: s for s in dataset["stocks"]}
        for row in available:
            bars = source_lookup[row["code"]]["bars"]
            previous = next((b for b in reversed(bars[:-1]) if b["date"] == previous_day), None)
            if previous:
                today_total += row["current"]["amount_yuan"]
                previous_total += previous["amount_yuan"]
                comparable += 1
        amount_ratio = today_total / previous_total if previous_total and comparable == len(available) else None
        score = None
        if complete and limit_coverage == 1:
            score = round(35 * clamp((avg + 3) / 8) + 35 * breadth + 30 * clamp(up / len(available) / .12), 1)
        sectors.append({"id": sector["id"], "name": sector["name"], "type": kind, "classification": sector["classification"], "asof_date": sector["asof_date"], "member_count": len(all_members), "eligible_count": len(members), "available_count": len(available), "coverage": rounded(coverage * 100, 1), "complete": complete, "score": score, "avg_pct": rounded(avg), "breadth": rounded(breadth * 100, 1), "limit_up": up if limit_coverage == 1 else None, "amount_yuan": sum(r["current"]["amount_yuan"] for r in available), "amount_ratio": rounded(amount_ratio), "members": [r["code"] for r in available], "incomplete_reason": None if score is not None else "成分、当日行情或涨停状态不足；至少需要5只有效成分"})
    sectors.sort(key=lambda s: (s["score"] is not None, s["score"] or 0), reverse=True)
    return sectors


def percentile(values, value):
    # Average rank for ties: all tied observations receive 0.5, not 1.0.
    if not values:
        return 0
    return (sum(x < value for x in values) + .5 * sum(x == value for x in values)) / len(values)


def daily_sector_leaders(dataset, sectors, kind):
    """One comparable series per board type; never promote an undated partial list."""
    day = dataset["meta"]["trade_date"]
    expanded = {s["id"]: s for s in sectors}
    coverage = dataset["meta"].get("provider_sector_coverage", {}).get(kind, {})
    provider_rows = [r for r in dataset.get("provider_sector_ranking", []) if r.get("type") == kind]
    expected = coverage.get("expected_count")
    full = (coverage.get("complete") is True and coverage.get("asof_date") == day
        and type(expected) is int and expected > 0 and len(provider_rows) == expected
        and len({r.get("id") for r in provider_rows}) == expected
        and all(r.get("id") and r.get("name") and r.get("asof_date") == day
            and r.get("source") == coverage.get("source") and bool(r.get("source"))
            and type(r.get("pct_chg")) in (int, float) and math.isfinite(r["pct_chg"])
            for r in provider_rows))
    if full:
        rows = [{**expanded.get(r["id"], {}), "id": r["id"], "name": r["name"], "type": kind,
            "daily_pct": r["pct_chg"], "daily_source": r["source"], "asof_date": day,
            "expanded": r["id"] in expanded, "members": expanded.get(r["id"], {}).get("members", []),
            "classification": r["source"]} for r in provider_rows]
        basis, source = "供应商板块指数涨幅", coverage["source"]
    else:
        rows = [{**s, "daily_pct": s["avg_pct"], "daily_source": s["classification"], "expanded": True}
            for s in sectors if s["complete"] and s["asof_date"] == day
            and s["avg_pct"] is not None and math.isfinite(s["avg_pct"])]
        basis, source = "同日成分等权涨幅", "已展开板块同日成分"
        expected = None
    rows.sort(key=lambda r: (-r["daily_pct"], -(r.get("breadth") or 0), -(r.get("amount_yuan") or 0), r["id"]))
    available_count = len(rows)
    positive = [{**row, "daily_rank": i+1} for i, row in enumerate(rows) if row["daily_pct"] > 0]
    return {"rows": positive, "asof_date": day, "type": kind, "basis": basis, "source": source,
        "complete_universe": bool(full), "available_count": available_count, "expected_count": expected,
        "expanded_count": len(sectors), "positive_count": len(positive),
        "scope": "同一供应商全量分类榜单" if full else "仅已展开且同日成分完整的板块；未覆盖全市场"}


def market_metrics(dataset, rows):
    eligible = [r for r in rows if not r["is_st"]]
    active = [r for r in eligible if r["current"] and not r["exclusions"] and r["verification"].get("status") != "conflict"]
    known_limits = [r for r in active if r["limit_status"] is not None]
    ups = [r for r in active if r["limit_status"] == "U"]
    ladders_complete = len(known_limits) == len(active) and all(r["consecutive"] is not None for r in ups)
    ladder = []
    known_ups = [r for r in ups if r["consecutive"] is not None]
    for height in sorted(set(r["consecutive"] for r in known_ups), reverse=True):
        subset = [r for r in known_ups if r["consecutive"] == height]
        ladder.append({"height": height, "count": len(subset), "stocks": [{"code": r["code"], "name": r["name"]} for r in subset]})
    prev_cohort = [r for r in eligible if r["previous_limit"].get("status") == "U" and r["previous_limit"].get("consecutive", 0) >= 2]
    promotion = None
    if dataset["meta"].get("previous_limit_complete") and prev_cohort and all(r["current"] and r["limit_status"] is not None and not r["exclusions"] for r in prev_cohort):
        promotion = 100 * sum(r["limit_status"] == "U" for r in prev_cohort) / len(prev_cohort)
    up_count, down_count = sum(r["pct_chg"] > 0 for r in active), sum(r["pct_chg"] < 0 for r in active)
    touches = [r for r in active if r["limit_status"] in ("U", "Z")]
    broken = [r for r in active if r["limit_status"] == "Z"]
    breadth = up_count / len(active) if active else 0
    # Defined descriptive heuristic, not a forecast of returns.
    phase = "偏强扩散" if breadth >= .65 and len(ups) >= len(active) * .025 else ("分歧轮动" if breadth >= .4 else "偏弱收缩")
    history = []
    history_days = dataset["meta"]["calendar"][-20:]
    history_stocks = [s for s in dataset["stocks"] if set(history_days).issubset({b["date"] for b in s["bars"]}) and not (s.get("is_st") or "ST" in s["name"].upper() or "退" in s["name"])]
    for day in history_days:
        daybars = [next((b for b in reversed(s["bars"]) if b["date"] == day), None) for s in history_stocks]
        available = [b for b in daybars if b]
        history.append({"date": day, "breadth": rounded(100 * sum(b["close"] > b["pre_close"] for b in available) / len(available), 1) if available else None, "amount_yuan": sum(b["amount_yuan"] for b in available), "sample_count": len(available)})
    return {"limit_up_confirmed": len(ups), "limit_down_confirmed": sum(r["limit_status"] == "D" for r in active), "broken_confirmed": len(broken), "limit_status_known": len(known_limits), "limit_status_coverage": rounded(100 * len(known_limits) / len(active), 1) if active else None, "universe_count": len(dataset["stocks"]), "eligible_count": len(eligible), "active_count": len(active), "excluded_st": len(rows) - len(eligible), "missing_or_conflict": len(eligible) - len(active), "advancing": up_count, "declining": down_count, "unchanged": len(active) - up_count - down_count, "breadth": rounded(breadth * 100, 1), "amount_yuan": sum(r["current"]["amount_yuan"] for r in active), "limit_up": len(ups) if len(known_limits) == len(active) else None, "limit_down": sum(r["limit_status"] == "D" for r in active) if len(known_limits) == len(active) else None, "broken": len(broken) if len(known_limits) == len(active) else None, "broken_rate": rounded(100 * len(broken) / len(touches), 1) if touches and len(known_limits) == len(active) else None, "height": max((r["consecutive"] for r in ups), default=0) if ladders_complete else None, "confirmed_height": max((r["consecutive"] for r in known_ups), default=None), "height_complete": ladders_complete, "ladder": ladder, "promotion_rate": rounded(promotion, 1), "promotion_denominator": len(prev_cohort) if dataset["meta"].get("previous_limit_complete") else None, "phase": phase, "history": history, "history_sample_count": len(history_stocks), "history_note": "固定的已补齐当前样本的历史广度；未还原逐日历史股票池/ST状态，不用于历史市场回测"}


def analyze(dataset, options=None):
    config = settings(options)
    day = dataset["meta"]["trade_date"]
    rows = [stock_row(s, day) for s in dataset["stocks"]]
    # Market statistics must use quote-quality checks only, never stock-screen
    # thresholds. Otherwise changing a minimum score would change market breadth.
    market = market_metrics(dataset, rows)
    sectors = sector_metrics(dataset, rows, config["sector_type"])
    available_rows = [r for r in rows if r["current"] and not r["is_st"] and not r["exclusions"] and r["verification"].get("status") != "conflict"]
    peers = {}
    for row in available_rows:
        peers.setdefault(row["board"] or "未知板块", []).append(row)
    weights = WEIGHTS[config["style"]]
    for row in rows:
        current = row["current"]
        if not current:
            continue
        tech, exclusions, risks = row["technical"], row["exclusions"], row["risk_flags"]
        memberships = [s for s in sectors if row["code"] in s["members"]]
        row["sector_names"] = [s["name"] for s in memberships]
        valid = [s for s in memberships if s["score"] is not None]
        best = max(valid, key=lambda s: s["score"]) if valid else None
        row["primary_sector"] = best["name"] if best else (row["sector_names"][0] if memberships else "未分类")
        row["sector_score"] = best["score"] if best else None
        if row["is_st"]:
            exclusions.append("ST / 退市风险名称过滤")
        if row["verification"].get("status") == "conflict":
            exclusions.append("主源与参考收盘价冲突")
        if not best:
            exclusions.append("板块数据不足，无法计算可比强度")
        elif best["score"] < config["min_sector_score"]:
            exclusions.append("板块强度低于门槛")
        if current["amount_yuan"] < config["min_amount"]:
            exclusions.append("成交额低于流动性门槛")
        turnover = current.get("turnover_rate")
        if turnover is None:
            exclusions.append("缺少换手率")
        elif not config["min_turnover"] <= turnover <= config["max_turnover"]:
            exclusions.append("换手率超出筛选区间")
        if tech["sessions"] < 60:
            exclusions.append("有效交易记录不足60根")
        age_reference = row.get("list_date") or row.get("listed_before")
        if age_reference:
            try:
                if (date.fromisoformat(day) - date.fromisoformat(age_reference)).days < 90:
                    exclusions.append("上市未满90个自然日")
            except ValueError:
                exclusions.append("上市日期无效")
        else:
            exclusions.append("缺少上市日期或至少90个自然日的可验证行情")
        if tech["ma20"] is None or tech["volume_ratio"] is None or tech["return5"] is None:
            exclusions.append("复权或历史行情不足，关键指标不可用")
        if row["one_word"]:
            risks.append("一字涨停，成交可得性低")
            if not config["include_one_word"]:
                exclusions.append("一字板默认仅作市场高度观察")
        if row["limit_status"] is None:
            exclusions.append("涨跌停状态未核验")
        if row["consecutive"] is None:
            exclusions.append("连板高度缺失，短线地位不可计分")
        if row["verification"].get("status") == "single_source":
            risks.append("单源行情，收盘价待交叉核验")
        if not row["announcements"]:
            risks.append("公告待核验")
        for event in row["announcements"]:
            if event.get("risk"):
                risks.append(str(event.get("title", "公告风险")))
        if tech["macd"] is None:
            risks.append("MACD预热不足120根，暂不展示")
        row["role"] = "连板观察" if (row["consecutive"] or 0) >= 2 else ("首板观察" if row["limit_status"] == "U" else ("趋势突破" if tech["breakout20"] else "趋势强势"))
        if best and tech["ma20"] is not None and tech["return5"] is not None and tech["volume_ratio"] is not None and row["consecutive"] is not None:
            group = peers.get(row["board"] or "未知板块", [])
            pct_rank = percentile([r["pct_chg"] for r in group], row["pct_chg"])
            return_rank = percentile([r["technical"]["return5"] for r in group if r["technical"]["return5"] is not None], tech["return5"])
            span = current["high"] - current["low"]
            position = (current["close"] - current["low"]) / span if span > .005 else (1 if row["limit_status"] == "U" else .5)
            trend_passes = [current["close"] > tech[f"ma{n}"] for n in (5, 10, 20)] + [bool(tech["breakout20"])]
            factors = {"sector": best["score"] / 100, "price": .5 * position + .5 * pct_rank, "volume": clamp((tech["volume_ratio"] - .5) / 1.5), "trend": sum(trend_passes) / 4, "status": .7 * clamp(row["consecutive"] / 4) + .3 * return_rank}
            labels = {"sector": "板块共振", "price": "价格强度", "volume": "量能活跃", "trend": "趋势结构", "status": "短线地位"}
            evidence = {"sector": f"{best['name']} 强度 {best['score']}", "price": f"收盘位置 {position*100:.0f}% · 同板涨幅分位 {pct_rank*100:.0f}%", "volume": f"今日成交量 / 前20根均量 {tech['volume_ratio']:.2f}倍", "trend": f"均线及突破条件 {sum(trend_passes)}/4 项", "status": f"确认连板 {row['consecutive']} · 同板5日动量分位 {return_rank*100:.0f}%"}
            row["components"] = [{"key": k, "label": labels[k], "earned": round(factors[k] * weights[k], 1), "max": weights[k], "evidence": evidence[k]} for k in weights]
            penalties = []
            if row["one_word"]:
                penalties.append({"label": "一字板成交约束", "points": 15})
            if tech["extension20"] is not None and tech["extension20"] > 25:
                penalties.append({"label": "偏离20日均线超过25%", "points": 10})
            if tech["volume_ratio"] > 5:
                penalties.append({"label": "放量超过前20根均量5倍", "points": 8})
            if (row["consecutive"] or 0) >= 5:
                penalties.append({"label": "5板及以上，高位分歧", "points": 5})
            if (current["high"] / current["close"] - 1) * 100 > 5:
                penalties.append({"label": "距日内高点回落超过5%", "points": 8})
            row["penalties"] = penalties
            risks.extend(p["label"] for p in penalties)
            row["score"] = max(0, round(sum(c["earned"] for c in row["components"]) - sum(p["points"] for p in penalties), 1))
            row["reasons"] = [c["evidence"] for c in row["components"] if c["earned"] >= c["max"] * .6]
            if row["score"] < config["min_score"]:
                exclusions.append("综合评分低于门槛")
        else:
            exclusions.append("评分关键输入缺失")
        row["selected"] = not exclusions
        row["confirmation"] = f"观察{row['primary_sector']}是否保持扩散，个股能否保持强于同板股票；突破需结合次日成交量确认。"
        row["invalidation"] = "板块强度明显回落、个股跌破前日低点且不能修复，或出现影响交易逻辑的公告。"
        row["observation_prices"] = {"high": current["high"], "low": current["low"], "note": "仅为已验证日线的观察参照，除权时需重新校准；不保证止损成交"}
        row["risk_flags"] = list(dict.fromkeys(risks))
    rows.sort(key=lambda r: (r["selected"], r["score"] is not None, r["score"] or 0), reverse=True)
    candidates = [r for r in rows if r["selected"]]
    quality = {"matched": sum(r["verification"].get("status") == "matched" for r in rows), "conflicts": sum(r["verification"].get("status") == "conflict" for r in rows), "single_source": sum(r["verification"].get("status") == "single_source" for r in rows), "demo": dataset["meta"].get("mode") == "demo", "expected_count": dataset["meta"].get("expected_count"), "actual_count": len(rows), "complete": dataset["meta"].get("complete", False), "missing_adjustment": sum(r["technical"].get("ma20") is None for r in rows), "announcements_checked": sum(bool(r["announcements"]) for r in rows)}
    narrative = []
    ranked = [s for s in sectors if s["score"] is not None]
    if ranked:
        first = ranked[0]
        narrative.append(f"样本中 {first['name']} 综合强度居前：等权涨幅 {first['avg_pct']:+.2f}%，上涨占比 {first['breadth']:.1f}%，收盘涨停 {first['limit_up']} 只。")
    narrative.append(f"按当前规则筛得 {len(candidates)} 只观察候选；评分是透明规则排序，尚未经过收益有效性验证。")
    if not ranked:
        narrative.append("板块关键数据不足，暂不输出可比排名和选股结果。")
    if quality["single_source"]:
        narrative.append("当前存在单源行情；可导入同日参考收盘价进行核对。")
    return {"meta": dataset["meta"], "config": config, "weights": weights, "market": market, "provider_sector_ranking": dataset.get("provider_sector_ranking", []), "sector_leaders": daily_sector_leaders(dataset, sectors, config["sector_type"]), "sectors": sectors, "stocks": rows, "candidates": [r["code"] for r in candidates], "quality": quality, "narrative": narrative, "methodology": {"version": "1.0.0", "sector": "35×截断((等权涨幅+3)/8) + 35×上涨比例 + 30×截断(涨停比例/12%)，截断范围0～1；可比成交额倍数仅作补充", "price": "50%收盘在日内区间的位置 + 50%同交易板块涨幅分位", "volume": "截断((今日量/前20根均量−0.5)/1.5)；超过5倍另扣8分", "trend": "收盘高于复权MA5/10/20及突破前20根复权最高价，各占25%", "status": "70%×截断(确认连板数/4) + 30%同交易板块5日动量分位", "missing": "缺少关键指标不归一化、不加分；板块不可比较时不排名", "scope": "排除ST/退市风险名称；板块分类分开展示；题材重叠不累加为全市场股票数", "notes": ["涨停以供应商涨停榜或实际涨停价确认，禁止用涨幅阈值替代", "量比为日线成交量相对前20根均量，不是行情终端的分时量比", "暂无独立公告搜索、筹码分布与真实资金身份判断", "当前未提供策略回测、收益预测或自动交易"]}}


def stock_detail(dataset, code):
    stock = next((s for s in dataset["stocks"] if s["code"] == code), None)
    if not stock:
        raise ValueError("未找到该证券")
    bars = stock["bars"]
    anchor = bars[-1].get("adj_factor") if bars else None
    adjusted = bool(anchor and anchor > 0)
    chart = []
    for index, bar in enumerate(bars):
        if adjusted and bar.get("adj_factor") is None:
            continue
        factor = bar["adj_factor"] / anchor if adjusted else 1
        item = {"date": bar["date"], **{k: bar[k] * factor for k in ("open", "high", "low", "close")}, "volume": bar["volume_shares"], "raw_close": bar["close"]}
        for span in (5, 10, 20):
            previous = bars[max(0, index-span+1):index+1]
            item[f"ma{span}"] = mean([b["close"] * b["adj_factor"] / anchor for b in previous]) if adjusted and len(previous) == span and all(b.get("adj_factor") is not None for b in previous) else None
        chart.append(item)
    receipt = stock.get("history_fetch") or {}
    complete = receipt.get("date") == dataset["meta"]["trade_date"] and receipt.get("status") == "complete"
    needs_history = not complete and (not adjusted or len(chart) < 120)
    return {"code": code, "name": stock["name"], "trade_date": dataset["meta"]["trade_date"],
            "bars": chart[-60:], "technical": indicators(bars), "adjustment": "qfq" if adjusted else "raw",
            "needs_history": needs_history and dataset["meta"].get("mode") != "demo", "history_fetch": receipt,
            "snapshot_revision": dataset["meta"].get("history_updated_at") or dataset["meta"].get("fetched_at"),
            "adjustment_note": "以分析日复权因子为锚的前复权序列；原始收盘价保留用于核验" if adjusted else "不复权行情；复权数据未齐，暂不显示跨期均线",
            "chip_status": "筹码峰待核验；未用日线成交分布冒充实际持仓成本"}
