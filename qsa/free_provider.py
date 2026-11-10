"""Free public feeds, with bounded requests, dated raw evidence and no trading APIs.

Endpoint structures follow AKShare's public upstream adapters. These are unofficial
public feeds, not exchange-certified services. Missing data stays missing.
"""
from __future__ import annotations

import json
import math
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .data import SHANGHAI, atomic_json, iso_date, now_iso, validate_dataset

UA = {"User-Agent": "Mozilla/5.0 (compatible; AfterCloseResearch/0.1)", "Referer": "https://quote.eastmoney.com/"}
SOURCE_URLS = ["https://quote.eastmoney.com/center/gridlist.html#hs_a_board", "https://quote.eastmoney.com/center/boardlist.html#industry_board", "https://quote.eastmoney.com/center/boardlist.html#concept_board", "https://quote.eastmoney.com/ztb/detail#type=ztgc", "https://gu.qq.com/", "https://akshare.akfamily.xyz/data/stock/stock.html"]


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def get_text(url, encoding="utf-8"):
    headers = {**UA, "Referer": "https://finance.sina.com.cn/"} if "sinajs.cn" in url else UA
    with urlopen(Request(url, headers=headers), timeout=18) as response:
        return response.read(12 * 1024 * 1024).decode(encoding)


def get_json(url, params=None):
    return json.loads(get_text(url + ("?" + urlencode(params) if params else "")))


def market_list(fs, day, evidence, name):
    rows, total = [], None
    for page in range(1, 151):
        payload = get_json("https://82.push2.eastmoney.com/api/qt/clist/get", {"pn": page, "pz": 100, "po": 1, "np": 1, "fltt": 2, "invt": 2, "fid": "f12", "fs": fs, "fields": "f2,f3,f5,f6,f8,f12,f13,f14,f15,f16,f17,f18,f20,f21,f26,f104,f105,f124"})
        data = payload.get("data") or {}
        batch = data.get("diff") or []
        if isinstance(batch, dict):
            batch = list(batch.values())
        if total is None:
            total = int(data.get("total", 0))
        elif int(data.get("total", 0)) != total:
            raise ValueError("行情分页总数变化，请稍后重试")
        if not batch:
            break
        rows.extend(batch)
        if len(rows) >= total:
            break
        time.sleep(.12)
    if not rows or len(rows) != total or len({r["f12"] for r in rows}) != total:
        raise ValueError(f"{name} 分页不完整，拒绝发布排名")
    atomic_json(evidence / f"{name}.json", {"observed_at": now_iso(), "requested_date": day, "rows": rows})
    return rows


def fresh(stamp, day):
    try:
        moment = datetime.fromtimestamp(float(stamp), SHANGHAI)
        return moment.date().isoformat() == day and moment.hour >= 15
    except (ValueError, OSError, TypeError):
        return False


def share_volume(raw_volume, amount, low, high):
    """Infer only when amount / shares lies in the actual day's price range."""
    if raw_volume <= 0 or amount <= 0:
        raise ValueError("成交量或成交额无效")
    choices = [raw_volume * m for m in (1, 100) if low * .995 <= amount / (raw_volume * m) <= high * 1.005]
    if len(choices) != 1:
        raise ValueError("成交量股/手单位不能由成交额和价格区间唯一核对")
    return choices[0]


def normalization_record(raw_volume, raw_amount, volume, amount):
    """Keep numerical conversion evidence beside standardized observations."""
    multiplier = volume / raw_volume
    return {"price_unit": "CNY", "price_multiplier": 1, "turnover_unit": "percent",
        "volume": {"raw_value": raw_volume, "raw_unit": "hand" if abs(multiplier-100)<1e-8 else "share",
            "standard_value": volume, "standard_unit": "share", "multiplier": multiplier,
            "basis": "成交额/成交量落在当日高低价内，排除歧义换算"},
        "amount": {"raw_value": raw_amount, "raw_unit": "10000_CNY" if abs(amount/raw_amount-10000)<1e-8 else "CNY",
            "standard_value": amount, "standard_unit": "CNY", "multiplier": amount/raw_amount}}


