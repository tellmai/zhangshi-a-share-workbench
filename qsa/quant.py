"""Transparent rule strategies and daily-bar research replay, never live orders."""
from __future__ import annotations

import bisect
import math
import re
from collections import Counter

from .analytics import analyze
from .data import iso_date

ENGINE_VERSION = "1.0.0"
DEFAULTS = {
    "template": "trend", "min_amount": 200000000, "min_turnover": 1, "max_turnover": 30,
    "min_volume_ratio": 1.2, "max_volume_ratio": 5, "min_return5": 0,
    "max_extension20": 25, "close_position_min": .55, "ma_alignment": True,
    "ma20_rising": False, "breakout_days": 20, "pullback_ma": 0, "pullback_distance_pct": 3,
    "min_boards": 0, "sector_min": 0, "sector_type": "industry", "ranking": "trend", "min_bars": 60,
    "capital": 100000, "max_positions": 5, "holding_days": 3,
    "stop_loss_pct": 6, "take_profit_pct": 15,
    "commission_bps": 3, "minimum_commission": 5, "stamp_bps": 5,
    "transfer_bps": .1, "slippage_bps": 5, "strict_limits": False,
}
PRESETS = {
    "trend": {"name": "趋势突破", "tag": "先从这里开始", "description": "均线顺向、收盘突破前20根高点，配合温和放量。", "config": {}},
    "volume": {"name": "放量上攻", "tag": "量价共振", "description": "关注有5日动量、成交放大且收盘位置较高的股票。", "config": {
        "ma_alignment": False, "ma20_rising": True, "breakout_days": 0, "min_volume_ratio": 1.5,
        "min_return5": 3, "close_position_min": .75, "ranking": "momentum"}},
    "pullback": {"name": "强势回踩", "tag": "趋势中的整理", "description": "20日均线向上，价格靠近10日均线，成交量较前20根均量收缩。", "config": {
        "ma_alignment": False, "ma20_rising": True, "breakout_days": 0, "min_volume_ratio": .3,
        "max_volume_ratio": 1.2, "min_return5": -5, "close_position_min": .3,
        "pullback_ma": 10, "pullback_distance_pct": 3, "max_extension20": 15, "ranking": "pullback"}},
    "relay": {"name": "连板观察", "tag": "当前快照可运行", "description": "用已核验的连续涨停记录筛选，历史回测需要逐日连板资料。", "config": {
        "ma_alignment": False, "breakout_days": 0, "min_volume_ratio": .1,
        "max_volume_ratio": 10, "min_boards": 2, "max_extension20": 60, "ranking": "relay"}},
}


def config_settings(value=None):
    value = {} if value is None else value
    if not isinstance(value, dict) or set(value)-set(DEFAULTS):
        raise ValueError("策略参数包含不支持的字段")
    template = value.get("template", "trend")
    if template not in PRESETS:
        raise ValueError("未知策略模板")
    cfg = {**DEFAULTS, **PRESETS[template]["config"], "template": template, **value}
    bounds = {
        "min_amount": (0,1e12), "min_turnover": (0,100), "max_turnover": (0,100),
        "min_volume_ratio": (0,30), "max_volume_ratio": (0,30), "min_return5": (-50,100),
        "max_extension20": (0,200), "close_position_min": (0,1), "pullback_distance_pct": (0,30),
        "sector_min": (0,100), "capital": (1000,1e8), "stop_loss_pct": (0,50),
        "take_profit_pct": (0,200), "commission_bps": (0,30), "minimum_commission": (0,100),
        "stamp_bps": (0,100), "transfer_bps": (0,10), "slippage_bps": (0,100),
    }
    for key, (lo,hi) in bounds.items():
        x = cfg[key]
        if isinstance(x, bool) or not isinstance(x, (int,float)) or not math.isfinite(x) or not lo <= x <= hi:
            raise ValueError(f"参数{key}须在{lo}—{hi}之间")
    for key, lo, hi in (("min_bars",21,250),("min_boards",0,20),("max_positions",1,20),("holding_days",1,30),("breakout_days",0,60)):
        if type(cfg[key]) is not int or not lo <= cfg[key] <= hi:
            raise ValueError(f"参数{key}须为{lo}—{hi}之间的整数")
    for key in ("ma_alignment", "ma20_rising", "strict_limits"):
        if type(cfg[key]) is not bool:
            raise ValueError(f"参数{key}必须是开关值")
    if cfg["min_turnover"] > cfg["max_turnover"] or cfg["min_volume_ratio"] > cfg["max_volume_ratio"]:
        raise ValueError("筛选下限不能大于上限")
    if cfg["pullback_ma"] not in (0,5,10,20) or isinstance(cfg["pullback_ma"], bool):
        raise ValueError("回踩均线只支持关闭、5、10、20日")
    if cfg["sector_type"] not in ("industry", "theme") or cfg["ranking"] not in ("trend","momentum","pullback","relay"):
        raise ValueError("无效的板块分类或排序方式")
    return cfg


