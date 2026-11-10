"""Synthetic history and HTTP-dispatch tests; no network or browser access."""
import copy
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from app import Workbench, make_handler
from qsa.analytics import stock_detail
from qsa.data import SnapshotStore, make_demo
from qsa.stock_history import StockHistoryService, fetch_history, merge_history


def fixture():
    demo = make_demo()
    stock = copy.deepcopy(demo["stocks"][2])
    day = demo["meta"]["trade_date"]
    stock.update(code="600838.SH", name="合成历史测试股", board="主板",
                 verification={"status": "matched", "date": day})
    bars = copy.deepcopy(stock["bars"])
    stock["bars"] = [copy.deepcopy(bars[-1])]
    stock["bars"][-1]["adj_factor"] = None
    data = {"schema_version": 1, "meta": {**demo["meta"], "mode": "import", "source": "合成测试，非真实行情"},
            "stocks": [stock], "sectors": []}
    return data, bars


class StockHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.root = Path(self.temp.name)
        self.data, self.bars = fixture()
        self.store = SnapshotStore(self.root)
        self.store.save(self.data)
        self.service = StockHistoryService(self.store)

    def tearDown(self):
        self.service.pool.shutdown(wait=True, cancel_futures=True)
        self.temp.cleanup()

    def test_unadjusted_quote_is_visible_without_invented_ma(self):
        detail = stock_detail(self.data, "600838.SH")
        self.assertEqual(detail["adjustment"], "raw")
        self.assertEqual(len(detail["bars"]), 1)
        self.assertIsNone(detail["bars"][0]["ma5"])
        self.assertTrue(detail["needs_history"])

    def test_history_enrichment_preserves_current_quote_and_computes_indicators(self):
        before = copy.deepcopy(self.data)
        enriched = merge_history(self.data, "600838.SH", self.bars, "合成历史")
        detail = stock_detail(enriched, "600838.SH")
        self.assertEqual(self.data, before)
        self.assertEqual(detail["adjustment"], "qfq")
        self.assertEqual(len(detail["bars"]), 60)
        self.assertEqual(detail["technical"]["sessions"], 150)
        self.assertIsNotNone(detail["technical"]["macd"])
        self.assertIsNotNone(detail["bars"][-1]["ma20"])
        self.assertFalse(detail["needs_history"])
        self.assertEqual(enriched["stocks"][0]["verification"], self.data["stocks"][0]["verification"])
        for key, value in self.data["stocks"][0]["bars"][-1].items():
            if key != "adj_factor":
                self.assertEqual(enriched["stocks"][0]["bars"][-1][key], value)

    def test_stale_conflicting_missing_or_invalid_history_cannot_replace_quote(self):
        original = copy.deepcopy(self.data)
        for kind in ("stale", "close", "high", "factor", "duplicate", "unordered", "nonfinite"):
            bars = copy.deepcopy(self.bars)
            if kind == "stale": bars.pop()
            elif kind in ("close", "high"): bars[-1][kind] += 1
            elif kind == "factor": bars[-20]["adj_factor"] = None
            elif kind == "duplicate": bars.insert(-1, copy.deepcopy(bars[-2]))
            elif kind == "unordered": bars[0], bars[1] = bars[1], bars[0]
            else: bars[-10]["adj_factor"] = float("nan")
            with self.assertRaises(ValueError, msg=kind):
                merge_history(self.data, "600838.SH", bars, "合成历史")
            self.assertEqual(self.data, original)

    def test_short_listing_history_is_not_refetched_forever(self):
        short = self.bars[-4:]
        enriched = merge_history(self.data, "600838.SH", short, "合成短历史")
        detail = stock_detail(enriched, "600838.SH")
        self.assertEqual(len(detail["bars"]), 4)
        self.assertFalse(detail["needs_history"])
        self.assertIsNone(detail["technical"]["macd"])

    def test_provider_fallback_validates_and_retains_attempt_evidence(self):
        with patch("qsa.free_provider.history_tencent", side_effect=ValueError("合成接口失败")) as first, \
             patch("qsa.free_provider.history", return_value=self.bars) as second:
            bars, source, evidence = fetch_history(self.data, "600838.SH", self.root)
            self.assertEqual(source, "东方财富历史日线")
            self.assertEqual(len(bars), 150)
            first.assert_called_once()
            second.assert_called_once()
            result = json.loads((self.root / evidence / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(result["previous_attempts"][0]["source"], "腾讯历史日线")

    def test_background_fetch_deduplicates_and_cached_data_does_not_fetch_again(self):
        started, release = threading.Event(), threading.Event()
        def fake_fetch(*args):
            started.set()
            if not release.wait(3): raise RuntimeError("test timed out")
            return self.bars, "合成历史", "test-evidence"
        with patch("qsa.stock_history.fetch_history", side_effect=fake_fetch) as fetch:
            self.service.start({"code": "600838.SH"})
            self.assertTrue(started.wait(2))
            self.service.start({"code": "600838.SH"})
            release.set()
            self.service.pool.shutdown(wait=True)
            fetch.assert_called_once()
        self.assertEqual(self.service.status("600838.SH")["job"]["state"], "done")
        self.assertFalse(self.service.status("600838.SH")["detail"]["needs_history"])
        with patch("qsa.stock_history.fetch_history") as fetch:
            self.assertEqual(self.service.start({"code": "600838.SH"})["job"]["state"], "done")
            fetch.assert_not_called()

    def test_switching_snapshot_during_fetch_cannot_overwrite_new_quote(self):
        started, release = threading.Event(), threading.Event()
        def fake_fetch(*args):
            started.set()
            if not release.wait(3): raise RuntimeError("test timed out")
            return self.bars, "合成历史", "test-evidence"
        with patch("qsa.stock_history.fetch_history", side_effect=fake_fetch):
            job = self.service.start({"code": "600838.SH"})
            self.assertTrue(started.wait(2))
            newer = copy.deepcopy(self.data)
            newer["meta"]["fetched_at"] = "2026-10-22T23:59:59+08:00"
            self.store.save(newer)
            release.set()
            self.service.pool.shutdown(wait=True)
        self.assertEqual(len(self.store.read()["stocks"][0]["bars"]), 1)
        self.assertEqual(next(iter(self.service.jobs.values()))["state"], "error")

    def test_concurrent_stocks_merge_into_latest_snapshot(self):
        second = copy.deepcopy(self.data["stocks"][0])
        second["code"] = "600839.SH"
        self.data["stocks"].append(second)
        self.store.save(self.data)
        barrier = threading.Barrier(2)
        def fake_fetch(*args):
            barrier.wait(timeout=3)
            return self.bars, "合成历史", "test-evidence"
        with patch("qsa.stock_history.fetch_history", side_effect=fake_fetch):
            self.service.start({"code": "600838.SH"})
            self.service.start({"code": "600839.SH"})
            self.service.pool.shutdown(wait=True)
        self.assertEqual([len(s["bars"]) for s in self.store.read()["stocks"]], [150,150])

    def test_failed_fetch_preserves_snapshot(self):
        before = (self.root / "active.json").read_bytes()
        with patch("qsa.stock_history.fetch_history", side_effect=ValueError("合成失败")):
            self.service.start({"code": "600838.SH"})
            self.service.pool.shutdown(wait=True)
        self.assertEqual((self.root / "active.json").read_bytes(), before)
        self.assertIn("可重试", self.service.status("600838.SH")["job"]["message"])

    def test_invalid_requests_do_not_start_network_jobs(self):
        for payload in ({"code": "../active"}, {"code": "600838.SH", "mode": "demo"},
                        {"code": "600838.SH", "date": "2026-10-21"}, {"code": "600838.SH", "mode": "unknown"}):
            with self.assertRaises(ValueError):
                self.service.start(payload)
        self.assertFalse(self.service.jobs)

    def test_api_history_has_csrf_and_serves_current_detail_without_network(self):
        workbench = Workbench(self.root)
        Handler = make_handler(workbench)
        handler = Handler.__new__(Handler)
        responses = []
        handler.send = lambda *args: responses.append(args)
        handler.path = "/api/stock/history?code=600838.SH&date=2026-10-22"
        handler.headers = {"Host": "127.0.0.1:8766"}
        handler.do_GET()
        self.assertEqual(responses[-1][0], 200)
        self.assertEqual(responses[-1][1]["detail"]["adjustment"], "raw")
        raw = json.dumps({"code": "600838.SH"}).encode()
        handler.path = "/api/stock/history"
        handler.headers["Content-Length"] = str(len(raw))
        handler.rfile = io.BytesIO(raw)
        handler.do_POST()
        self.assertEqual(responses[-1][0], 403)
        self.assertFalse(workbench.histories.jobs)
        workbench.histories.close()
        workbench.reviews.close()


if __name__ == "__main__":
    unittest.main()