def tencent_quotes(codes, day, evidence):
    output = {}
    for offset in range(0, len(codes), 100):
        group = codes[offset:offset+100]
        symbols = [("sh" if c.endswith(".SH") else "sz") + c[:6] for c in group]
        raw = get_text("https://qt.gtimg.cn/q=" + ",".join(symbols), "gb18030")
        (evidence / f"tencent-{offset//100:03d}.txt").write_text(raw, encoding="utf-8")
        for symbol, body in re.findall(r'v_((?:sh|sz)\d{6})="([^"]*)";', raw):
            fields = body.split("~")
            if len(fields) < 49 or fields[2] != symbol[2:]:
                continue
            code = symbol[2:] + (".SH" if symbol.startswith("sh") else ".SZ")
            try:
                stamp = datetime.strptime(fields[30], "%Y%m%d%H%M%S")
                if stamp.date().isoformat() != day or stamp.hour < 15:
                    continue
                close, previous, opening, high, low = [float(fields[k]) for k in (3, 4, 5, 33, 34)]
                if min(close, previous, opening, high, low) <= 0 or not low <= min(close, opening) <= max(close, opening) <= high:
                    continue
                if abs((close / previous - 1) * 100 - float(fields[32])) > .03:
                    continue
                summary = fields[35].split("/")
                if len(summary) != 3 or abs(float(summary[0]) - close) > .005:
                    continue
                amount = float(summary[2])
                if abs(amount / 10000 - float(fields[37])) > 1.1:
                    continue
                volume = share_volume(float(summary[1]), amount, low, high)
                output[code] = {"name": fields[1], "stamp": stamp.isoformat(), "bar": {"date": day, "open": opening, "high": high, "low": low, "close": close, "pre_close": previous, "pct_chg": float(fields[32]), "volume_shares": volume, "amount_yuan": amount, "turnover_rate": number(fields[38]), "up_limit": number(fields[47]) if number(fields[47]) and float(fields[47]) > close * .5 else None, "down_limit": number(fields[48]) if number(fields[48]) and float(fields[48]) > 0 else None, "adj_factor": None}}
                output[code]["bar"]["normalization"] = normalization_record(float(summary[1]), float(summary[2]), volume, amount)
            except (ValueError, IndexError, ZeroDivisionError):
                continue
        time.sleep(.1)
    return output


def history(code, day, cache, evidence=None):
    path = cache / f"{code}-{day}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    # AKShare upstream's Eastmoney historical endpoint. Preserve raw and qfq
    # side by side; never glue a stale adjusted series to today's raw quote.
    rows = {}
    for adjust in (0, 1):
        payload = get_json("https://push2his.eastmoney.com/api/qt/stock/kline/get", {"secid": ("1." if code.endswith(".SH") else "0.") + code[:6], "klt": 101, "fqt": adjust, "beg": (datetime.fromisoformat(day) - timedelta(days=330)).strftime("%Y%m%d"), "end": day.replace("-", ""), "lmt": 200, "fields1": "f1,f2,f3,f4,f5,f6", "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"})
        if evidence is not None:
            atomic_json(evidence / f"eastmoney-{adjust}.json", payload)
        lines = (payload.get("data") or {}).get("klines") or []
        rows[adjust] = [line.split(",") for line in lines]
    adjusted = {r[0]: float(r[2]) for r in rows[1] if len(r) >= 11}
    answer = []
    for raw in rows[0]:
        if len(raw) < 11 or raw[0] > day:
            continue
        dt, opening, close, high, low, volume, amount, _, change, _, turnover = raw[:11]
        opening, close, high, low, volume, amount, change, turnover = map(float, (opening, close, high, low, volume, amount, change, turnover))
        if min(opening, close, high, low) <= 0 or change <= -100:
            continue
        adjusted_close = adjusted.get(dt)
        answer.append({"date": dt, "open": opening, "high": high, "low": low, "close": close, "pre_close": close / (1 + change / 100), "pct_chg": change, "volume_shares": share_volume(volume, amount, low, high), "amount_yuan": amount, "turnover_rate": turnover, "adj_factor": adjusted_close / close if adjusted_close and adjusted_close > 0 else None})
    if answer and answer[-1]["date"] == day:
        atomic_json(path, answer)
    return answer


def get_limit_pool(day, evidence):
    payload = get_json("https://push2ex.eastmoney.com/getTopicZTPool", {"ut": "7eea3edcaed734bea9cbfc24409ed989", "dpt": "wz.ztzt", "Pageindex": 0, "pagesize": 10000, "sort": "fbt:asc", "date": day.replace("-", "")})
    atomic_json(evidence / f"limit-{day}.json", payload)
    data = payload.get("data")
    if not data or "pool" not in data:
        raise ValueError("免费涨停池未返回有效数据")
    provider_day = data.get("qdate") or data.get("date")
    if provider_day is None or iso_date(str(provider_day)) != day:
        raise ValueError("涨停池缺少可核验同日日期，暂不使用连板/封单字段")
    output = {}
    for row in data["pool"]:
        code = str(row.get("c", ""))
        if not re.fullmatch(r"[036]\d{5}", code):
            continue
        output[code + (".SH" if code.startswith("6") else ".SZ")] = {"status": "U", "consecutive": int(row["lbc"]) if row.get("lbc") is not None else None, "first_time": str(row.get("fbt", "")).zfill(6), "last_time": str(row.get("lbt", "")).zfill(6), "open_times": row.get("zbc"), "fd_amount": row.get("fund"), "close": float(row["p"]) / 1000 if row.get("p") else None}
    return output


