"""Evidence-bound, local Douyin closing-review drafts and immutable exports."""
from __future__ import annotations

import base64
import copy
import html
import io
import json
import re
import secrets
import threading
import unicodedata
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

from .analytics import analyze, stock_row
from .data import SHANGHAI, atomic_json, iso_date, now_iso
from .review_titles import title_plan

DISCLAIMER = "以上内容仅为个人对市场行情的复盘与观点分享，不构成任何投资建议。"
TASK_TITLE = "A股抖音复盘收盘"
TASK_THREAD = "6adb45da-0394-83e8-a4df-159de764e0ee"
PROJECT = Path(__file__).resolve().parents[1]
SSE_CALENDAR = "https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml"
SZSE_CALENDAR = "https://www.szse.cn/disclosure/notice/t20251222_618087.html"
# Annual SSE notice read on 2026-10-23. SZSE URL was found, its body timed out.
# Never extend a known year's calendar into the next year by guessing weekdays.
HOLIDAYS = {2026: [("01-01", "01-03"), ("02-15", "02-23"), ("04-04", "04-06"),
                   ("05-01", "05-05"), ("06-19", "06-21"), ("09-25", "09-27"), ("10-01", "10-07")]}
DEFAULT_SETTINGS = {"enabled": False, "time": "19:30", "timezone": "Asia/Shanghai",
                    "refresh_quotes": True, "extra_urls": [], "closed_dates": []}


def trading_day(day, closed_dates=()):
    current = date.fromisoformat(iso_date(day))
    if day in closed_dates:
        return False
    if current.weekday() >= 5:
        return False
    if current.year not in HOLIDAYS:
        return None
    for start, end in HOLIDAYS[current.year]:
        if f"{current.year}-{start}" <= day <= f"{current.year}-{end}":
            return False
    return True


def prior_session(day):
    current = date.fromisoformat(day)
    for _ in range(15):
        current -= timedelta(days=1)
        if trading_day(current.isoformat()):
            return current.isoformat()
    return None


def public_stock(row):
    code = row["code"]
    return (not row["is_st"] and row.get("current") and row["verification"].get("status") == "matched"
            and row["verification"].get("date") == row["current"]["date"]
            and not any("榜单与日线价格冲突" in e for e in row.get("exclusions", []))
            and ((code.endswith(".SH") and code.startswith(("600", "601", "603", "605")))
                 or (code.endswith(".SZ") and code.startswith(("000", "001", "002", "003", "300", "301")))))


def reconcile_news(sources):
    facts, problems = {}, []
    for field in ("turnover_yuan", "limit_up", "limit_down", "broken", "height"):
        records = [s for s in sources if field in s.get("metrics", {})]
        # Statistics of different pools are not conflicting measurements of one fact.
        groups = {}
        for source in records:
            scope = "沪深成交额" if field == "turnover_yuan" else source["metrics"].get("limit_scope")
            if scope == "原文未完整说明":
                continue
            groups.setdefault(scope, []).append(source)
        agreed = []
        for scope, members in groups.items():
            values = {m["metrics"][field] for m in members}
            if len(values) != 1:
                problems.append(f"{field}在同口径来源中冲突，未引用")
                continue
            origins = {m["publisher"] for m in members}
            # Different syndication domains with the same publisher still count once.
            if len(origins) >= 2:
                agreed.append({"value": values.pop(), "scope": scope, "source_ids": [s["id"] for s in members]})
                for source in members:
                    source.setdefault("verified_fields", []).append(field)
        if len(agreed) == 1:
            facts[field] = agreed[0]
    # Compute the sealing rate only if both operands have exactly the same source set/pool.
    up, broken = facts.get("limit_up"), facts.get("broken")
    if up and broken and up["scope"] == broken["scope"] and set(up["source_ids"]) == set(broken["source_ids"]):
        total = up["value"] + broken["value"]
        if total:
            facts["seal_rate"] = {"value": round(100 * up["value"] / total, 1), "scope": up["scope"], "source_ids": up["source_ids"]}
    return facts, problems