def preset_catalog():
    return {key:{**value, "config": config_settings({"template": key})} for key,value in PRESETS.items()}


def universe_ok(stock, demo=False):
    return (not stock.get("is_st") and "ST" not in stock["name"].upper() and "退" not in stock["name"]
        and (demo or bool(re.fullmatch(r"(?:60[0135]\d{3}\.SH|00[0123]\d{3}\.SZ|30[01]\d{3}\.SZ)", stock["code"]))))


def required_bars(cfg):
    return max(cfg["min_bars"], cfg["breakout_days"]+1, 25 if cfg["ma20_rising"] else 21)


def features(bars, cfg):
    if len(bars) < required_bars(cfg):
        return None, "历史日线不足"
    tail = bars[-max(25,cfg["breakout_days"]+1):]
    if any(not b.get("adj_factor") for b in tail):
        return None, "复权因子缺失"
    anchor = bars[-1]["adj_factor"]
    closes = [b["close"]*b["adj_factor"]/anchor for b in tail]
    current = bars[-1]
    volume_base = sum(b["volume_shares"] for b in bars[-21:-1])/20
    if not volume_base or current.get("turnover_rate") is None:
        return None, "量能或换手率缺失"
    ma = {n:sum(closes[-n:])/n for n in (5,10,20)}
    span = current["high"]-current["low"]
    high = max(b["high"]*b["adj_factor"]/anchor for b in tail[-cfg["breakout_days"]-1:-1]) if cfg["breakout_days"] else None
    selected_ma = ma.get(cfg["pullback_ma"], ma[10])
    return {"close":current["close"], "pct":(current["close"]/current["pre_close"]-1)*100,
        "amount":current["amount_yuan"], "turnover":current["turnover_rate"],
        "volume_ratio":current["volume_shares"]/volume_base, "return5":(closes[-1]/closes[-6]-1)*100,
        "return20":(closes[-1]/closes[-21]-1)*100, "ma5":ma[5], "ma10":ma[10], "ma20":ma[20],
        "ma20_rising":len(closes)>=25 and ma[20] > sum(closes[-25:-5])/20,
        "ma_alignment":current["close"]>ma[5]>ma[10]>ma[20],
        "extension20":(current["close"]/ma[20]-1)*100,
        "close_position":(current["close"]-current["low"])/span if span>.000001 else 1,
        "breakout":current["close"]>high+1e-6 if high is not None else None,
        "distance_ma":(current["close"]/selected_ma-1)*100}, None