def fetch_eastmoney(options, root, progress):
    day = iso_date(options.get("date", ""))
    now = datetime.now(SHANGHAI)
    if day > now.date().isoformat() or (day == now.date().isoformat() and (now.hour, now.minute) < (16, 30)):
        raise ValueError("当日完整盘后分析请在上海时间16:30后更新")
    # Current constituent endpoints cannot reconstruct a historical universe.
    index_raw = get_text("https://qt.gtimg.cn/q=sh000001", "gb18030")
    match = re.search(r'v_sh000001="([^"]*)"', index_raw)
    if not match:
        raise ValueError("无法核验当前市场日期")
    stamp = match.group(1).split("~")[30]
    if iso_date(stamp[:8]) != day or int(stamp[8:10]) < 15:
        raise ValueError(f"当前免费接口的最新收盘日为{stamp[:8]}，不能把当前成分回填成历史行情；历史复盘请导入当日保存的快照")
    industry_limit, theme_limit = int(options.get("industry_limit", 12)), int(options.get("theme_limit", 8))
    history_limit = int(options.get("history_limit", 120))
    if not 1 <= industry_limit <= 100 or not 0 <= theme_limit <= 1000 or not 0 <= history_limit <= 500:
        raise ValueError("板块数量超出范围")
    root = Path(root)
    evidence = root / "evidence" / f"{day}-{now.strftime('%H%M%S%f')}"
    evidence.mkdir(parents=True, exist_ok=False)
    (evidence / "market-clock.txt").write_text(index_raw, encoding="utf-8")
    cache = root / "history"
    cache.mkdir(parents=True, exist_ok=True)
    notes = ["免费公共接口未提供交易所级服务保证；历史市场股票池未还原", "公告、筹码与龙虎榜未接入，不作主力资金身份推断", "板块指数涨幅初筛后展开成分，综合排名仅限所选研究范围"]
    progress(3, "获取完整沪深A股报价列表", {"stage": "quotes"})
    quote_rows = market_list("m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23", day, evidence, "market")
    quote_rows = [r for r in quote_rows if re.fullmatch(r"[036]\d{5}", str(r.get("f12", "")))]
    codes = [str(r["f12"]) + (".SH" if str(r["f12"]).startswith("6") else ".SZ") for r in quote_rows]
    progress(8, f"核验{len(codes)}只证券的腾讯日期、价格与成交单位", {"stage": "quotes"})
    dated = tencent_quotes(codes, day, evidence)
    if len(dated) < len(codes) * .90:
        raise ValueError(f"带日期收盘报价覆盖不足90%（{len(dated)}/{len(codes)}），拒绝生成市场分析")
    stocks = []
    lookup = {}
    for row, code in zip(quote_rows, codes):
        quote = dated.get(code)
        bar = quote["bar"] if quote else None
        reference_ok = fresh(row.get("f124"), day) and number(row.get("f2")) is not None
        verification = {"status": "single_source"}
        if quote and reference_ok:
            verification = {"status": "matched" if abs(float(row["f2"]) - bar["close"]) <= .005001 else "conflict", "source": "东方财富", "close": row["f2"], "date": day}
        stock = {"code": code, "name": quote["name"] if quote else row["f14"], "exchange": "SH" if code.endswith(".SH") else "SZ", "board": "科创板" if code.startswith("688") else ("创业板" if code.startswith(("300", "301")) else "主板"), "industry": None, "list_date": iso_date(int(row["f26"])) if number(row.get("f26")) and len(str(int(row["f26"]))) == 8 else None, "is_st": "ST" in str(row["f14"]).upper() or "退" in str(row["f14"]), "bars": [bar] if bar else [], "limit": {}, "previous_limit": {}, "verification": verification, "announcements": []}
        stocks.append(stock)
        lookup[code] = stock
    progress(22, "读取行业与题材全量列表，建立独立研究范围", {"stage": "sectors"})
    sectors, rankings, board_coverage = [], [], {}
    for kind, fs, count in (("industry", "m:90 t:2 f:!50", industry_limit), ("theme", "m:90 t:3 f:!50", theme_limit)):
        if not count:
            continue
        try:
            boards = market_list(fs, day, evidence, f"boards-{kind}")
            valid_boards = [r for r in boards if fresh(r.get("f124"), day) and number(r.get("f3")) is not None]
            if len(valid_boards) < len(boards) * .95:
                raise ValueError("同日板块行情覆盖不足95%")
            valid_boards.sort(key=lambda r: float(r["f3"]), reverse=True)
            board_coverage[kind] = {"asof_date": day, "source": "东方财富板块指数", "expected_count": len(boards), "valid_count": len(valid_boards), "complete": len(valid_boards) == len(boards)}
            rankings.extend({"id": r["f12"], "name": r["f14"], "type": kind, "pct_chg": float(r["f3"]), "asof_date": day, "source": "东方财富板块指数", "rank": i+1} for i, r in enumerate(valid_boards))
            for index, board in enumerate(valid_boards[:count]):
                total = min(count, len(valid_boards))
                base = 24 if kind == "industry" else 31
                progress(base + int(index / total * 7), f"展开{board['f14']}完整成分",
                         {"stage": "sectors", "group": "行业" if kind == "industry" else "题材", "completed": index, "total": total, "unit": "个"})
                members = market_list(f"b:{board['f12']} f:!50", day, evidence, f"members-{board['f12']}")
                member_codes = [str(r["f12"]) + (".SH" if str(r["f12"]).startswith("6") else ".SZ") for r in members if re.fullmatch(r"[036]\d{5}", str(r.get("f12", "")))]
                sectors.append({"id": board["f12"], "name": board["f14"], "type": kind, "classification": "东方财富行业" if kind == "industry" else "东方财富概念（成分可交叉）", "asof_date": day, "members": member_codes, "complete": True})
                if kind == "industry":
                    for code in member_codes:
                        if code in lookup:
                            lookup[code]["industry"] = board["f14"]
                progress(base + int((index + 1) / total * 7), f"已展开{board['f14']}完整成分",
                         {"stage": "sectors", "group": "行业" if kind == "industry" else "题材", "completed": index + 1, "total": total, "unit": "个"})
        except Exception as error:
            notes.append(f"{kind}板块数据不完整：{type(error).__name__}；没有生成该范围的全市场综合排名")
    if not sectors:
        raise ValueError("行业和题材成分均获取失败，无法进行板块共振筛选")
    progress(40, "核验同日涨停池；缺失时保留未知连板高度", {"stage": "limits"})
    limit_complete = False
    try:
        pool = get_limit_pool(day, evidence)
        for code, limit in pool.items():
            if code in lookup and lookup[code]["bars"]:
                lookup[code]["limit"] = limit
                if limit.get("close") and abs(limit["close"] - lookup[code]["bars"][-1]["close"]) > .005001:
                    lookup[code]["verification"] = {"status": "conflict", "source": "东方财富涨停池", "date": day}
        # The public pool excludes some STAR/new listings. Do not call it complete.
        notes.append("免费涨停池可能不含科创板或连续一字新股；市场高度仅展示可验证样本，晋级率暂缺")
    except Exception as error:
        notes.append(f"同日连板/封单数据不可核验（{type(error).__name__}），相应字段留空")
    selected_members = set(c for s in sectors for c in s["members"])
    targets = [s for s in stocks if s["code"] in selected_members and s["bars"] and not s["is_st"] and s["bars"][-1]["amount_yuan"] >= 1e8]
    targets.sort(key=lambda s: s["bars"][-1]["pct_chg"], reverse=True)
    targets = targets[:history_limit]
    notes.append(f"按当日涨幅顺序优先补齐最多{history_limit}只成交额≥1亿元股票的历史；未补齐者不进入候选池")
    failures = []
    for index, stock in enumerate(targets):
        code = stock["code"]
        progress(44 + int(50 * index / len(targets)), f"补齐前复权历史 · {stock['name']}（同日历史优先读取缓存）",
                 {"stage": "history", "completed": index, "total": len(targets), "unit": "只", "succeeded": index - len(failures), "failed": len(failures)})
        try:
            from .source_adapters import history_chain,check_bars
            previous,history_source,_ = history_chain(code,day,root,evidence,
                lambda bars,name:check_bars(code,day,bars,stock['bars'][-1]))
            if not previous or previous[-1]["date"] != day:
                raise ValueError("历史行情未更新到分析日")
            current = stock["bars"][-1]
            if abs(previous[-1]["close"] - current["close"]) > .005001:
                raise ValueError("历史末根与当前收盘价冲突")
            factor = previous[-1].get("adj_factor")
            current["adj_factor"] = factor
            stock["bars"] = previous[:-1] + [current]
            stock['history_fetch']={'status':'complete','date':day,'source':history_source,'sessions':len(previous),'fetched_at':now_iso(),'note':'当前OHLC已核对；历史逐日未完成独立核验'}
            if not stock["list_date"] and previous:
                stock["listed_before"] = previous[0]["date"]
        except Exception as error:
            failures.append({"code": code, "reason": type(error).__name__})
        progress(44 + int(50 * (index + 1) / len(targets)), f"已处理历史 · {stock['name']}",
                 {"stage": "history", "completed": index + 1, "total": len(targets), "unit": "只", "succeeded": index + 1 - len(failures), "failed": len(failures)})
        time.sleep(.1)
    if failures:
        atomic_json(evidence / "history-failures.json", failures)
        notes.append(f"{len(failures)}只个股历史不完整，相应股票不进入强势候选池")
    calendar = sorted({b["date"] for s in stocks for b in s["bars"]})
    progress(97, "检查日期、价格、单位、重复记录与未来数据", {"stage": "validate"})
    dataset = {"schema_version": 1, "meta": {"trade_date": day, "source": "腾讯公开报价＋东方财富公开数据", "mode": "live", "fetched_at": now_iso(), "currency": "CNY", "calendar": calendar, "calendar_source": "供应商实际日线日期并集；不是交易所认证日历", "universe": "沪深A股（不含北交所）；个股历史按所选强势行业/题材展开", "expected_count": len(codes), "complete": len(dated) == len(codes), "sector_universe_complete": False, "provider_sector_coverage": board_coverage, "limit_complete": limit_complete, "previous_limit_complete": False, "notes": notes, "source_urls": SOURCE_URLS, "evidence_directory": str(evidence.relative_to(root)), "history_target_count": len(targets), "history_success_count": len(targets) - len(failures)}, "stocks": stocks, "sectors": sectors, "provider_sector_ranking": rankings}
    return validate_dataset(dataset)


