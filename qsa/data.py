from __future__ import annotations

import copy
import json
import math
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

SHANGHAI = timezone(timedelta(hours=8))


def now_iso():
    return datetime.now(SHANGHAI).isoformat(timespec="seconds")


def iso_date(value):
    value = str(value).replace("-", "")
    return datetime.strptime(value, "%Y%m%d").date().isoformat()


def validate_dataset(dataset):
    """Fail closed: reject invalid snapshots rather than silently repairing quotes."""
    if not isinstance(dataset, dict) or dataset.get("schema_version") != 1:
        raise ValueError("需要 schema_version=1 的行情快照")
    meta = dataset.get("meta", {})
    day = iso_date(meta.get("trade_date", ""))
    if day > datetime.now(SHANGHAI).date().isoformat():
        raise ValueError("行情日期不能晚于当前上海日期")
    if not meta.get("source"):
        raise ValueError("必须注明数据来源 source")
    if meta.get("currency", "CNY") != "CNY":
        raise ValueError("当前仅接受人民币元口径")
    calendar = meta.get("calendar", [])
    if not calendar or day not in calendar or calendar != sorted(set(calendar)):
        raise ValueError("需要有序且不重复的交易日历，且包含分析日")
    for item in calendar:
        if iso_date(item) != item:
            raise ValueError("交易日历日期需为 YYYY-MM-DD")
    stocks = dataset.get("stocks", [])
    if not stocks or len(stocks) > 15000:
        raise ValueError("股票数量须在 1～15000 之间")
    codes = set()
    for stock in stocks:
        code = stock.get("code")
        if not code or not stock.get("name") or code in codes:
            raise ValueError("证券代码、名称不能为空，代码不能重复")
        codes.add(code)
        bars = stock.get("bars", [])
        if len(bars) > 1000:
            raise ValueError(f"{code} 单次最多导入 1000 根日线")
        dates = set()
        for bar in bars:
            bday = bar.get("date", "")
            if iso_date(bday) != bday or bday > day or bday in dates or bday not in calendar:
                raise ValueError(f"{code} 存在未来、重复或日历外的日线")
            dates.add(bday)
            for key in ("open", "high", "low", "close", "pre_close", "volume_shares", "amount_yuan"):
                value = bar.get(key)
                if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
                    raise ValueError(f"{code} {bday} 的 {key} 无效，不能用缺失值代替 0")
            if min(bar[k] for k in ("open", "high", "low", "close", "pre_close")) <= 0:
                raise ValueError(f"{code} {bday} 存在非正价格")
            if bar["low"] > min(bar["open"], bar["close"]) + .005 or bar["high"] < max(bar["open"], bar["close"]) - .005 or bar["low"] > bar["high"]:
                raise ValueError(f"{code} {bday} OHLC 范围不一致")
            change = (bar["close"] / bar["pre_close"] - 1) * 100
            if bar.get("pct_chg") is not None and (not math.isfinite(bar["pct_chg"]) or abs(change - bar["pct_chg"]) > .12):
                raise ValueError(f"{code} {bday} 涨跌幅与参考前收盘价不一致")
            for key in ("adj_factor", "up_limit", "down_limit", "turnover_rate"):
                value = bar.get(key)
                if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or (key in ("adj_factor", "up_limit", "down_limit") and value == 0)):
                    raise ValueError(f"{code} {bday} 的 {key} 无效")
            if bar.get("up_limit") is not None and bar["high"] > bar["up_limit"] + .0051:
                raise ValueError(f"{code} {bday} 最高价超出所提供的涨停价")
            if bar.get("down_limit") is not None and bar["low"] < bar["down_limit"] - .0051:
                raise ValueError(f"{code} {bday} 最低价低于所提供的跌停价")
        if [b["date"] for b in bars] != sorted(dates):
            raise ValueError(f"{code} 日线必须按日期升序")
        for field in ("limit", "previous_limit"):
            value = stock.get(field)
            if value:
                if value.get("status") not in ("U", "D", "Z", "N", None):
                    raise ValueError(f"{code} 的涨停类型无效")
                if value.get("consecutive") is not None and (not isinstance(value["consecutive"], int) or not 0 <= value["consecutive"] <= 100):
                    raise ValueError(f"{code} 的连板数无效")
                if value.get("status") == "U" and value.get("consecutive") == 0:
                    raise ValueError(f"{code} 收盘涨停的已知连板数不能为0")
    sector_ids = set()
    for sector in dataset.get("sectors", []):
        if not sector.get("id") or sector["id"] in sector_ids or sector.get("type") not in ("industry", "theme"):
            raise ValueError("板块需提供唯一 id 及 industry/theme 类型")
        sector_ids.add(sector["id"])
        if sector.get("asof_date") != day:
            raise ValueError("板块成员快照日期必须与行情日期一致，禁止当前成分冒充历史成分")
        members = sector.get("members", [])
        if len(members) != len(set(members)):
            raise ValueError("板块成分不能重复")
        if not sector.get("classification"):
            raise ValueError("必须注明板块分类来源")
    ranking = dataset.get("provider_sector_ranking", [])
    if not isinstance(ranking, list) or len(ranking) > 10000:
        raise ValueError("供应商板块榜单格式或数量无效")
    ranking_ids = set()
    for row in ranking:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"] or not row.get("name") or row.get("type") not in ("industry", "theme"):
            raise ValueError("供应商板块榜单需包含代码、名称及分类")
        key = (row["type"], row["id"])
        value = row.get("pct_chg")
        if key in ranking_ids or type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("供应商板块榜单不能重复，涨幅需为有效数值")
        ranking_ids.add(key)
        if row.get("asof_date") is not None and (iso_date(row["asof_date"]) != row["asof_date"] or row["asof_date"] > day):
            raise ValueError("供应商板块行情日期无效或超过分析日")
    board_coverage = meta.get("provider_sector_coverage", {})
    if not isinstance(board_coverage, dict):
        raise ValueError("供应商板块范围信息需为对象")
    for kind, item in board_coverage.items():
        if kind not in ("industry", "theme") or not isinstance(item, dict):
            raise ValueError("供应商板块范围分类无效")
        if type(item.get("complete")) is not bool or type(item.get("expected_count")) is not int or not 1 <= item["expected_count"] <= 10000:
            raise ValueError("供应商板块范围需包含完整性和预期数量")
        if iso_date(item.get("asof_date", "")) != item["asof_date"] or item["asof_date"] > day or not item.get("source"):
            raise ValueError("供应商板块范围需注明有效日期与来源")
    return dataset


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    temp.replace(path)


class SnapshotStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.demo = make_demo()
        self.active = self.demo
        self.active_mtime = None
        marker = self.root / "active.json"
        if marker.exists():
            try:
                self.active = validate_dataset(json.loads(marker.read_text(encoding="utf-8")))
                self.active_mtime = marker.stat().st_mtime_ns
            except (ValueError, OSError, TypeError):
                pass

    def save(self, dataset):
        validate_dataset(dataset)
        stamp = datetime.now(SHANGHAI).strftime("%H%M%S%f")
        path = self.root / "snapshots" / f"{dataset['meta']['trade_date']}-{stamp}.json"
        atomic_json(path, dataset)
        atomic_json(self.root / "active.json", dataset)
        self.active = dataset
        self.active_mtime = (self.root / "active.json").stat().st_mtime_ns
        return path.name

    def read(self, mode="active"):
        if mode == "demo":
            return self.demo
        marker = self.root / "active.json"
        if marker.exists() and marker.stat().st_mtime_ns != self.active_mtime:
            dataset = validate_dataset(json.loads(marker.read_text(encoding="utf-8")))
            self.active = dataset
            self.active_mtime = marker.stat().st_mtime_ns
        return self.active


def attach_reference(dataset, payload):
    result = copy.deepcopy(dataset)
    day = result["meta"]["trade_date"]
    if payload.get("trade_date") != day:
        raise ValueError("参考行情日期必须与当前快照一致")
    source = str(payload.get("source", "")).strip()
    if not source or source == result["meta"]["source"]:
        raise ValueError("请填写区别于主源的参考来源名称")
    rows = payload.get("rows", [])
    lookup = {}
    for row in rows:
        code = str(row.get("code", ""))
        close = float(row.get("close", 0))
        if not code or code in lookup or not math.isfinite(close) or close <= 0:
            raise ValueError("参考行情存在重复代码或无效收盘价")
        if row.get("date", day) != day:
            raise ValueError("参考行情包含其他交易日")
        lookup[code] = close
    if not lookup:
        raise ValueError("参考行情为空")
    count = 0
    for stock in result["stocks"]:
        bars = stock["bars"]
        if stock["code"] in lookup and bars and bars[-1]["date"] == day:
            close = lookup[stock["code"]]
            stock["verification"] = {"status": "matched" if abs(close - bars[-1]["close"]) <= .005001 else "conflict", "source": source, "close": close, "date": day}
            count += 1
    if not count:
        raise ValueError("参考文件中没有可匹配的证券代码")
    result["meta"]["reference_note"] = f"用户提供的 {source} 收盘参考；仅核对收盘价，未认证来源独立性"
    return result, count