def select_sectors(dataset):
    # Compare themes with themes, industries with industries. Never mix two rankings.
    analyses = {style: analyze(dataset, {"style": style, "sector_type": "theme"}) for style in ("balanced", "relay", "trend")}
    ranked = [s for s in analyses["balanced"]["sectors"] if s["score"] is not None and s["score"] >= 40 and s["avg_pct"] > 0]
    kind = "theme"
    if not ranked:
        kind = "industry"
        analyses = {style: analyze(dataset, {"style": style, "sector_type": kind}) for style in ("balanced", "relay", "trend")}
        ranked = [s for s in analyses["balanced"]["sectors"] if s["score"] is not None and s["score"] >= 40 and s["avg_pct"] > 0]
    rows = analyses["balanced"]["stocks"]
    used, selections = set(), []
    for sector in ranked:
        reps = []
        for style in ("relay", "trend", "balanced"):
            choices = [r for r in analyses[style]["stocks"] if r["selected"] and public_stock(r)
                       and r["code"] in sector["members"] and r["code"] not in used]
            if choices:
                row = choices[0]
                reps.append({"code": row["code"], "name": row["name"], "style": style, "role": row["role"],
                    "pct": row["pct_chg"], "consecutive": row["consecutive"], "limit_status": row["limit_status"],
                    "close": row["current"]["close"], "low": row["current"]["low"], "high": row["current"]["high"],
                    "score": row["score"], "technical": row["technical"], "risks": row["risk_flags"]})
                used.add(row["code"])
        if reps:
            selections.append({**sector, "representatives": reps})
        if len(selections) == 3:
            break
    return selections, kind, rows, analyses["balanced"]["market"]


def yesterday_feedback(dataset, previous, day):
    if not previous or previous["meta"]["trade_date"] != prior_session(day):
        return [], "昨日强势股的同口径快照尚未齐备，暂不计算晋级率或平均溢价。"
    today = {s["code"]: stock_row(s, day) for s in dataset["stocks"]}
    cohort = []
    for stock in previous["stocks"]:
        prior = stock_row(stock, previous["meta"]["trade_date"])
        row = today.get(prior["code"])
        if prior["limit_status"] == "U" and row and public_stock(prior) and public_stock(row):
            cohort.append({"name": row["name"], "pct": row["pct_chg"], "limit_status": row["limit_status"],
                           "previous_height": prior["consecutive"], "height": row["consecutive"]})
    cohort.sort(key=lambda s: s["previous_height"] or 0, reverse=True)
    sample = cohort[:3]
    if not sample:
        return [], "昨日涨停股的今日反馈没有形成可核验的对应样本，接力判断暂缺这一环。"
    descriptions = [f"{s['name']}今日{'继续收盘涨停' if s['limit_status']=='U' else '收盘'+format(s['pct'],'+.2f')+'%'}" for s in sample]
    return sample, "；".join(descriptions) + "。这是已核验个例，不能外推为全部昨日强势股的表现。"


def cover_prompt(title, day, design=None):
    return (f"生成真正16:9横版A股收盘复盘封面，1920×1080。深蓝黑高级多屏交易室，窗外蓝橙暮色城市，克制金色灯光，摄影质感。"
        f"左上日期『{day}』和『A股收盘复盘』；左侧主标题『{title}』最多两行，白色主字、关键题材或数字用少量金色强调，手机缩略图可读；"
        + (f"标题只表达已核验的『{design['angle']}』，范围『{design['scope']}』。" if design else '')+
        "右侧屏幕只呈现模糊抽象行情。底部准确写『示意画面，非实时行情』。不要股票代码、收益承诺、交易指令、二维码或数据卡片。"
        "原任务E盘参考图不可访问，按上述文字风格制作。")