def sina_members(node, evidence):
    base = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center."
    count = int(get_json(base + "getHQNodeStockCount", {"node": node}))
    if not 0 < count <= 15000:
        raise ValueError("新浪成分数量异常")
    rows = []
    for page in range(1, math.ceil(count / 80) + 1):
        payload = get_json(base + "getHQNodeData", {"page": page, "num": 80, "sort": "symbol", "asc": 1, "node": node, "symbol": "", "_s_r_a": "page"})
        if not isinstance(payload, list):
            raise ValueError("新浪成分数据格式变化")
        rows.extend(payload)
        time.sleep(.15)
    if len(rows) != count or len({r["symbol"] for r in rows}) != count:
        raise ValueError("新浪分页成分不完整")
    atomic_json(evidence / f"sina-{node}.json", {"observed_at": now_iso(), "rows": rows})
    return rows


def sina_boards(kind, evidence):
    url = "https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php" if kind == "industry" else "https://money.finance.sina.com.cn/q/view/newFLJK.php?param=class"
    raw = get_text(url, "gb18030")
    payload = json.loads(raw[raw.find("{"):].rstrip(";\r\n "))
    rows = []
    for value in payload.values():
        cells = value.split(",")
        if len(cells) >= 6 and number(cells[5]) is not None:
            rows.append({"id": cells[0], "name": cells[1], "count": int(cells[2]), "prefetch_return": float(cells[5])})
    if not rows:
        raise ValueError("新浪板块列表为空")
    atomic_json(evidence / f"sina-boards-{kind}.json", {"observed_at": now_iso(), "note": "没有日期字段；涨幅仅用于获取优先级，不能作为已核验榜单", "rows": rows})
    return sorted(rows, key=lambda r: r["prefetch_return"], reverse=True)