def make_demo():
    """Synthetic quotes with fictional codes: never disguised as real securities."""
    rng = random.Random(40927)
    end = date(2026, 10, 22)
    days = []
    cursor = end
    while len(days) < 150:
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    days.reverse()
    names = ["算力设备", "机器人", "储能设备", "创新医药", "消费零售", "基础材料"]
    stocks, sectors = [], []
    for group, name in enumerate(names):
        members = []
        for j in range(15):
            code = f"DEMO{group * 15 + j + 1:03d}"
            members.append(code)
            close = rng.uniform(8, 45)
            bars = []
            chain = max(0, 5 - j) if group == 0 and j < 5 else (2 if group == 1 and j == 0 else 0)
            for k, day in enumerate(days):
                previous = close
                trend = (.0045 if group < 2 else .0006) if k > 110 else .0006
                move = rng.gauss(trend, .018)
                if k == len(days) - 1:
                    move += [.025, .019, .003, -.004, -.012, -.008][group]
                    if j % 5 == 2 and group < 2:
                        move = .065
                upper, lower = round(previous * 1.1 + 1e-8, 2), round(previous * .9 + 1e-8, 2)
                is_limit = bool(chain and k >= len(days) - chain)
                open_price = round(previous * (1 + rng.uniform(-.012, .018)), 2)
                close = upper if is_limit else round(min(upper - .01, max(lower, previous * (1 + move))), 2)
                high = min(upper, round(max(open_price, close) * (1 + rng.uniform(.002, .016)), 2))
                low = max(lower, round(min(open_price, close) * (1 - rng.uniform(.002, .012)), 2))
                if j == 0 and group == 0 and is_limit:
                    open_price = high = low = close
                volume = int(rng.uniform(12e6, 35e6) * (1.8 if k == len(days) - 1 and group < 2 else 1))
                bars.append({"date": day, "open": open_price, "high": high, "low": low, "close": close, "pre_close": previous, "pct_chg": round((close / previous - 1) * 100, 4), "volume_shares": volume, "amount_yuan": round(volume * (open_price + close) / 2, 2), "adj_factor": 1.0, "up_limit": upper, "down_limit": lower, "turnover_rate": round(rng.uniform(3.5, 19), 2)})
            status = "U" if chain else ("Z" if j == 6 and group < 2 else "N")
            if status == "Z":
                bars[-1]["high"] = bars[-1]["up_limit"]
            stocks.append({"code": code, "name": f"演示{name[:2]}{j+1:02d}", "exchange": "DEMO", "board": "演示主板", "industry": name, "list_date": "2020-01-01", "is_st": False, "bars": bars, "limit": {"status": status, "consecutive": chain, "first_time": "09:42:08" if chain else None, "last_time": "14:21:30" if chain else None, "open_times": (j % 3) if chain else None, "fd_amount": 120e6 if chain else None}, "previous_limit": {"status": "U" if chain > 1 else "N", "consecutive": max(0, chain - 1)}, "verification": {"status": "demo"}, "announcements": []})
        sectors.append({"id": f"I{group}", "name": name, "type": "industry", "classification": "演示行业分类 · 非真实市场分类", "asof_date": days[-1], "members": members, "complete": True})
    for i, name in enumerate(["智能制造", "数字基础设施", "绿色转型"]):
        members = [s["code"] for n, s in enumerate(stocks) if n % 3 == i and n < 60]
        sectors.append({"id": f"T{i}", "name": name, "type": "theme", "classification": "演示题材分类 · 成分存在交叉", "asof_date": days[-1], "members": members, "complete": True})
    return validate_dataset({"schema_version": 1, "meta": {"trade_date": days[-1], "source": "内置合成数据", "mode": "demo", "fetched_at": now_iso(), "currency": "CNY", "calendar": days, "calendar_source": "演示工作日日历，未模拟交易所节假日", "universe": "90 只虚构证券，仅用于体验功能", "expected_count": 90, "complete": True, "limit_complete": True, "previous_limit_complete": True, "notes": ["全部名称、代码、价格与板块均为合成数据，不能作为交易依据", "公告与筹码数据未接入"]}, "stocks": stocks, "sectors": sectors})