def render_cover(title, day, background=None, design=None):
    background = background or PROJECT / "static" / "assets" / "review-room-v1.png"
    if not background.exists():
        return None
    photo = base64.b64encode(background.read_bytes()).decode("ascii")
    # Fixed typography is code-native artwork layered over the generated photo.
    split = title.find("，")
    if design and design.get('selected_title') == title:
        lines = design['selected_lines']
    elif split>=0:
        lines = [title[:split], title[split+1:]]
    else:
        lines = [title[:11], title[11:]] if len(title) > 11 else [title]
    # Estimate CJK/Latin widths so numbers and long names fit the same mobile-safe block.
    units=max(sum(1 if unicodedata.east_asian_width(c) in ('W','F') else .58 for c in line) for line in lines)
    font=min(112,int(900/max(1,units)))
    text = ''.join(f'<text x="116" y="{430+i*142}" font-size="{font}" font-weight="800" fill="{("#f0ca85" if i==0 else "#faf7ef")}">{html.escape(line)}</text>' for i,line in enumerate(lines) if line)
    supporting = (f'<text x="116" y="735" font-size="27" fill="#c0cbd0">{html.escape(design["support"])}</text>'
        f'<text x="116" y="860" font-size="23" fill="#a3b4c0">{html.escape(design["scope"])}</text>') if design else ''
    return (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="1920" height="1080" viewBox="0 0 1920 1080">'
        '<defs><linearGradient id="shade"><stop stop-color="#071723" stop-opacity=".96"/><stop offset=".46" stop-color="#071723" stop-opacity=".87"/><stop offset=".75" stop-color="#071723" stop-opacity=".18"/><stop offset="1" stop-color="#071723" stop-opacity=".02"/></linearGradient></defs>'
        f'<image width="1920" height="1080" preserveAspectRatio="xMidYMid slice" xlink:href="data:image/png;base64,{photo}"/>'
        '<rect width="1920" height="1080" fill="url(#shade)"/>'
        '<g font-family="Microsoft YaHei, PingFang SC, Noto Sans CJK SC, sans-serif" fill="#faf7ef">'
        f'<text x="116" y="150" font-size="32" fill="#d9be86">{day}　/　A股收盘复盘</text>'
        f'<rect x="116" y="275" width="92" height="5" rx="2" fill="#d9be86"/>{text}{supporting}'
        '<text x="116" y="940" font-size="27" fill="#d4dce3">示意画面，非实时行情</text></g></svg>')