def eligible_sina_codes(rows):
    """Apply explicit universe exclusions before checking constituent coverage."""
    return [r["symbol"][2:] + (".SH" if r["symbol"].startswith("sh") else ".SZ") for r in rows if re.fullmatch(r"(?:sh6|sz[03])\d{5}", str(r.get("symbol", ""))) and "退" not in str(r.get("name", "")) and "ST" not in str(r.get("name", "")).upper()]


def sina_dated_reference(codes, day, evidence):
    output = {}
    for offset in range(0, len(codes), 200):
        symbols = [("sh" if c.endswith(".SH") else "sz") + c[:6] for c in codes[offset:offset+200]]
        raw = get_text("https://hq.sinajs.cn/list=" + ",".join(symbols), "gb18030")
        (evidence / f"sina-dated-{offset//200:03d}.txt").write_text(raw, encoding="utf-8")
        for symbol, body in re.findall(r'hq_str_((?:sh|sz)\d{6})="([^"]*)";', raw):
            fields = body.split(",")
            if len(fields) > 31 and fields[30] == day and fields[31] >= "15:00:00" and number(fields[3]) and float(fields[3]) > 0:
                code = symbol[2:] + (".SH" if symbol.startswith("sh") else ".SZ")
                output[code] = float(fields[3])
        time.sleep(.1)
    return output


