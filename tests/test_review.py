"""Synthetic fixtures only. No production browser or network access."""
import copy
import json
import re
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from qsa.data import SHANGHAI, SnapshotStore, make_demo
from qsa.review import (DISCLAIMER, ReviewStore, build_review, prior_session,
                        public_stock, reconcile_news, render_cover, trading_day)
from qsa.review_service import ReviewService
from qsa.review_sources import ArticleText, cached_news, checked_url, closing_relevant, metric_values, news_record


def fixture():
    data = make_demo()
    data["meta"]["mode"] = "import"
    data["meta"]["source"] = "合成测试记录，不是真实行情"
    day = data["meta"]["trade_date"]
    codes = {s["code"]: f"{600000+i:06d}.SH" for i, s in enumerate(data["stocks"])}
    for s in data["stocks"]:
        s["code"] = codes[s["code"]]
        s["verification"] = {"status": "matched", "date": day}
        s["board"] = "主板"
    for sector in data["sectors"]:
        sector["members"] = [codes[c] for c in sector["members"]]
    indices = {"上证指数": {"close": 3000., "pct": 1., "published_at": day+"T15:01:00+08:00", "status": "两平台收盘一致"}}
    sources = [{"id": publisher, "kind": "index", "title": "合成指数测试", "publisher": publisher,
        "market_date": day, "published_at": day+"T15:01:00+08:00", "verified_fields": ["上证指数"]} for publisher in ("甲", "乙")]
    context = {"trade_date": day, "cutoff_at": day+"T19:30:00+08:00", "indices": indices, "sources": sources}
    return data, context


class ReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data, cls.context = fixture()

    def report(self, **kwargs):
        return build_review(copy.deepcopy(self.data), copy.deepcopy(self.context), **kwargs)

    def test_annual_holidays_and_weekend_makeup_days(self):
        for day in ("2026-10-01", "2026-10-07", "2026-10-10", "2026-02-23", "2026-02-28"):
            self.assertFalse(trading_day(day))
        self.assertTrue(trading_day("2026-10-08"))
        self.assertIsNone(trading_day("2027-01-04"))
        self.assertFalse(trading_day("2026-10-23", ["2026-10-23"]))
        self.assertEqual(prior_session("2026-10-08"), "2026-09-30")

    def test_copy_contract_and_disclaimer(self):
        r = self.report()
        self.assertEqual(r["status"], "draft")
        self.assertTrue(r["body"].endswith(DISCLAIMER))
        self.assertNotRegex(r["body"], r"\d{6}|买入|卖出|加仓|减仓|止损|目标价|情景一|情景二|情景三")
        self.assertLessEqual(len(r["sectors"]), 3)
        self.assertLessEqual(len(r["signals"]), 3)
        self.assertNotIn("\n", r["forecast"])
        self.assertLessEqual(r["char_count"], 1200)
        self.assertIn("待人工复核", str(r["calendar"]))
        self.assertTrue(all(1 <= len(s["representatives"]) <= 3 for s in r["sectors"]))

    def test_demo_stale_snapshot_and_incomplete_core_fail_closed(self):
        data, context = fixture()
        data["meta"]["mode"] = "demo"
        self.assertEqual(build_review(data, context)["status"], "verification_failed")
        data["meta"]["mode"] = "import"
        context["trade_date"] = "2026-10-21"
        r = build_review(data, context)
        self.assertFalse(r["body"])
        self.assertFalse(r["title"])
        context["trade_date"] = data["meta"]["trade_date"]
        context["indices"] = {}
        self.assertFalse(build_review(data, context)["body"])

    def test_intraday_wrong_date_and_single_index_source_rejected(self):
        for mutation in ("intraday", "wrongdate", "single", "cutoff"):
            data, context = fixture()
            if mutation == "intraday":
                context["indices"]["上证指数"]["published_at"] = context["trade_date"] + "T14:59:00+08:00"
            elif mutation == "wrongdate":
                context["sources"][0]["market_date"] = "2026-10-21"
            elif mutation == "cutoff":
                context["sources"][0]["published_at"] = context["trade_date"]+"T20:00:00+08:00"
            else:
                context["sources"].pop()
            self.assertEqual(build_review(data, context)["status"], "verification_failed", mutation)

    def test_nontrading_day_has_no_market_cover_or_body(self):
        data, context = fixture()
        context["trade_date"] = "2026-10-24"
        r = build_review(data, context)
        self.assertEqual(r["status"], "skipped")
        self.assertFalse(r["body"])
        self.assertEqual(r["cover"]["status"], "not_generated")

    def test_public_scope_excludes_star_bj_st_conflict_and_stale_reference(self):
        from qsa.analytics import stock_row
        row = stock_row(copy.deepcopy(self.data["stocks"][2]), self.data["meta"]["trade_date"])
        self.assertTrue(public_stock(row))
        for code in ("688001.SH", "920001.BJ", "430001.BJ", "600001.BJ"):
            self.assertFalse(public_stock({**row, "code": code}))
        self.assertFalse(public_stock({**row, "is_st": True}))
        self.assertFalse(public_stock({**row, "verification": {"status": "matched", "date": "2026-10-21"}}))
        self.assertFalse(public_stock({**row, "exclusions": ["涨停榜单与日线价格冲突"]}))

    def test_history_title_dedup_and_no_fake_dedup(self):
        first = self.report()
        other = self.report(recent=[first])
        self.assertNotEqual(first["title"], other["title"])
        self.assertIn("未做历史去重", first["checks"][-1])
        self.assertIn("已对比", other["checks"][-1])

    def test_source_scope_and_same_origin_syndication(self):
        metrics = {"limit_up": 82, "broken": 12, "limit_scope": "排除ST及退市股"}
        a = {"id": "a", "publisher": "财联社", "metrics": metrics}
        b = {"id": "b", "publisher": "财联社", "metrics": metrics}
        facts, _ = reconcile_news([a,b])
        self.assertNotIn("limit_up", facts)
        b["publisher"] = "独立原始报道"
        facts, _ = reconcile_news([a,b])
        self.assertAlmostEqual(facts["seal_rate"]["value"], 87.2)
        b["metrics"] = {**metrics, "limit_scope": "沪深北含ST"}
        self.assertNotIn("seal_rate", reconcile_news([a,b])[0])
        b["metrics"] = {**metrics, "limit_up": 84}
        facts, problems = reconcile_news([a,b])
        self.assertNotIn("limit_up", facts)
        self.assertTrue(problems)

    def test_article_values_do_not_infer_missing_statistics(self):
        text = "今日全市场共82股涨停，12股封板未遂，封板率为87%（不含ST股、退市股）。"
        values = metric_values(text)
        self.assertEqual(values["limit_up"], 82)
        self.assertEqual(values["broken"], 12)
        self.assertNotIn("height", values)
        self.assertNotIn("turnover_yuan", values)
        self.assertNotIn("limit_up", metric_values("昨日全市场共82股涨停，12股封板未遂。"))
        self.assertNotIn("turnover_yuan", metric_values("昨日沪深两市成交额19000亿元。"))
        with self.assertRaises(ValueError):
            news_record("午间", text, "2026-10-22T14:00:00+08:00", "https://www.cls.cn/detail/1", "2026-10-22", datetime(2026,10,22,19,30,tzinfo=SHANGHAI), "财联社")

    def test_article_parser_excludes_navigation_scripts_and_footer(self):
        p = ArticleText()
        p.feed('<title>测试</title><nav>999股涨停</nav><article><p>正文</p><script>坏数据</script></article><footer>旧报道</footer>')
        self.assertEqual(p.parts, ["正文"])

    def test_news_cache_requires_same_date_actual_body_hash_and_keeps_read_time(self):
        day = "2026-10-22"
        cutoff = datetime(2026,10,22,19,30,tzinfo=SHANGHAI)
        item = news_record("10月22日涨停分析", "今日全市场共82股涨停，12股封板未遂（不含ST股、退市股）。",
                          day+"T15:14:00+08:00", "https://www.cls.cn/detail/1", day, cutoff, "财联社")
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            root = Path(folder)
            path = root / (day + "-test")
            path.mkdir()
            marker = path / "context.json"
            marker.write_text(json.dumps({"sources": [item]}), encoding="utf-8")
            records = cached_news(day, root, cutoff, [])
            self.assertEqual(len(records), 1)
            self.assertTrue(records[0]["cached"])
            self.assertEqual(records[0]["retrieved_at"], item["retrieved_at"])
            self.assertFalse(cached_news("2026-10-21", root, cutoff, []))
            item["text"] = "被替换的正文"
            marker.write_text(json.dumps({"sources": [item]}), encoding="utf-8")
            self.assertFalse(cached_news(day, root, cutoff, []))
        self.assertFalse(closing_relevant("港股收盘：恒生科技指数涨2%"))
        self.assertFalse(closing_relevant("在岸人民币兑美元收盘报6.6711"))
        self.assertTrue(closing_relevant("收评：创业板指涨超4%"))

    def test_source_urls_and_redirects_are_allowlisted(self):
        for url in ("http://www.cls.cn/", "https://127.0.0.1/", "https://www.cls.cn.evil.test/", "https://user@www.cls.cn/", "https://www.cls.cn:9000/", "file:///etc/passwd"):
            with self.assertRaises(ValueError):
                checked_url(url)
        self.assertEqual(checked_url("https://www.cls.cn/detail/1"), "https://www.cls.cn/detail/1")

    def test_report_versions_and_bundle_are_immutable_and_path_safe(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            store = ReviewStore(folder)
            r = store.save(self.report())
            original = store.read(r["id"])
            newer = store.save(self.report(recent=[r]))
            self.assertNotEqual(r["id"], newer["id"])
            self.assertEqual(original, store.read(r["id"]))
            self.assertTrue(store.artifact(r["id"], "bundle.zip").startswith(b"PK"))
            with self.assertRaises(ValueError):
                store.artifact("../active", "report.json")
            with self.assertRaises(ValueError):
                store.artifact(r["id"], "../settings.json")

    def test_settings_disabled_by_default_and_inputs_bounded(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            store = ReviewStore(folder)
            self.assertFalse(store.settings()["enabled"])
            for bad in ({"enabled": "false"}, {"time": "09:30"}, {"extra_urls": ["https://127.0.0.1/"]}):
                with self.assertRaises(ValueError):
                    store.update_settings(bad)
            self.assertEqual(store.update_settings({"time": "19:31"})["time"], "19:31")

    def test_schedule_once_per_day_restart_disabled_weekend_and_timezone(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            snapshots = SnapshotStore(folder)
            service = ReviewService(snapshots)
            now = datetime(2026,10,23,19,30,tzinfo=SHANGHAI)
            with patch.object(service, "start") as start:
                self.assertFalse(service.tick(now))
                service.store.update_settings({"enabled": True})
                self.assertFalse(service.tick(now.replace(hour=19,minute=29)))
                self.assertTrue(service.tick(now))
                self.assertFalse(service.tick(now))
                start.assert_called_once()
            again = ReviewService(snapshots)
            with patch.object(again, "start") as start:
                self.assertFalse(again.tick(now))
                self.assertFalse(again.tick(now.replace(day=24)))
                self.assertFalse(again.tick(datetime(2027,1,4,20,tzinfo=SHANGHAI)))
                start.assert_not_called()

    def test_cover_exact_ratio_escaped_title_and_missing_asset_fallback(self):
        self.assertIsNone(render_cover("测试", "2026-10-22", Path("missing-image-asset.png")))
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            image = Path(folder) / "image.png"
            image.write_bytes(b"synthetic-test-placeholder")
            svg = render_cover("测试<标题>", "2026-10-22", image)
            self.assertIn('width="1920" height="1080"', svg)
            self.assertIn("&lt;标题&gt;", svg)
            self.assertIn("示意画面，非实时行情", svg)
            self.assertNotIn("<标题>", svg)


if __name__ == "__main__":
    unittest.main()