def build_review(dataset, context, previous=None, recent=(), closed_dates=()):
    day = iso_date(context["trade_date"])
    cutoff = datetime.fromisoformat(context["cutoff_at"]).astimezone(SHANGHAI)
    report = {"version": "1.1", "trade_date": day, "created_at": now_iso(), "cutoff_at": cutoff.isoformat(),
              "task_title": TASK_TITLE, "status": "verification_failed", "title": "", "body": "", "sources": [],
              "missing": [], "checks": [], "sectors": [], "signals": [], "forecast": "", "cover": {"status": "not_generated"}}
    known_day = trading_day(day, closed_dates)
    if known_day is False:
        return {**report, "status": "skipped", "failure": "非交易日：静默跳过"}
    if known_day is None:
        return {**report, "failure": "目标年份交易日历未配置，无法确认交易日；未生成行情文稿或封面。"}
    if day > cutoff.date().isoformat() or (day == cutoff.date().isoformat() and cutoff.hour < 15):
        return {**report, "failure": "目标交易日尚未收盘；未生成行情文稿或封面。"}
    if dataset["meta"].get("mode") == "demo" or dataset["meta"]["trade_date"] != day:
        return {**report, "failure": "没有目标交易日的真实有效行情快照；请先更新数据。"}
    # Persist only sources actually read. Discard other-date or after-cutoff evidence.
    sources = []
    for source in context.get("sources", []):
        try:
            published = datetime.fromisoformat(source["published_at"])
            if published.tzinfo is None:
                published = published.replace(tzinfo=SHANGHAI)
            if source["market_date"] == day and published.date().isoformat() == day and published.hour >= 15 and published <= cutoff:
                sources.append({**source, "verified_fields": list(source.get("verified_fields", []))})
        except (KeyError, ValueError, TypeError):
            continue
    report["sources"] = [{k: v for k, v in s.items() if k != "text"} for s in sources]
    facts, problems = reconcile_news(sources)
    indices = {}
    for name, value in context.get("indices", {}).items():
        try:
            stamp = datetime.fromisoformat(value["published_at"])
            publishers = {s["publisher"] for s in sources if s["kind"] == "index" and name in s.get("verified_fields", [])}
            if (name in ("上证指数", "深证成指", "创业板指") and value.get("status") == "两平台收盘一致"
                    and len(publishers) >= 2 and stamp.date().isoformat() == day and stamp.hour >= 15 and stamp <= cutoff):
                indices[name] = value
        except (KeyError, TypeError, ValueError):
            continue
    if not indices and not any(key in facts for key in ("turnover_yuan", "limit_up")):
        return {**report, "failure": "指数、沪深成交额及涨停统计均未完成有效交叉核验；仅保存核验失败记录，不输出强行复盘。", "missing": problems + context.get("errors", [])}
    sectors, kind, rows, market = select_sectors(dataset)
    eligible = [r for r in rows if public_stock(r)]
    up = sum(r["pct_chg"] > 0 for r in eligible)
    down = sum(r["pct_chg"] < 0 for r in eligible)
    breadth = up / len(eligible) if eligible else None
    previous_rows, feedback = yesterday_feedback(dataset, previous, day)
    # These are bounded sample judgments, not predictions or trading instructions.
    if breadth is not None and breadth >= .65 and len(sectors) >= 2:
        base = "上涨后的局部轮动"
        reason = f"收盘样本上涨家数明显多于下跌家数，且{sectors[0]['name']}等方向出现共振"
        trigger = "高辨识度个股保持承接，强势方向由少数代表扩展到更多成分"
        invalid = "高位个股集中回落，且今日强势方向的上涨广度同步收缩"
    elif breadth is not None and breadth < .4:
        base = "分歧延续下的局部修复"
        reason = "收盘样本下跌家数占优，普遍扩散的证据不足"
        trigger = "昨日强势股先修复，且板块内部下跌范围缩小"
        invalid = "弱势继续从后排扩散到高辨识度个股"
    else:
        base = "局部轮动"
        reason = "目前可验证的板块扩散和连续性证据仍然有限"
        trigger = "强势方向保住当前上涨广度，并出现新的接力成分"
        invalid = "代表股转弱且板块内部同步退潮"
    forecast = f"下一个交易日的基准判断偏向{base}。依据是{reason}，但单日强势尚不足以证明主升趋势已经确立。如果{trigger}，延续性判断会增强；如果{invalid}，这一判断即失效。"
    design = title_plan(indices, sectors, facts, recent)
    title, dedup_note = design['selected_title'], design['dedup_note']
    parts = []
    if sectors:
        names = "、".join(s["name"] for s in sectors)
        parts.append(f"今天市场表现较强的方向主要集中在{names}。结论来自已展开板块和收盘样本，延续性仍需下一交易日检验。")
    else:
        parts.append("今天的收盘数据已经整理，但当前没有证据充分的强势方向，复盘保留空名单。")
    parts.append("一、指数和市场情绪")
    index_text = "；".join(f"{name}收于{value['close']:.2f}点，{value['pct']:+.2f}%" for name, value in indices.items()) + "。"
    if not indices:
        index_text = "指数交叉核验未完成，本次省略具体点位。"
    if "turnover_yuan" in facts:
        index_text += f"沪深两市成交额{facts['turnover_yuan']['value']/1e8:.2f}亿元。"
    if eligible:
        index_text += f"本次已核验的{len(eligible)}只沪深主板及创业板样本中，上涨{up}只、下跌{down}只。"
    if "seal_rate" in facts:
        index_text += f"收盘涨停{facts['limit_up']['value']}只、封板未遂{facts['broken']['value']}只，封板率{facts['seal_rate']['value']:.1f}%。"
    parts.append(index_text)
    parts.extend(["二、短线接力与昨日强势股反馈", feedback])
    known_high = sorted([r for r in eligible if r["limit_status"] == "U" and (r["consecutive"] or 0) >= 2], key=lambda r:r["consecutive"], reverse=True)
    if known_high:
        high = known_high[0]
        parts.append(f"已核验个例中，{high['name']}为{high['consecutive']}连板，属于高辨识度接力观察对象。当前高度统计不完整，不能据此认定它是全市场最高板或唯一龙头。")
    parts.append("三、今天市场表现较强的方向")
    for sector in sectors:
        parts.append(sector["name"])
        text = f"板块内部有扩散：本次成分等权涨幅{sector['avg_pct']:+.2f}%，上涨占比{sector['breadth']:.1f}%。"
        descriptions = []
        for rep in sector["representatives"]:
            if rep["limit_status"] == "U":
                performance = f"收盘{rep['consecutive']}连板" if (rep["consecutive"] or 0) >= 2 else "收盘首板"
            else:
                performance = f"收盘{rep['pct']:+.2f}%"
            role = "接力观察" if (rep["consecutive"] or 0) >= 2 else ("首板观察" if rep["limit_status"] == "U" else "趋势观察")
            descriptions.append(f"{rep['name']}{performance}，作为{role}样本")
        text += "；".join(descriptions) + "。"
        text += "下一交易日看代表股承接及成分扩散，若二者同步转弱，延续性判断失效。公告仍待复核。"
        parts.append(text)
    if not sectors:
        parts.append("板块覆盖或代表股证据不足，暂不列出具体方向。")
    parts.extend(["四、明日走势判断", forecast, "五、明天重点观察的信号"])
    signals = []
    if known_high:
        high = known_high[0]
        signals.append(f"{high['name']}的高位反馈：今日收盘{high['current']['close']:.2f}元、最低{high['current']['low']:.2f}元，关注承接是否明显减弱，价格只作当日观察参照。")
    if sectors:
        first = sectors[0]
        signals.append(f"{first['name']}的扩散：对照今日上涨占比{first['breadth']:.1f}%，检查下一交易日是否继续由少数代表向更多成分扩展。")
    if eligible:
        signals.append(f"亏钱效应是否扩散：对照本次下跌{down}只的固定范围样本，关注弱势是否蔓延到今日强势股，不把样本变化当成全市场变化。")
    parts.append("\n".join(f"{i+1}. {signal}" for i, signal in enumerate(signals[:3])))
    parts.extend(["你更关注强势方向延续，还是轮动扩散？", "#A股复盘 #收盘复盘 #市场情绪 #短线观察", f"复盘日期：{day}", DISCLAIMER])
    body = "\n\n".join(parts)
    # Block a changed template/imported stock name from leaking codes or action commands.
    forbidden = re.findall(r"(?<!\d)\d{6}(?:\.(?:SH|SZ|BJ))?(?!\d)|买入|卖出|加仓|减仓|止损|目标价|稳赚|必涨", title + body)
    if forbidden:
        return {**report, "failure": "文稿检查发现代码或交易指令，已停止导出。"}
    missing = problems + ["深交所年度通知正文未成功读取；临时休市公告未自动核验，以同日沪深收盘报价补充确认开市",
        "独立公告检索、筹码分布和除权检查尚未接入，代表股公告仍待人工复核"]
    if len(indices) < 3:
        missing.append("部分指数未完成两平台收盘核对")
    if "turnover_yuan" not in facts:
        missing.append("沪深两市成交额缺少两个独立原始来源的一致记录，正文省略数值")
    if "seal_rate" not in facts:
        missing.append("全市场涨停、炸板与封板率未完成同口径交叉核验，正文省略")
    if not previous_rows:
        missing.append("缺少上一实际交易日的完整强势股反馈样本")
    missing.append("最近3—5个实际交易日的完整同口径市场比较仍需补齐；当前历史小样本不外推")
    if kind == "industry":
        missing.append("题材证据不足，使用同分类行业方向；未将行业名冒充题材催化")
    report.update(status="draft", title=title, title_design=design, body=body, forecast=forecast, signals=signals[:3], sectors=sectors,
        indices=indices, facts=facts, missing=missing + context.get("errors", []), feedback=previous_rows,
        snapshot={k: dataset["meta"].get(k) for k in ("trade_date", "fetched_at", "evidence_directory", "mode", "source")},
        checks=["目标日期已收盘", "仅使用真实同日快照", "仅列沪深主板和创业板", "三种风格均参与代表选择", "正文没有股票代码或交易指令", "明日判断为单段", "免责声明准确", dedup_note],
        char_count=len(re.sub(r"\s", "", body)), source_policy="转载按原始发布者合并；两平台价格一致不等同于独立行情认证",
        calendar={"year": 2026, "sse_url": SSE_CALENDAR, "szse_url": SZSE_CALENDAR, "sse_published": "2025-12-22", "szse_status": "正文读取超时", "temporary_notices": "待人工复核"})
    # Reconciliation updates the field ledger, expose the final version.
    report["sources"] = [{k:v for k,v in s.items() if k != "text"} for s in sources]
    report["sources"].append({"id": "local-snapshot", "kind": "snapshot", "title": "本项目同日收盘快照与原始响应",
        "url": "", "publisher": dataset["meta"].get("source"), "market_date": day,
        "published_at": dataset["meta"].get("fetched_at"), "retrieved_at": report["created_at"],
        "scope": "已核验收盘股票及已展开板块；非完整市场，当前成分归类并非历史成分",
        "verified_fields": ["正文代表股收盘价", "样本涨跌家数", "板块成分强度"], "status": "快照可追溯",
        "evidence_directory": dataset["meta"].get("evidence_directory")})
    report["cover"] = {"status": "template_ready" if render_cover(title, day, design=design) else "not_generated",
        "width": 1920, "height": 1080, "prompt": cover_prompt(title, day, design),
        "method": "ImageGen摄影底图 + 软件确定性日期/标题排版；非每日调用图像模型",
        "reference": "原任务E盘参考图不可访问，未使用原图"}
    return report