def history_tencent(code, day, cache, evidence=None):
    path = cache / f"tx-{code}-{day}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    symbol = ("sh" if code.endswith(".SH") else "sz") + code[:6]
    series = {}
    for adjust in ("", "qfq"):
        payload = get_json("https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get", {"param": f"{symbol},day,,,200,{adjust}"})
        if evidence is not None:
            atomic_json(evidence / f"tencent-{adjust or 'raw'}.json", payload)
        content = (payload.get("data") or {}).get(symbol, {})
        series[adjust] = content.get(adjust + "day") or content.get("day") or []
    raw_rows, qfq_rows = series[""], series["qfq"]
    adjusted = {r[0]: float(r[2]) for r in qfq_rows if len(r) >= 5 and r[0] <= day}
    output = []
    for index, row in enumerate(raw_rows):
        if len(row) < 9 or row[0] > day or index == 0:
            continue
        dt, opening, close, high, low, raw_volume = row[:6]
        opening, close, high, low, raw_volume = map(float, (opening, close, high, low, raw_volume))
        amount = float(row[8]) * 10000
        if min(opening, close, high, low) <= 0 or raw_volume <= 0:
            continue
        qclose, qprevious = adjusted.get(dt), adjusted.get(raw_rows[index-1][0])
        factor = qclose / close if qclose and qclose > 0 else None
        previous = close / (qclose / qprevious) if qclose and qprevious and qclose > 0 and qprevious > 0 else float(raw_rows[index-1][2])
        output.append({"date": dt, "open": opening, "high": high, "low": low, "close": close, "pre_close": previous, "volume_shares": share_volume(raw_volume, amount, low, high), "amount_yuan": amount, "turnover_rate": number(row[7]), "adj_factor": factor})
    if output and output[-1]["date"] == day:
        atomic_json(path, output)
    return output