def evaluate(stock, bars, cfg, *, boards=None, sector_score=None):
    f, error = features(bars, cfg)
    if error:
        return None, [error]
    checks = [(f["amount"]>=cfg["min_amount"],"成交额不足"),
        (cfg["min_turnover"]<=f["turnover"]<=cfg["max_turnover"],"换手率不符"),
        (cfg["min_volume_ratio"]<=f["volume_ratio"]<=cfg["max_volume_ratio"],"20日均量倍数不符"),
        (f["return5"]>=cfg["min_return5"],"5日动量不足"),
        (f["extension20"]<=cfg["max_extension20"],"偏离20日均线过大"),
        (f["close_position"]>=cfg["close_position_min"],"收盘位置不足")]
    if cfg["ma_alignment"]: checks.append((f["ma_alignment"],"均线未顺向排列"))
    if cfg["ma20_rising"]: checks.append((f["ma20_rising"],"20日均线未向上"))
    if cfg["breakout_days"]: checks.append((f["breakout"],"未突破前期高点"))
    if cfg["pullback_ma"]: checks.append((abs(f["distance_ma"])<=cfg["pullback_distance_pct"],"距离回踩均线过远"))
    if cfg["min_boards"]: checks.append((boards is not None and boards>=cfg["min_boards"],"已核验连板数不足"))
    if cfg["sector_min"]: checks.append((sector_score is not None and sector_score>=cfg["sector_min"],"板块强度不足或缺失"))
    failures = [message for passed,message in checks if not passed]
    clip = lambda x:max(0,min(1,x))
    trend = sum(f["close"]>f[f"ma{n}"] for n in (5,10,20))/3
    if cfg["ranking"] == "pullback":
        score = 40*clip(1-abs(f["distance_ma"])/max(cfg["pullback_distance_pct"],.01))+30*trend+20*clip(f["return20"]/20)+10*clip((1.5-f["volume_ratio"])/1.5)
    elif cfg["ranking"] == "relay":
        score = 40*clip((boards or 0)/5)+25*trend+20*clip(f["return5"]/20)+15*f["close_position"]
    elif cfg["ranking"] == "momentum":
        score = 35*clip(f["return5"]/15)+25*clip(f["volume_ratio"]/3)+20*f["close_position"]+20*trend
    else:
        score = 30*trend+25*clip(f["return5"]/15)+20*clip(f["volume_ratio"]/3)+15*f["close_position"]+10*clip(f["return20"]/30)
    row = {"code":stock["code"], "name":stock["name"], "score":round(score,2),
        "features":{k:round(v,5) if isinstance(v,float) else v for k,v in f.items()},
        "passed":not failures, "reasons":["满足已启用的全部条件"] if not failures else failures,
        "boards":boards, "sector_score":sector_score}
    return row, failures


def dataset_info(dataset):
    demo = dataset["meta"].get("mode")=="demo"
    pool = [s for s in dataset["stocks"] if universe_ok(s,demo)]
    calendar = dataset["meta"]["calendar"]
    return {"trade_date":dataset["meta"]["trade_date"], "mode":"demo" if demo else "active",
        "source":dataset["meta"]["source"], "total":len(dataset["stocks"]), "universe":len(pool),
        "history_60":sum(len(s["bars"])>=60 for s in pool), "history_120":sum(len(s["bars"])>=120 for s in pool),
        "default_start":calendar[max(0,len(calendar)-61)], "default_end":dataset["meta"]["trade_date"],
        "sample_warning":"合成演示数据，价格与收益均非真实" if demo else "当前已下载样本，存在选样与幸存者偏差，不能视为全市场回测"}


def scan(dataset, config, progress=lambda *_:None, cancelled=lambda:False):
    cfg = config_settings(config)
    day, demo = dataset["meta"]["trade_date"], dataset["meta"].get("mode")=="demo"
    rows, rejected, counts = [], [], Counter()
    scores = {}
    if cfg["sector_min"]:
        analysis = analyze(dataset,{"sector_type":cfg["sector_type"]})
        scores = {s["code"]:s["sector_score"] for s in analysis["stocks"]}
    pool = [s for s in dataset["stocks"] if universe_ok(s,demo)]
    history_count = 0
    for index, stock in enumerate(pool):
        if cancelled(): raise InterruptedError("运行已取消")
        if index%200==0: progress(int(index/max(1,len(pool))*90),f"检查规则 {index}/{len(pool)}")
        bars = stock["bars"]
        verification=stock.get("verification",{})
        if not bars or bars[-1]["date"]!=day or (not demo and (verification.get("status")!="matched" or verification.get("date")!=day)):
            counts["当日收盘未完成核对"]+=1
            continue
        boards = (stock.get("limit") or {}).get("consecutive") if (stock.get("limit") or {}).get("status")=="U" else 0
        row, failures = evaluate(stock,bars,cfg,boards=boards,sector_score=scores.get(stock["code"]))
        if row is not None: history_count+=1
        if failures:
            counts[failures[0]]+=1
            if len(rejected)<30: rejected.append({"code":stock["code"],"name":stock["name"],"reasons":failures})
        else: rows.append(row)
    rows.sort(key=lambda r:(-r["score"],r["code"]))
    return {"type":"scan", "status":"completed", "trade_date":day, "config":cfg, "signals":rows,
        "shortlist":rows[:cfg["max_positions"]], "rejected_examples":rejected,
        "funnel":{"total":len(dataset["stocks"]),"universe":len(pool),"history_ready":history_count,"passed":len(rows),"shortlist":min(len(rows),cfg["max_positions"])},
        "exclusions":dict(counts), "warnings":["信号在收盘后形成，作为下一交易日的观察名单，不代表已经成交。", dataset_info(dataset)["sample_warning"],
          "排名分是规则排序，不是上涨概率；公告和除权事件仍需复核。"]}