class ReviewStore:
    def __init__(self, root):
        self.root = Path(root) / "reviews"
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def settings(self):
        path = self.root / "settings.json"
        return {**DEFAULT_SETTINGS, **(json.loads(path.read_text(encoding="utf-8")) if path.exists() else {})}

    def update_settings(self, value):
        from .review_sources import checked_url
        with self.lock:
            settings = self.settings()
            for flag in ("enabled", "refresh_quotes"):
                if flag in value:
                    if type(value[flag]) is not bool:
                        raise ValueError("调度开关必须是布尔值")
                    settings[flag] = value[flag]
            if "time" in value:
                if not isinstance(value["time"], str) or not re.fullmatch(r"(?:1[5-9]|2[0-3]):[0-5]\d", value["time"]):
                    raise ValueError("运行时间需为15:00—23:59的北京时间")
                settings["time"] = value["time"]
            if "extra_urls" in value:
                if not isinstance(value["extra_urls"], list) or len(value["extra_urls"]) > 8:
                    raise ValueError("最多8个补充文章链接")
                settings["extra_urls"] = [checked_url(u) for u in value["extra_urls"]]
            if "closed_dates" in value:
                if not isinstance(value["closed_dates"], list) or len(value["closed_dates"]) > 60:
                    raise ValueError("临时休市日期最多60个")
                settings["closed_dates"] = [iso_date(d) for d in value["closed_dates"]]
            atomic_json(self.root / "settings.json", settings)
            return settings

    def history(self):
        path = self.root / "index.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []

    def read(self, report_id):
        if not isinstance(report_id, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}-[a-f0-9]{12}", report_id):
            raise ValueError("复盘记录编号无效")
        try:
            return json.loads((self.root / report_id / "report.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ValueError("复盘记录不存在") from None

    def save(self, report):
        with self.lock:
            report = {**report, "id": report["trade_date"] + "-" + secrets.token_hex(6)}
            folder = self.root / report["id"]
            folder.mkdir()
            if report["body"]:
                (folder / "douyin.txt").write_text(report["title"] + "\n\n" + report["body"], encoding="utf-8")
            if report["title"]:
                (folder / "cover-prompt.txt").write_text(report["cover"]["prompt"], encoding="utf-8")
                cover = render_cover(report["title"], report["trade_date"], design=report.get('title_design'))
                if cover:
                    (folder / "cover.svg").write_text(cover, encoding="utf-8")
            atomic_json(folder / "report.json", report)
            atomic_json(folder / "sources.json", report["sources"])
            atomic_json(folder / "checks.json", {k: report.get(k) for k in ("status", "failure", "missing", "checks", "snapshot", "calendar")})
            history = self.history()
            history.insert(0, {k: report.get(k) for k in ("id", "trade_date", "created_at", "cutoff_at", "status", "title", "char_count")})
            atomic_json(self.root / "index.json", history)
            return report

    def choose_title(self, report_id, index):
        report = copy.deepcopy(self.read(report_id))
        design = report.get('title_design')
        if not design:
            raise ValueError('这份旧稿没有事实型标题方案，请重新生成一版')
        if type(index) is not int or not 0 <= index < len(design['options']):
            raise ValueError('标题方案无效')
        choice = design['options'][index]
        if report['title'] == choice['title']:
            return report
        report.update(title=choice['title'], created_at=now_iso(), title_revision_of=report_id)
        design.update(selected_index=index, selected_title=choice['title'], selected_lines=choice['lines'])
        report['cover'].update(prompt=cover_prompt(report['title'],report['trade_date'],design),
            status='template_ready' if render_cover(report['title'],report['trade_date'],design=design) else 'not_generated')
        return self.save(report)

    def artifact(self, report_id, name):
        self.read(report_id)
        allowed = {"douyin.txt", "cover.svg", "cover.png", "report.json", "sources.json", "checks.json", "cover-prompt.txt", "bundle.zip"}
        if name not in allowed:
            raise ValueError("不支持的导出文件")
        folder = self.root / report_id
        if name == "bundle.zip":
            data = io.BytesIO()
            with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as archive:
                for filename in sorted(allowed - {"bundle.zip"}):
                    path = folder / filename
                    if path.exists():
                        archive.write(path, filename)
            return data.getvalue()
        if not (folder / name).exists():
            raise ValueError("本次未生成该文件，请查看核验结果")
        return (folder / name).read_bytes()