def fetch_sina(options, root, progress, fallback_reason):
    day = iso_date(options.get("date", ""))
    now = datetime.now(SHANGHAI)
    if day > now.date().isoformat() or (day == now.date().isoformat() and (now.hour, now.minute) < (16, 30)):
        raise ValueError("当日盘后更新请在上海时间16:30后运行")
    raw_clock = get_text("https://qt.gtimg.cn/q=sh000001", "gb18030")
    match = re.search(r'v_sh000001="([^"]*)"', raw_clock)
    if not match or iso_date(match.group(1).split("~")[30][:8]) != day or int(match.group(1).split("~")[30][8:10]) < 15:
        raise ValueError("目标日期不是免费当前接口可核验的最新收盘日；历史复盘请导入当日快照")
    industry_limit, theme_limit = int(options.get("industry_limit", 12)), int(options.get("theme_limit", 8))
    history_limit = int(options.get("history_limit", 120))
    if not 1 <= industry_limit <= 100 or not 0 <= theme_limit <= 1000 or not 0 <= history_limit <= 500:
        raise ValueError("板块或历史补齐数量超出范围")
    root = Path(root)
    evidence = root / "evidence" / f"sina-{day}-{now.strftime('%H%M%S%f')}"
    evidence.mkdir(parents=True, exist_ok=False)
    (evidence / "market-clock.txt").write_text(raw_clock, encoding="utf-8")
    cache = root / "history"
    cache.mkdir(parents=True, exist_ok=True)
    notes = [f"东方财富主列表不可用，已切换新浪列表＋腾讯日期报价；原错误：{fallback_reason}", "新浪板块列表没有日期字段，仅用其涨幅安排获取优先级；正式强度基于腾讯同日成分报价重算", "行情与板块分类为采集时快照，不回填历史成分；不含北交所", f"免费版按所选板块内当日涨幅顺序，优先补齐最多{history_limit}只成交额≥1亿元股票的历史，其余股票明确标为历史不足", "公告、筹码分布未接入；历史广度仅反映已补齐的当前样本"]
    if str(fallback_reason).startswith('用户选择'):
        notes[0]=fallback_reason
    progress(4, "新浪通道：获取完整证券列表", {"stage": "quotes"})
    raw_stocks = sina_members("hs_a", evidence)
    raw_stocks = [r for r in raw_stocks if re.fullmatch(r"(?:sh6|sz[03])\d{5}", str(r.get("symbol", "")))]
    codes = [r["symbol"][2:] + (".SH" if r["symbol"].startswith("sh") else ".SZ") for r in raw_stocks]
    progress(13, f"核验{len(codes)}只证券的同日腾讯收盘报价", {"stage": "quotes"})
    dated = tencent_quotes(codes, day, evidence)
    if len(dated) < len(codes) * .9:
        raise ValueError(f"有效日期报价不足90%（{len(dated)}/{len(codes)}），不生成市场分析")
    references = {}
    try:
        progress(24, "核对带日期的新浪收盘参考行情", {"stage": "quotes"})
        references = sina_dated_reference(list(dated), day, evidence)
    except Exception as error:
        notes.append(f"新浪带日期参考源不可用（{type(error).__name__}）；未核对者保留单源标记")
    stocks, lookup = [], {}
    for raw, code in zip(raw_stocks, codes):
        quote = dated.get(code)
        verification = {"status": "single_source"}
        if quote and code in references:
            verification = {"status": "matched" if abs(quote["bar"]["close"] - references[code]) <= .005001 else "conflict", "source": "新浪日期报价", "close": references[code], "date": day}
        stock = {"code": code, "name": quote["name"] if quote else raw["name"], "exchange": code[-2:], "board": "科创板" if code.startswith("688") else ("创业板" if code.startswith(("300", "301")) else "主板"), "industry": None, "list_date": None, "is_st": "ST" in str(raw["name"]).upper() or "退" in str(raw["name"]), "bars": [quote["bar"]] if quote else [], "limit": {}, "previous_limit": {}, "verification": verification, "announcements": []}
        stocks.append(stock)
        lookup[code] = stock
    sectors = []
    for kind, count in (("industry", industry_limit), ("theme", theme_limit)):
        if not count:
            continue
        try:
            boards = sina_boards(kind, evidence)
            if kind == "industry":
                boards = [b for b in boards if b["id"] != "new_stock"]
            for index, board in enumerate(boards[:count]):
                total = min(count, len(boards))
                base = 28 if kind == "industry" else 32
                progress(base + int(4 * index / total), f"新浪板块展开：{board['name']}",
                         {"stage": "sectors", "group": "行业" if kind == "industry" else "题材", "completed": index, "total": total, "unit": "个"})
                try:
                    raw_members = sina_members(board["id"], evidence)
                except Exception as error:
                    notes.append(f"{board['name']}成分未通过完整检查（{type(error).__name__}: {str(error)[:100]}），不参与综合排名")
                    progress(base + int(4 * (index + 1) / total), f"已处理板块：{board['name']} · 成分缺失",
                             {"stage": "sectors", "group": "行业" if kind == "industry" else "题材", "completed": index + 1, "total": total, "unit": "个"})
                    continue
                # Sina may retain explicitly delisted names in classification
                # tables. Apply the same named exclusion as the market universe.
                members = eligible_sina_codes(raw_members)
                sectors.append({"id": board["id"], "name": board["name"], "type": kind, "classification": "新浪行业（排除ST/退市名称）" if kind == "industry" else "新浪概念（排除ST/退市名称，成分可交叉）", "asof_date": day, "members": members, "complete": True, "excluded_st_delisted_or_outside": len(raw_members) - len(members)})
                if kind == "industry":
                    for code in members:
                        if code in lookup:
                            lookup[code]["industry"] = board["name"]
                progress(base + int(4 * (index + 1) / total), f"已展开板块：{board['name']}",
                         {"stage": "sectors", "group": "行业" if kind == "industry" else "题材", "completed": index + 1, "total": total, "unit": "个"})
        except Exception as error:
            notes.append(f"新浪{kind}板块未完整展开（{type(error).__name__}）；仅显示已完整获取的研究线")
    if not sectors:
        raise ValueError("备用源板块成分获取失败")
    progress(39, "读取有日期标识的涨停池与连板数据", {"stage": "limits"})
    try:
        pool = get_limit_pool(day, evidence)
        for code, limit in pool.items():
            if code in lookup and lookup[code]["bars"]:
                lookup[code]["limit"] = limit
                if limit.get("close"):
                    matched = abs(limit["close"] - lookup[code]["bars"][-1]["close"]) <= .005001
                    if not matched or lookup[code]["verification"]["status"] == "single_source":
                        lookup[code]["verification"] = {"status": "matched" if matched else "conflict", "source": "东方财富同日涨停池", "close": limit["close"], "date": day}
        notes.append("免费涨停池不保证覆盖科创板/连续一字新股；未知连板高度不估算，昨日晋级率暂缺")
    except Exception as error:
        notes.append(f"涨停池不可核验（{type(error).__name__}），连板与封单字段留空")
    member_set = {c for s in sectors for c in s["members"]}
    targets = [s for s in stocks if s["code"] in member_set and s["bars"] and not s["is_st"] and s["bars"][-1]["amount_yuan"] >= 1e8]
    targets.sort(key=lambda s: s["bars"][-1]["pct_chg"], reverse=True)
    targets = targets[:history_limit]
    failed = []
    for index, stock in enumerate(targets):
        progress(44 + int(50 * index / len(targets)), f"补齐前复权历史 · {stock['name']}",
                 {"stage": "history", "completed": index, "total": len(targets), "unit": "只", "succeeded": index - len(failed), "failed": len(failed)})
        try:
            from .source_adapters import history_chain,check_bars
            bars,history_source,_=history_chain(stock['code'],day,root,evidence,
                lambda bars,name:check_bars(stock['code'],day,bars,stock['bars'][-1]))
            current = stock["bars"][-1]
            if not bars or bars[-1]["date"] != day:
                raise ValueError("历史未更新到分析日")
            if abs(bars[-1]["close"] - current["close"]) > .005001:
                raise ValueError("当前报价与历史日线冲突")
            current["adj_factor"] = bars[-1].get("adj_factor")
            stock["bars"] = bars[:-1] + [current]
            stock['history_fetch']={'status':'complete','date':day,'source':history_source,'sessions':len(bars),'fetched_at':now_iso(),'note':'当前OHLC已核对；历史逐日未完成独立核验'}
            stock["listed_before"] = bars[0]["date"]
        except Exception as error:
            failed.append({"code": stock["code"], "reason": str(error)[:160]})
        progress(44 + int(50 * (index + 1) / len(targets)), f"已处理历史 · {stock['name']}",
                 {"stage": "history", "completed": index + 1, "total": len(targets), "unit": "只", "succeeded": index + 1 - len(failed), "failed": len(failed)})
        time.sleep(.12)
    if failed:
        atomic_json(evidence / "history-failures.json", failed)
        notes.append(f"{len(failed)}只目标股票历史补齐失败，相应股票不入选")
    progress(97, "校验免费盘后快照", {"stage": "validate"})
    dataset = {"schema_version": 1, "meta": {"trade_date": day, "source": "腾讯日期报价＋新浪板块＋东方财富涨停池", "mode": "live", "fetched_at": now_iso(), "currency": "CNY", "calendar": sorted({b["date"] for s in stocks for b in s["bars"]}), "calendar_source": "供应商实际日线日期并集；非交易所认证日历", "universe": "沪深A股当前报价；按已展开板块和历史补齐范围选股", "expected_count": len(codes), "complete": len(dated) == len(codes), "sector_universe_complete": False, "limit_complete": False, "previous_limit_complete": False, "notes": notes, "source_urls": ["https://vip.stock.finance.sina.com.cn/mkt/#hs_a", "https://finance.sina.com.cn/stock/sl/", *SOURCE_URLS], "evidence_directory": str(evidence.relative_to(root)), "history_target_count": len(targets), "history_success_count": len(targets)-len(failed)}, "stocks": stocks, "sectors": sectors}
    return validate_dataset(dataset)


