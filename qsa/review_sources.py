"""Small, auditable public-data adapters for the closing-review workflow.

No language model or search snippets are treated as factual evidence. News bodies
are read from the publisher, dated, cached and reduced to a few explicit metrics.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time
from html.parser import HTMLParser
from urllib.error import HTTPError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .data import SHANGHAI, atomic_json, now_iso

ALLOWED_HOSTS = {
    "www.cls.cn", "api3.cls.cn", "www.sse.com.cn", "www.szse.cn",
    "english.sse.com.cn", "m.cnfin.com", "www.cnfin.com",
    "www.news.cn", "www.xinhuanet.com", "www.stcn.com",
    "finance.sina.com.cn", "cj.sina.com.cn", "k.sina.com.cn",
    "finance.eastmoney.com", "stock.eastmoney.com",
    "qt.gtimg.cn", "hq.sinajs.cn",
}
UA = {"User-Agent": "Mozilla/5.0 (compatible; AfterCloseLab/0.2)", "Referer": "https://finance.sina.com.cn/"}


def checked_url(url):
    parsed = urlparse(url)
    if (parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS
            or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise ValueError("来源仅支持列表中的公开财经网站 HTTPS 链接")
    return url


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch(url, encoding=None):
    opener = build_opener(NoRedirect())
    for _ in range(4):
        checked_url(url)
        try:
            with opener.open(Request(url, headers=UA), timeout=10) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
                if len(raw) > 2 * 1024 * 1024:
                    raise ValueError("来源正文超过2MB")
                charset = encoding or response.headers.get_content_charset()
                if not charset:
                    match = re.search(br'charset=["\s]*([\w-]+)', raw[:5000], re.I)
                    charset = match[1].decode() if match else "utf-8"
                return raw.decode(charset, errors="replace"), url
        except HTTPError as error:
            if error.code not in (301, 302, 303, 307, 308):
                raise
            url = urljoin(url, error.headers.get("Location", ""))
    raise ValueError("来源重定向过多")


class ArticleText(HTMLParser):
    """Select article containers, ignoring scripts and unrelated page navigation."""
    def __init__(self):
        super().__init__()
        self.stack, self.parts, self.title_parts = [], [], []
        self.active = self.skip = self.in_title = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        marker = " ".join([attrs.get("class", ""), attrs.get("id", "")]).lower()
        is_article = tag == "article" or any(x in marker.split() for x in (
            "detail-content", "article-content", "article_content", "article", "artical-content", "txt_con"))
        skip = tag in ("script", "style", "nav", "footer", "header")
        # HTML void elements do not have end tags.
        if tag not in ("br", "hr", "img", "meta", "link", "input", "source"):
            self.stack.append((tag, is_article, skip))
            self.active += int(is_article)
            self.skip += int(skip)
        self.in_title += int(tag == "title")

    def handle_endtag(self, tag):
        self.in_title = max(0, self.in_title - int(tag == "title"))
        if not any(t[0] == tag for t in self.stack):
            return
        while self.stack:
            old, active, skip = self.stack.pop()
            self.active -= int(active)
            self.skip -= int(skip)
            if old == tag:
                break

    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data)
        if self.active and not self.skip and data.strip():
            self.parts.append(data.strip())


def metric_values(text):
    """Only parse narrowly worded closing statistics, never infer from price moves."""
    text = re.sub(r"[ \t\r]+", "", html.unescape(text)).replace(",", "")
    result = {}
    # Keep U, Z and their definition in one explicitly current-day sentence.
    closing = re.findall(r"今日(?:全市场)?共(\d+)股涨停([^。\n]{0,260})", text)
    if len(closing) == 1:
        value, sentence = closing[0]
        result["limit_up"] = int(value)
        broken = re.findall(r"(\d+)股封板未遂", sentence)
        if len(set(broken)) == 1:
            result["broken"] = int(broken[0])
        result["limit_scope"] = "排除ST及退市股" if "不含ST" in sentence and "退市股" in sentence else "原文未完整说明"
    amount_pattern = r"(?:沪深两市|两市|沪深两市全天)(?:全天)?成交(?:额|金额)?(?:为|达|合计|共计)?(\d+(?:\.\d+)?)(万亿|亿)元"
    amounts = []
    for match in re.finditer(amount_pattern, text):
        if not re.search(r"(?:昨日|上一交易日|上个交易日|上周|去年)[^。，；\n]{0,14}$", text[max(0,match.start()-24):match.start()]):
            amounts.append(match.groups())
    values = {round(float(value) * (1e12 if unit == "万亿" else 1e8)) for value, unit in amounts}
    if len(values) == 1:
        result["turnover_yuan"] = values.pop()
    # U and Z must come from the same explicitly defined statistical pool.
    result.setdefault("limit_scope", "原文未完整说明")
    return result


def news_record(title, text, published, url, day, cutoff, publisher, retrieved_url=None):
    moment = datetime.fromisoformat(published)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=SHANGHAI)
    if moment.date().isoformat() != day or moment.hour < 15 or moment > cutoff:
        raise ValueError("文章不是目标交易日收盘后、截止时间前的正文")
    if not text.strip():
        raise ValueError("没有读取到可识别的文章正文")
    digest = hashlib.sha256(text.encode()).hexdigest()
    return {"id": digest[:16], "kind": "news", "title": title[:180], "url": url,
            "retrieved_url": retrieved_url or url, "publisher": publisher,
            "published_at": moment.isoformat(), "retrieved_at": now_iso(),
            "market_date": day, "scope": "原文范围；不能与其他统计池直接合并",
            "metrics": metric_values(text), "sha256": digest,
            "text": text[:60000], "verified_fields": [], "status": "已读取，指标待交叉核验"}


def read_article(url, day, cutoff):
    raw, final_url = fetch(url)
    next_data = re.search(r'<script[^>]*__NEXT_DATA__[^>]*>(.*?)</script>', raw, re.S)
    if next_data and urlparse(final_url).hostname == "www.cls.cn":
        detail = json.loads(next_data[1]).get("props", {}).get("pageProps", {}).get("articleDetail", {})
        if detail.get("isFree") and detail.get("ctime") and detail.get("content"):
            text = html.unescape(re.sub(r"<[^>]+>", "", detail["content"]))
            stamp = datetime.fromtimestamp(int(detail["ctime"]), SHANGHAI).isoformat()
            return news_record(detail.get("title", "财联社收盘报道"), text, stamp, final_url, day, cutoff, "财联社")
    parser = ArticleText()
    parser.feed(raw)
    # Prefer article publication metadata; never use an unrelated date in the footer.
    patterns = [
        r'<meta[^>]+(?:property|name)=["\'](?:article:published_time|pubdate|publishdate|PubDate)["\'][^>]+content=["\']([^"\']+)',
        r'"(?:datePublished|pub_time|publish_time)"\s*:\s*"([^"]+)"',
    ]
    stamp = next((m[1] for p in patterns if (m := re.search(p, raw, re.I))), None)
    if not stamp:
        raise ValueError("未识别文章发布时间，已保留链接但不引用数值")
    stamp = stamp.replace("年", "-").replace("月", "-").replace("日", " ").replace("/", "-")
    stamp = stamp.strip().replace("Z", "+00:00")
    publisher = urlparse(final_url).hostname
    # A syndication of CLS is still one origin, even on another domain.
    if "财联社" in "".join(parser.title_parts) + " ".join(parser.parts)[:600]:
        publisher = "财联社"
    return news_record("".join(parser.title_parts), "\n".join(parser.parts), stamp,
                       final_url, day, cutoff, publisher)


def read_cls(day, cutoff):
    output = []
    # Closing news window, rather than hundreds of unrelated overnight headlines.
    last_time = int(min(cutoff, datetime.combine(datetime.fromisoformat(day).date(), time(18), SHANGHAI)).timestamp())
    for _ in range(8):
        # Public request checksum, matching the publisher's web client / AKShare adapter.
        params = {"app": "CailianpressWeb", "category": "", "last_time": last_time,
                  "os": "web", "refresh_type": "1", "rn": "20", "sv": "8.4.6"}
        params["sign"] = hashlib.md5(hashlib.sha1(urlencode(params).encode()).hexdigest().encode()).hexdigest()
        url = "https://www.cls.cn/v1/roll/get_roll_list?" + urlencode(params)
        raw, _ = fetch(url)
        payload = json.loads(raw)
        data = payload.get("data") or {}
        items = data.get("roll_data", []) if isinstance(data, dict) else []
        if not items:
            if payload.get("errno") not in (None, 0, "0", 200, "200"):
                raise ValueError(f"财联社接口未返回正文：{str(payload.get('msg', payload.get('errno')))[:80]}")
            break
        for item in items:
            stamp = datetime.fromtimestamp(int(item["ctime"]), SHANGHAI)
            content = html.unescape(re.sub(r"<[^>]+>", "", item.get("content", "")))
            title = item.get("title") or content[:60]
            if stamp.date().isoformat() == day and stamp.hour >= 15 and stamp <= cutoff and closing_relevant(title + content[:60]):
                output.append(news_record(title, content, stamp.isoformat(),
                    f"https://www.cls.cn/detail/{int(item['id'])}", day, cutoff, "财联社", url))
        new_last = min(int(item["ctime"]) for item in items)
        if datetime.fromtimestamp(new_last, SHANGHAI) < datetime.fromisoformat(day + "T15:00:00+08:00") or new_last >= last_time:
            break
        last_time = new_last - 1
    return output


def closing_relevant(text):
    return bool(re.search("涨停分析|收评|收盘|连板", text)) and not (
        re.search("人民币|汇市|港股|恒生|美股|日经|韩股|欧股", text)
        and not re.search("A股|沪深|创业板|上证|连板|涨停", text))


def cached_news(day, evidence_root, cutoff, existing):
    """Reuse only actually read, hashed, dated bodies; keep original read times."""
    output, urls = [], {s["url"] for s in existing}
    for path in sorted(evidence_root.glob(day + "-*/context.json"), reverse=True)[:12]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            for source in value.get("sources", []):
                stamp = datetime.fromisoformat(source["published_at"])
                text = source.get("text", "")
                if (source.get("kind") == "news" and source["url"] not in urls and source.get("market_date") == day
                        and stamp.date().isoformat() == day and stamp.hour >= 15 and stamp <= cutoff
                        and hashlib.sha256(text.encode()).hexdigest() == source.get("sha256")
                        and closing_relevant(source["title"] + text[:60])):
                    checked_url(source["url"])
                    output.append({**source, "cached": True, "metrics": metric_values(text),
                        "verified_fields": [], "status": "同日已读取原文缓存；本次未重新联网核验"})
                    urls.add(source["url"])
        except (OSError, ValueError, TypeError, KeyError):
            continue
    return output


def read_indices(day):
    symbols = [("sh000001", "上证指数"), ("sz399001", "深证成指"), ("sz399006", "创业板指")]
    url = "https://qt.gtimg.cn/q=" + ",".join(s for s, _ in symbols)
    raw, _ = fetch(url, "gb18030")
    values, sources = {}, []
    for symbol, name in symbols:
        match = re.search(r'v_' + symbol + r'="([^"]+)"', raw)
        if not match:
            continue
        f = match[1].split("~")
        stamp = datetime.strptime(f[30], "%Y%m%d%H%M%S").replace(tzinfo=SHANGHAI)
        close, previous, change = float(f[3]), float(f[4]), float(f[32])
        if stamp.date().isoformat() != day or stamp.hour < 15 or min(close, previous) <= 0 or abs((close / previous - 1)*100-change) > .03:
            continue
        values[name] = {"close": close, "pre_close": previous, "pct": change, "published_at": stamp.isoformat(), "status": "单源"}
    if values:
        sources.append({"id": "tencent-index", "kind": "index", "title": "腾讯指数收盘报价",
            "url": url, "publisher": "腾讯", "published_at": max(v["published_at"] for v in values.values()),
            "retrieved_at": now_iso(), "market_date": day, "scope": "三个指数价格；不将指数成交额相加代替沪深成交额",
            "verified_fields": list(values), "status": "日期及收盘涨幅算术已检查", "text": raw})
    try:
        reference_url = "https://hq.sinajs.cn/list=" + ",".join(s for s, _ in symbols)
        reference, _ = fetch(reference_url, "gb18030")
        matched, stamps = [], []
        for symbol, name in symbols:
            match = re.search(r'hq_str_' + symbol + r'="([^"]+)"', reference)
            if not match or name not in values:
                continue
            f = match[1].split(",")
            if len(f) >= 32 and f[30] == day and f[31] >= "15:00:00":
                stamps.append(day + "T" + f[31] + "+08:00")
                if abs(float(f[3]) - values[name]["close"]) <= .011 and abs(float(f[2]) - values[name]["pre_close"]) <= .011:
                    values[name]["status"] = "两平台收盘一致"
                    matched.append(name)
                else:
                    values.pop(name)  # Conflicts never enter public copy.
        sources.append({"id": "sina-index", "kind": "index", "title": "新浪指数收盘参考",
            "url": reference_url, "publisher": "新浪", "published_at": max(stamps) if stamps else None,
            "retrieved_at": now_iso(), "market_date": day, "scope": "仅交叉核对收盘价格；不认证数据源独立性",
            "verified_fields": matched, "status": f"{len(matched)}个指数一致", "text": reference})
    except Exception:
        pass
    return values, sources


def collect_context(day, root, extra_urls=(), cutoff=None, progress=lambda *_: None):
    cutoff = cutoff or datetime.now(SHANGHAI)
    root.mkdir(parents=True, exist_ok=True)
    context = {"trade_date": day, "cutoff_at": cutoff.isoformat(), "indices": {}, "sources": [], "errors": []}
    progress("读取带日期的指数与收盘报道")
    jobs = [("indices", lambda: read_indices(day)), ("news", lambda: read_cls(day, cutoff))]
    for url in dict.fromkeys(extra_urls):
        checked_url(url)
        jobs.append(("article " + url, lambda u=url: read_article(u, day, cutoff)))
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [(kind, pool.submit(fn)) for kind, fn in jobs]
        for kind, future in futures:
            try:
                value = future.result()
                if kind == "indices":
                    context["indices"], records = value
                else:
                    records = value if kind == "news" else [value]
                context["sources"].extend(records)
            except Exception as error:
                context["errors"].append(f"{kind}: {type(error).__name__}: {str(error)[:180]}")
    if not any(s["kind"] == "news" for s in context["sources"]):
        reused = cached_news(day, root.parent, cutoff, context["sources"])
        context["sources"].extend(reused)
        context["errors"].append(f"收盘新闻本次未成功取得，复用{len(reused)}条同日已读取原文缓存，原读取时间保留" if reused else "未取得可引用的同日A股收盘报道，新闻相关结论省略")
    seen = set()
    context["sources"] = [s for s in context["sources"] if not (s["id"] in seen or seen.add(s["id"]))]
    for source in context["sources"]:
        atomic_json(root / f"{source['id']}.json", source)
    atomic_json(root / "context.json", context)
    return context