def cost(notional, side, cfg):
    return {"commission":max(cfg["minimum_commission"],notional*cfg["commission_bps"]/10000),
        "stamp":notional*cfg["stamp_bps"]/10000 if side=="sell" else 0,
        "transfer":notional*cfg["transfer_bps"]/10000, "slippage":notional*cfg["slippage_bps"]/10000}


def fill_block(bar, side, strict=False, price=None):
    if not bar or bar["volume_shares"]<=0: return "无有效成交日线"
    price = bar["open"] if price is None else price
    upper,lower = bar.get("up_limit"),bar.get("down_limit")
    if strict and (upper is None or lower is None): return "缺少历史涨跌停价，严格模式跳过"
    if side=="buy" and upper is not None and price>=upper-.0051: return "涨停价买入不假设能够成交"
    if side=="sell" and lower is not None and price<=lower+.0051: return "跌停价卖出不假设能够成交"
    if abs(bar["high"]-bar["low"])<.0051 and (upper is None or lower is None): return "单一成交价且涨跌停信息缺失，保守跳过"
    return None


def adjustment_changed(a,b):
    if not a.get("adj_factor") or not b.get("adj_factor"): return True
    # Tencent's qfq close is rounded to cents, so division introduces small noise.
    tolerance = .011/a["close"] + .011/b["close"] + 1e-6
    return abs(b["adj_factor"]/a["adj_factor"]-1)>tolerance