def fetch_free(options, root, progress, *, detailed_progress=None):
    """Keep the CLI's two-argument callback; web clients may request structured events."""
    from .source_config import read_settings
    policy=read_settings(root);primary=policy['market_provider']
    order=['eastmoney','sina'] if primary in ('auto','eastmoney') else ['sina','eastmoney']
    if not policy['market_fallback']:order=order[:1]
    failures=[]
    for attempt, name in enumerate(order, 1):
        def report(percent, message, detail=None):
            if detailed_progress is not None:
                detailed_progress(percent, message, {"source": name, "attempt": attempt,
                                  "attempt_total": len(order), **(detail or {})})
            else:
                progress(percent, message)
        try:
            report(0, "正在连接" + ("东方财富" if name == "eastmoney" else "新浪＋腾讯") + "行情通道", {"stage": "connecting"})
            data=fetch_eastmoney(options,root,report) if name=='eastmoney' else fetch_sina(options,root,report,
                failures[-1]['error'] if failures else '用户选择新浪证券列表＋腾讯日期报价通道')
            data['meta']['provider_policy']=policy
            data['meta']['market_route']=name
            data['meta']['provider_attempts']=failures+[{'source':name,'status':'success'}]
            data['meta']['history_sources_used']=sorted({s['history_fetch']['source'] for s in data['stocks'] if s.get('history_fetch')})
            if data['meta']['history_sources_used']:
                data['meta']['notes'].append('个股历史来源分别留痕：'+'、'.join(data['meta']['history_sources_used'])+'；同一只股票的复权序列不跨源拼接')
            return data
        except Exception as error:
            failures.append({'source':name,'status':'error','error':f'{type(error).__name__}: {str(error)[:160]}'})
            if attempt < len(order):
                report(0, '当前通道未完成，正在切换备用来源；本轮进度将重新计算', {"stage": "fallback"})
    raise ValueError('；'.join(r['source']+'：'+r['error'] for r in failures))
