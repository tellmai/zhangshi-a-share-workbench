"""Independent-account and frozen batch invariants; synthetic quotes, no network."""
import copy
import csv
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from qsa.data import SnapshotStore
from qsa.quant import replay
from qsa.quant_service import QuantService
from qsa.quant_stocks import replay_stocks, select_stocks
from test_quant import toy, price


def two_stocks():
    data, cfg = toy()
    second = copy.deepcopy(data["stocks"][0])
    second.update(code="DEMO002", name="另一合成股")
    data["stocks"].append(second)
    return data, cfg, data["meta"]["calendar"][30:38]


class StockReplayTests(unittest.TestCase):
    def test_independent_capital_matches_single_stock_replay_not_shared_portfolio(self):
        d, c, days = two_stocks(); c["max_positions"] = 5
        price(d["stocks"][1]["bars"][32], 12.)
        before = copy.deepcopy(d)
        r = replay_stocks(d, c, ["DEMO001", "DEMO002"], days[0], days[-1])
        self.assertEqual(r["summary"]["completed"], 2)
        self.assertEqual(r["config"]["max_positions"], 5)
        self.assertEqual(r["execution_config"]["max_positions"], 1)
        self.assertNotIn("metrics", r)  # No invented combined return.
        for s, child in zip(d["stocks"], r["results"]):
            expected = replay({**d, "stocks": [s]}, {**c, "max_positions": 1}, days[0], days[-1])
            self.assertEqual(child["metrics"], expected["metrics"])
            self.assertEqual(child["curve"][0]["equity"], c["capital"])
            self.assertEqual(child["orders"][0]["shares"], 10000)
            self.assertEqual(child["orders"][0]["date"], days[1])
            self.assertTrue(all(t["entry_date"] < t["exit_date"] for t in child["trades"]))
        self.assertEqual(d, before)

    def test_bad_selection_is_rejected_atomically_and_valid_duplicates_deduplicate(self):
        d, _, _ = two_stocks()
        self.assertEqual(len(select_stocks(d, "DEMO001, DEMO001；另一合成股")), 2)
        for bad in ([], "DEMO001 unknown", ["DEMO001"]*21, {"code":"DEMO001"}, [None]):
            with self.assertRaises(ValueError): select_stocks(d, bad)
        d["meta"]["mode"] = "import"
        d["stocks"][0].update(code="600838.SH", name="上海九百")
        self.assertEqual(select_stocks(d, "sh600838")[0]["code"], "600838.SH")
        self.assertEqual(select_stocks(d, ["600838"])[0]["code"], "600838.SH")
        d["stocks"][0]["is_st"] = True
        with self.assertRaises(ValueError): select_stocks(d, "600838")

    def test_insufficient_start_warmup_is_skipped_without_shifting_dates(self):
        d, c, days = two_stocks(); d["stocks"][1]["bars"] = d["stocks"][1]["bars"][20:]
        r = replay_stocks(d, c, ["DEMO001", "DEMO002"], days[0], days[-1])
        self.assertEqual(r["status"], "partial")
        bad = r["results"][1]
        self.assertEqual(bad["status"], "skipped"); self.assertIsNone(bad["metrics"])
        self.assertIn("仅11根", bad["failure"])
        self.assertEqual(r["results"][0]["curve"][0]["date"], days[0])

    def test_missing_end_or_adjustment_never_produces_full_period_metrics(self):
        d, c, days = two_stocks()
        d["stocks"][0]["bars"] = d["stocks"][0]["bars"][:35]
        d["stocks"][1]["bars"][33]["adj_factor"] = None
        r = replay_stocks(d, c, ["DEMO001", "DEMO002"], days[0], days[-1])
        self.assertEqual(r["status"], "incomplete")
        self.assertTrue(all(s["metrics"] is None for s in r["results"]))
        self.assertIn("末日", r["results"][0]["failure"])
        self.assertIn("复权因子", r["results"][1]["failure"])

    def test_corporate_action_halts_only_the_affected_account(self):
        d, c, days = two_stocks(); d["stocks"][0]["bars"][32]["adj_factor"] = 1.2
        r = replay_stocks(d, c, ["DEMO001", "DEMO002"], days[0], days[-1])
        self.assertEqual(r["results"][0]["status"], "incomplete")
        self.assertIsNone(r["results"][0]["metrics"])
        self.assertEqual(r["results"][1]["status"], "completed")
        self.assertEqual(r["results"][1]["curve"][-1]["date"], days[-1])

    def test_no_signal_is_completed_with_zero_return_and_undefined_win_rate(self):
        d, c, days = two_stocks(); c["min_return5"] = 50
        r = replay_stocks(d, c, ["DEMO001"], days[0], days[-1])["results"][0]
        self.assertEqual(r["status"], "completed")
        self.assertEqual(r["metrics"]["total_return_pct"], 0)
        self.assertIsNone(r["metrics"]["win_rate_pct"])
        self.assertEqual(r["signal_days"], 0)
        self.assertEqual(sum(r["entry_exclusions"].values()), len(days))

    def test_internal_missing_quotes_are_counted_and_do_not_shorten_curve(self):
        d, c, days = two_stocks(); d["stocks"][0]["bars"].pop(33)
        r = replay_stocks(d, c, ["DEMO001"], days[0], days[-1])["results"][0]
        self.assertEqual(r["coverage"]["missing_sessions"], 1)
        self.assertEqual(len(r["curve"]), len(days))
        self.assertGreater(sum(r["skips"].values()), 0)

    def test_future_changes_cannot_affect_prior_accounts_or_orders(self):
        d, c, days = two_stocks()
        first = replay_stocks(d, c, ["DEMO001", "DEMO002"], days[0], days[-1])
        price(d["stocks"][0]["bars"][37], 15.)
        second = replay_stocks(d, c, ["DEMO001", "DEMO002"], days[0], days[-1])
        self.assertEqual(first["results"][1], second["results"][1])
        self.assertEqual(first["results"][0]["curve"][:-1], second["results"][0]["curve"][:-1])
        self.assertEqual([o for o in first["results"][0]["orders"] if o["date"] < days[-1]],
                         [o for o in second["results"][0]["orders"] if o["date"] < days[-1]])

    def test_cancellation_between_accounts_and_daily_history_conditions(self):
        d, c, days = two_stocks(); flag = [False]
        def progress(_, message):
            if message.startswith("独立回测 2/"): flag[0] = True
        with self.assertRaises(InterruptedError):
            replay_stocks(d, c, ["DEMO001", "DEMO002"], days[0], days[-1], progress, lambda:flag[0])
        for extra in ({"min_boards":1}, {"sector_min":1}, {"ranking":"relay"}):
            with self.assertRaises(ValueError): replay_stocks(d, {**c, **extra}, ["DEMO001"], days[0], days[-1])


class StockRunStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.snapshots = SnapshotStore(Path(self.temp.name))
        self.data, self.cfg, self.days = two_stocks(); self.snapshots.demo = self.data
        self.service = QuantService(self.snapshots)

    def tearDown(self):
        if getattr(self.service, "worker", None): self.service.worker.join(5)
        self.temp.cleanup()

    def run_batch(self, **extra):
        payload = {"type":"stock_backtest", "mode":"demo", "config":self.cfg,
            "codes":[s["code"] for s in self.data["stocks"]], "start":self.days[0], "end":self.days[-1], **extra}
        self.service.start(payload); self.service.worker.join(5)
        self.assertFalse(self.service.worker.is_alive())
        return self.service.store.read_run(self.service.job["result_id"])

    def test_search_and_bulk_resolution_use_same_scope_and_reject_invalid_inputs(self):
        rows = self.service.stock_choices("demo", "另一")["stocks"]
        self.assertEqual([s["code"] for s in rows], ["DEMO002"])
        self.assertEqual(rows[0]["history_bars"], 80)
        self.assertEqual(len(self.service.stock_choices("demo", values="DEMO001,DEMO002")["stocks"]), 2)
        for bad in ("other", "../../data"):
            with self.assertRaises(ValueError): self.service.stock_choices(bad)

    def test_frozen_batch_reproduces_after_quotes_and_saved_library_change(self):
        saved = self.service.store.save_strategy({"name":"逐股规则", "config":self.cfg})
        first = self.run_batch(strategy_id=saved["id"], strategy_version=1)
        self.snapshots.demo["stocks"][0]["bars"][35]["close"] = 50
        self.service.store.save_strategy({"id":saved["id"],"name":"新名称","config":{**self.cfg,"capital":200000}})
        with patch("qsa.stock_history.fetch_history", side_effect=AssertionError("复现不得联网")):
            self.service.start({}, replay_id=first["id"]); self.service.worker.join(5)
        second = self.service.store.read_run(self.service.job["result_id"])
        self.assertEqual(first["results"], second["results"])
        self.assertEqual(first["input_digest"], second["input_digest"])
        self.assertEqual(second["strategy_name"], "逐股规则")
        self.assertEqual(self.service.store.history()[0]["summary"]["completed"], 2)

    def test_summary_csv_keeps_failed_row_blank_metrics_and_neutralizes_names(self):
        self.data["stocks"][0]["name"] = "=1+1"
        self.data["stocks"][1]["bars"] = self.data["stocks"][1]["bars"][-1:]
        result = self.run_batch()
        raw, _ = self.service.store.export(result["id"], "csv")
        rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
        self.assertEqual(len(rows), 3); self.assertEqual(rows[1][5], "'=1+1")
        self.assertEqual(rows[2][6], "skipped"); self.assertEqual(rows[2][8], "")
        self.assertTrue(rows[2][-1]); self.assertIn("results", result)

    def test_history_is_validated_cached_and_frozen_preserving_original_quote(self):
        self.data["meta"]["mode"] = "import"
        self.data["stocks"][0]["code"] = "600838.SH"; self.data["stocks"][1]["code"] = "000001.SZ"
        bars = copy.deepcopy(self.data["stocks"][0]["bars"])
        self.data["stocks"][0]["bars"] = bars[-1:]
        self.snapshots.save(self.data); before = copy.deepcopy(self.snapshots.read("active"))
        with patch("qsa.stock_history.fetch_history", return_value=(bars,"合成历史来源","evidence/test")) as fetch:
            result = self.run_batch(mode="active", auto_history=True)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(result["summary"]["completed"], 2)
        frozen = self.service.store.read_input(result["input_digest"])
        self.assertEqual(len(frozen["stocks"][0]["bars"]), 80)
        cached=self.snapshots.read("active")
        self.assertEqual(len(cached["stocks"][0]["bars"]),80)
        for key,value in before["stocks"][0]["bars"][-1].items():
            if key!="adj_factor": self.assertEqual(cached["stocks"][0]["bars"][-1][key],value)
        self.assertEqual(cached["stocks"][1],before["stocks"][1])
        self.assertEqual(result["history_preparation"][0]["cache_status"],"saved")
        with patch("qsa.stock_history.fetch_history", side_effect=AssertionError("复现不得联网")):
            self.service.start({}, replay_id=result["id"]); self.service.worker.join(5)
        again = self.service.store.read_run(self.service.job["result_id"])
        self.assertEqual(result["results"], again["results"])

    def test_history_fetch_failure_is_isolated_with_receipt_and_other_stock_result(self):
        self.data["meta"]["mode"] = "import"
        self.data["stocks"][0]["code"] = "600838.SH"; self.data["stocks"][1]["code"] = "000001.SZ"
        self.data["stocks"][0]["bars"] = self.data["stocks"][0]["bars"][-1:]
        self.snapshots.save(self.data)
        with patch("qsa.stock_history.fetch_history", side_effect=ValueError("来源日期冲突")):
            result = self.run_batch(mode="active", auto_history=True)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["history_preparation"][0]["message"], "来源日期冲突")
        self.assertEqual(result["results"][1]["status"], "completed")

    def test_history_chain_validator_requires_requested_period_before_accepting_source(self):
        self.data["meta"]["mode"] = "import"
        self.data["stocks"][0]["code"] = "600838.SH"; self.data["stocks"][1]["code"] = "000001.SZ"
        bars = copy.deepcopy(self.data["stocks"][0]["bars"])
        self.data["stocks"][0]["bars"] = bars[-1:]; self.snapshots.save(self.data)
        tried = []
        def chain(code, day, root, evidence, validator, progress):
            progress("合成来源正在补齐历史")
            with self.assertRaisesRegex(ValueError, "未覆盖本次回测"):
                validator(bars[-1:], "短历史来源")
            tried.append("short-rejected")
            validator(bars, "长历史来源"); tried.append("long-accepted")
            return bars, "长历史来源", [{"source":"短历史来源","message":"预热不足"}]
        with patch("qsa.source_adapters.history_chain", side_effect=chain):
            result = self.run_batch(mode="active", auto_history=True)
        self.assertEqual(tried, ["short-rejected", "long-accepted"])
        self.assertEqual(result["summary"]["completed"], 2)
        self.assertEqual(result["history_preparation"][0]["source"], "长历史来源")

    def test_preflight_checks_exact_period_without_downloading_or_writing(self):
        self.data["stocks"][1]["bars"]=self.data["stocks"][1]["bars"][-1:]
        before=copy.deepcopy(self.data)
        payload={"mode":"demo","codes":["DEMO001","DEMO002"],"config":self.cfg,"start":self.days[0],"end":self.days[-1]}
        with patch("qsa.stock_history.fetch_history") as fetch:
            checked=self.service.check_stocks(payload)
        fetch.assert_not_called();self.assertEqual(self.data,before)
        self.assertEqual(checked["ready"],1)
        self.assertEqual(checked["stocks"][0]["status"],"ready")
        self.assertEqual(checked["stocks"][1]["status"],"needs_history")
        self.assertIn("仅有当日报价",checked["stocks"][1]["message"])
        later_requirement=self.service.check_stocks({**payload,"config":{**self.cfg,"min_bars":60}})
        self.assertEqual(later_requirement["ready"],0)
        self.assertEqual(later_requirement["stocks"][0]["coverage"]["required_bars"],60)

    def test_new_live_run_downloads_by_default_and_next_run_reuses_validated_cache(self):
        self.data["meta"]["mode"]="import"
        self.data["stocks"][0]["code"]="600838.SH";self.data["stocks"][1]["code"]="000001.SZ"
        bars=copy.deepcopy(self.data["stocks"][0]["bars"])
        self.data["stocks"][0]["bars"]=bars[-1:];self.snapshots.save(self.data)
        with patch("qsa.free_provider.history_tencent",return_value=bars) as source:
            first=self.run_batch(mode="active")
        self.assertTrue(first["request"]["auto_history"])
        self.assertEqual(first["summary"]["completed"],2);source.assert_called_once()
        with patch("qsa.stock_history.fetch_history") as download:
            second=self.run_batch(mode="active")
        download.assert_not_called();self.assertEqual(first["results"],second["results"])
        with patch.object(self.service,"cache_history") as write_cache,patch("qsa.stock_history.fetch_history") as download:
            self.service.start({},replay_id=first["id"]);self.service.worker.join(5)
        download.assert_not_called();write_cache.assert_not_called()
        reproduced=self.service.store.read_run(self.service.job["result_id"])
        self.assertEqual(first["results"],reproduced["results"])

    def test_cache_does_not_overwrite_changed_quote_or_replace_longer_history(self):
        self.data["meta"]["mode"]="import"
        self.data["stocks"][0]["code"]="600838.SH";self.data["stocks"][1]["code"]="000001.SZ"
        self.snapshots.save(self.data);original=copy.deepcopy(self.data)
        receipt=self.service.cache_history(original,"600838.SH",original["stocks"][0]["bars"][-30:],"合成来源","evidence/test")
        self.assertEqual(receipt["cache_status"],"already_ready")
        self.assertEqual(len(self.snapshots.read()["stocks"][0]["bars"]),80)
        changed=copy.deepcopy(self.data);changed["stocks"][0]["bars"][-1]["close"]+=.1
        self.snapshots.save(changed)
        receipt=self.service.cache_history(original,"600838.SH",original["stocks"][0]["bars"],"合成来源","evidence/test")
        self.assertEqual(receipt["cache_status"],"snapshot_changed")
        self.assertEqual(self.snapshots.read(),changed)

    def test_cancel_after_history_preparation_is_saved_without_starting_replay(self):
        self.data["meta"]["mode"] = "import"
        self.data["stocks"][0]["code"] = "600838.SH"; self.data["stocks"][1]["code"] = "000001.SZ"
        bars = copy.deepcopy(self.data["stocks"][0]["bars"])
        self.data["stocks"][0]["bars"] = bars[-1:]; self.snapshots.save(self.data)
        def fetched(*args, **kwargs):
            self.service.cancel.set()
            return bars, "合成历史", "evidence/test"
        with patch("qsa.stock_history.fetch_history", side_effect=fetched), patch("qsa.quant_service.replay_stocks") as replay_batch:
            result = self.run_batch(mode="active", auto_history=True)
        self.assertEqual(result["status"], "cancelled")
        replay_batch.assert_not_called()


if __name__ == "__main__": unittest.main()