def replay(dataset, config, start, end, progress=lambda *_:None, cancelled=lambda:False):
    cfg = config_settings(config)
    start,end = iso_date(start),iso_date(end)
    if start>end or end>dataset["meta"]["trade_date"]: raise ValueError("回测日期顺序无效或超过快照日期")
    if cfg["min_boards"] or cfg["sector_min"] or cfg["ranking"]=="relay":
        raise ValueError("此策略使用连板或板块强度；缺少逐日同口径历史记录，当前支持运行当日筛选，暂不能回测。请关闭这两项或选择趋势模板。")
    demo = dataset["meta"].get("mode")=="demo"
    pool = [s for s in dataset["stocks"] if universe_ok(s,demo) and len(s["bars"])>=required_bars(cfg)]
    if not pool: raise ValueError("没有满足历史长度的股票，请先打开个股补齐历史或使用合成演示数据")
    if len(pool)>500: raise ValueError("当前回放最多500只完整历史样本，请先缩小数据范围")
    days = [d for d in dataset["meta"]["calendar"] if start<=d<=end]
    if len(days)<3 or len(days)>252: raise ValueError("回放区间需要3—252个已有交易日")
    dates = {s["code"]:[b["date"] for b in s["bars"]] for s in pool}
    by_code = {s["code"]:s for s in pool}
    maps = {s["code"]:{b["date"]:b for b in s["bars"]} for s in pool}
    cash = float(cfg["capital"])
    positions,pending,curve,trades,orders = {},[],[],[],[]
    fees = {"commission":0.,"stamp":0.,"transfer":0.,"slippage":0.}
    skips,signal_counts = Counter(),[]
    peak,max_dd = cash,0.
    last_prices,base_prices,baseline_prices = {},{},{}
    warnings = [dataset_info(dataset)["sample_warning"],
        "仅用信号日及以前的日线计算条件，下一交易日开盘模拟执行；遵守T+1和100股整数手。",
        "当前成分和ST标签不代表历史股票池；个股历史未逐日跨源核验，公告风险未还原。",
        "日线无法还原盘口与盘中路径；缺历史涨跌停价的非单价日按假设成交，收益不可视为实盘可实现结果。",
        "不强行平掉期末持仓；停牌或缺报价时按最近可用收盘估值。遇持仓除权跳变且事件资料不足时中止。",
        "佣金、过户费、印花税和滑点均为可修改的研究假设；佣金含其他费用时应避免重复计费。"]
    failure = None

    def charge(value):
        for k,v in value.items(): fees[k]+=v
        return sum(value.values())

    def sell(code,bar,price,reason,day,index):
        nonlocal cash
        block = fill_block(bar,"sell",cfg["strict_limits"],price)
        if block:
            skips[block]+=1
            return False
        p = positions.pop(code)
        notional = p["shares"]*price
        outgoing = cost(notional,"sell",cfg)
        proceeds = notional-charge(outgoing)
        cash+=proceeds
        pnl = proceeds-p["cost"]
        trades.append({"code":code,"name":by_code[code]["name"],"signal_date":p["signal_date"],"entry_date":p["entry_date"],
            "exit_date":day,"entry_price":p["price"],"exit_price":price,"shares":p["shares"],
            "holding_sessions":index-p["entry_index"],"entry_cost":p["cost"],"exit_proceeds":proceeds,
            "pnl":round(pnl,2),"return_pct":round(pnl/p["cost"]*100,3),"reason":reason,
            "fees":round(sum(p["fees"].values())+sum(outgoing.values()),3)})
        orders.append({"date":day,"code":code,"side":"sell","shares":p["shares"],"price":price,"reason":reason})
        return True

    for index,day in enumerate(days):
        if cancelled(): raise InterruptedError("运行已取消")
        progress(int(index/len(days)*95),f"逐日回放 {index+1}/{len(days)} · {day}")
        # Corporate-action ambiguity cannot be repaired by inventing cash or shares.
        for code,p in positions.items():
            bar = maps[code].get(day)
            prev_index = bisect.bisect_left(dates[code],day)-1
            if bar and prev_index>=0 and adjustment_changed(by_code[code]["bars"][prev_index],bar):
                failure = f"{day} {by_code[code]['name']}持仓期间出现复权因子跳变，缺少可核实分红送股资料，已中止资金回放；不输出完整区间收益。"
                break
        if failure: break
        # Exits planned at the preceding close; no same-day resale of new buys.
        for code,p in list(positions.items()):
            bar = maps[code].get(day)
            if index>=p["due_index"]:
                if not bar: skips["到期退出日无报价，等待下一有效日"]+=1
                else: sell(code,bar,bar["open"],"持有期到期",day,index)
        equity_before = cash+sum(p["shares"]*last_prices.get(c,p["price"]) for c,p in positions.items())
        for signal in pending:
            code = signal["code"]
            if code in positions: continue
            if len(positions)>=cfg["max_positions"]: skips["仓位已满"]+=1;continue
            bar = maps[code].get(day)
            block = fill_block(bar,"buy",cfg["strict_limits"])
            if block: skips[block]+=1;continue
            budget = min(cash,equity_before/cfg["max_positions"])
            shares = math.floor(budget/(bar["open"]*100))*100
            while shares>0:
                notional = shares*bar["open"]
                incoming = cost(notional,"buy",cfg)
                if notional+sum(incoming.values())<=budget+1e-8: break
                shares-=100
            if shares<=0: skips["资金不足一手或费用预算不足"]+=1;continue
            paid = notional+charge(incoming)
            cash-=paid
            positions[code] = {"shares":shares,"price":bar["open"],"cost":paid,"fees":incoming,
                "entry_date":day,"signal_date":signal["date"],"entry_index":index,"due_index":index+cfg["holding_days"]}
            last_prices[code]=bar["open"]
            orders.append({"date":day,"code":code,"side":"buy","shares":shares,"price":bar["open"],"reason":"上一交易日收盘信号"})
        for code,p in list(positions.items()):
            if p["entry_index"]>=index: continue
            bar = maps[code].get(day)
            if not bar: continue
            stop = p["price"]*(1-cfg["stop_loss_pct"]/100) if cfg["stop_loss_pct"] else None
            take = p["price"]*(1+cfg["take_profit_pct"]/100) if cfg["take_profit_pct"] else None
            if stop and bar["open"]<=stop:
                sell(code,bar,bar["open"],"止损跳空，按开盘价模拟",day,index)
            elif take and bar["open"]>=take:
                sell(code,bar,bar["open"],"止盈跳空，按开盘价模拟",day,index)
            elif stop and bar["low"]<=stop:
                sell(code,bar,stop,"止损触发；同日双触发时取止损",day,index)
            elif take and bar["high"]>=take:
                sell(code,bar,take,"止盈触发",day,index)
        # Price marks and fixed-start sample reference; neither drives today's entry.
        for stock in pool:
            code = stock["code"]
            bar = maps[code].get(day)
            if bar:
                last_prices[code]=bar["close"]
                if index==0 and bar.get("adj_factor") and bisect.bisect_right(dates[code],day)>=required_bars(cfg):
                    base_prices[code]=bar["close"]*bar.get("adj_factor",1)
                if code in base_prices and bar.get("adj_factor"):
                    baseline_prices[code]=bar["close"]*bar.get("adj_factor",1)/base_prices[code]
        invested = sum(p["shares"]*last_prices.get(code,p["price"]) for code,p in positions.items())
        equity = cash+invested
        peak=max(peak,equity);dd=(equity/peak-1)*100;max_dd=min(max_dd,dd)
        reference = sum(baseline_prices.values())/len(base_prices) if base_prices else None
        curve.append({"date":day,"equity":round(equity,4),"net_value":round(equity/cfg["capital"],6),
            "drawdown_pct":round(dd,4),"cash":round(cash,4),"positions":len(positions),
            "exposure_pct":round(invested/equity*100,2) if equity else 0,
            "reference":round(reference,6) if reference is not None else None})
        pending=[]
        for stock in pool:
            code=stock["code"]
            if day not in maps[code]: continue
            end_index = bisect.bisect_right(dates[code],day)
            row,failures = evaluate(stock,stock["bars"][:end_index],cfg)
            if row is not None and not failures: pending.append({"code":code,"score":row["score"],"date":day})
        pending.sort(key=lambda x:(-x["score"],x["code"]))
        signal_counts.append({"date":day,"count":len(pending)})
    if not curve: raise ValueError("区间内没有可计算的日线")
    final = curve[-1]
    metrics = {"total_return_pct":round((final["net_value"]-1)*100,3),"max_drawdown_pct":round(-max_dd,3),
        "final_equity":round(final["equity"],2),"closed_trades":len(trades),
        "win_rate_pct":round(sum(t["pnl"]>0 for t in trades)/len(trades)*100,2) if trades else None,
        "mean_trade_return_pct":round(sum(t["return_pct"] for t in trades)/len(trades),3) if trades else None,
        "total_fees":round(sum(fees.values()),3),"open_positions":len(positions),
        "reference_return_pct":round((final["reference"]-1)*100,3) if final["reference"] is not None else None}
    open_rows = [{"code":c,"name":by_code[c]["name"],"shares":p["shares"],"entry_date":p["entry_date"],
        "entry_price":p["price"],"mark_price":last_prices.get(c,p["price"])} for c,p in positions.items()]
    return {"type":"backtest", "status":"incomplete" if failure else "completed", "failure":failure,
        "start":start,"end":end,"computed_through":final["date"],"config":cfg,
        "metrics":None if failure else metrics,"curve":curve,"trades":trades,"orders":orders,
        "fees":{k:round(v,3) for k,v in fees.items()},"skips":dict(skips),"open_positions":open_rows,
        "signal_counts":signal_counts,"pending_signals":len(pending),"sample_count":len(pool),
        "reference_count":len(base_prices),"reference_name":"同样本等权观察线（起点收盘持有、不计费，非指数）",
        "warnings":warnings+([failure] if failure else [])}
