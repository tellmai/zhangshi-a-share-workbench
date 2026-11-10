import copy
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from qsa.analytics import WEIGHTS, analyze, indicators, percentile, settings, stock_row
from qsa.data import SnapshotStore, attach_reference, make_demo, validate_dataset
from qsa.free_provider import eligible_sina_codes, get_limit_pool, share_volume


class ResearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.demo = make_demo()

    def test_market_statistics_do_not_depend_on_screen_filters(self):
        loose = analyze(self.demo, {"min_score": 0, "min_amount": 0, "min_sector_score": 0, "include_one_word": True})
        strict = analyze(self.demo, {"min_score": 100, "min_amount": 1e12})
        self.assertEqual(loose["market"], strict["market"])
        self.assertEqual(loose["market"]["height"], 5)
        self.assertEqual(loose["market"]["active_count"], 90)
        self.assertFalse(strict["candidates"])

    def test_no_percent_threshold_is_used_to_invent_limit_up(self):
        stock = copy.deepcopy(self.demo["stocks"][0])
        stock["limit"] = {}
        stock["bars"][-1].pop("up_limit")
        stock["bars"][-1].pop("down_limit")
        row = stock_row(stock, self.demo["meta"]["trade_date"])
        self.assertGreater(row["pct_chg"], 9.9)
        self.assertIsNone(row["limit_status"])

    def test_macd_requires_120_adjusted_observations(self):
        bars = self.demo["stocks"][0]["bars"]
        self.assertIsNone(indicators(bars[-119:])["macd"])
        self.assertIsNotNone(indicators(bars[-120:])["macd"])

    def test_volume_denominator_excludes_today(self):
        bars = copy.deepcopy(self.demo["stocks"][0]["bars"][-21:])
        for bar in bars[:-1]:
            bar["volume_shares"] = 100
        bars[-1]["volume_shares"] = 200
        self.assertEqual(indicators(bars)["volume_ratio"], 2)

    def test_adjustment_factor_prevents_split_from_becoming_negative_momentum(self):
        bars = []
        for index in range(30):
            close = 100 if index < 20 else 50
            bars.append({"close": close, "open": close, "high": close, "low": close, "volume_shares": 100, "adj_factor": 1 if index < 20 else 2})
        tech = indicators(bars)
        self.assertEqual(tech["return20"], 0)
        self.assertEqual(tech["ma20"], 50)

    def test_missing_factor_never_defaults_to_one(self):
        bars = copy.deepcopy(self.demo["stocks"][1]["bars"])
        bars[-5]["adj_factor"] = None
        tech = indicators(bars)
        self.assertIsNone(tech["ma20"])
        self.assertIsNone(tech["macd"])

    def test_sector_missing_constituent_does_not_receive_score(self):
        data = copy.deepcopy(self.demo)
        data["sectors"][0]["members"].append("MISSING.SH")
        sector = next(s for s in analyze(data)["sectors"] if s["id"] == "I0")
        self.assertIsNone(sector["score"])
        self.assertFalse(sector["complete"])

    def test_historical_constituents_cannot_be_labeled_as_today(self):
        data = copy.deepcopy(self.demo)
        data["sectors"][0]["asof_date"] = "2026-10-21"
        with self.assertRaises(ValueError):
            validate_dataset(data)

    def test_duplicate_future_and_invalid_ohlc_rejected(self):
        for mutate in (lambda b: b.append(copy.deepcopy(b[-1])), lambda b: b[-1].update(date="2099-01-01"), lambda b: b[-1].update(high=.01)):
            data = copy.deepcopy(self.demo)
            mutate(data["stocks"][0]["bars"])
            with self.assertRaises(ValueError):
                validate_dataset(data)

    def test_nonfinite_quotes_rejected(self):
        data = copy.deepcopy(self.demo)
        data["stocks"][0]["bars"][-1]["close"] = math.nan
        with self.assertRaises(ValueError):
            validate_dataset(data)

    def test_reference_conflict_excluded_but_saved_with_evidence(self):
        data = copy.deepcopy(self.demo)
        candidate = analyze(data)["candidates"][0]
        result, count = attach_reference(data, {"trade_date": data["meta"]["trade_date"], "source": "test reference", "rows": [{"code": candidate, "close": 12345}]})
        self.assertEqual(count, 1)
        after = analyze(result)
        self.assertNotIn(candidate, after["candidates"])
        row = next(s for s in after["stocks"] if s["code"] == candidate)
        self.assertIn("主源与参考收盘价冲突", row["exclusions"])
        self.assertEqual(next(s for s in data["stocks"] if s["code"] == candidate)["verification"]["status"], "demo")

    def test_reference_requires_same_date(self):
        with self.assertRaises(ValueError):
            attach_reference(self.demo, {"trade_date": "2026-10-21", "source": "second", "rows": []})

    def test_st_filter_is_shared_by_market_and_candidates(self):
        data = copy.deepcopy(self.demo)
        data["stocks"][1]["name"] = "ST演示"
        result = analyze(data)
        self.assertEqual(result["market"]["excluded_st"], 1)
        self.assertNotIn(data["stocks"][1]["code"], result["candidates"])

    def test_all_modes_have_transparent_weights_and_score_bounds(self):
        for style, weights in WEIGHTS.items():
            self.assertEqual(sum(weights.values()), 100)
            result = analyze(self.demo, {"style": style})
            for row in result["stocks"]:
                if row["score"] is not None:
                    self.assertGreaterEqual(row["score"], 0)
                    self.assertLessEqual(row["score"], 100)
                    expected = max(0, round(sum(c["earned"] for c in row["components"]) - sum(p["points"] for p in row["penalties"]), 1))
                    self.assertEqual(row["score"], expected)

    def test_empty_candidate_pool_is_valid(self):
        result = analyze(self.demo, {"min_amount": 1e12})
        self.assertEqual(result["candidates"], [])

    def test_equal_values_have_midrank_not_perfect_rank(self):
        self.assertEqual(percentile([3, 3, 3], 3), .5)

    def test_partial_history_chart_uses_a_fixed_cohort(self):
        data = copy.deepcopy(self.demo)
        data["stocks"][5]["bars"] = data["stocks"][5]["bars"][-1:]
        market = analyze(data)["market"]
        self.assertEqual(market["history_sample_count"], 89)
        self.assertEqual({p["sample_count"] for p in market["history"]}, {89})

    def test_stale_classification_excludes_only_explicit_risk_names(self):
        rows = [{"symbol": "sh600000", "name": "普通样本"}, {"symbol": "sh600112", "name": "*ST样本"}, {"symbol": "sz300038", "name": "样本退"}, {"symbol": "bj920001", "name": "范围外样本"}]
        self.assertEqual(eligible_sina_codes(rows), ["600000.SH"])

    def test_zero_limit_price_and_zero_board_count_rejected(self):
        for change in (lambda s: s["bars"][-1].update(up_limit=0), lambda s: s["limit"].update(status="U", consecutive=0)):
            data = copy.deepcopy(self.demo)
            change(data["stocks"][0])
            with self.assertRaises(ValueError):
                validate_dataset(data)

    def test_invalid_filter_bounds_rejected(self):
        for config in ({"min_turnover": 40, "max_turnover": 5}, {"min_score": math.nan}, {"style": "unknown"}):
            with self.assertRaises(ValueError):
                settings(config)

    def test_volume_units_are_checked_against_traded_value(self):
        self.assertEqual(share_volume(1000, 1000000, 9, 11), 100000)
        self.assertEqual(share_volume(100000, 1000000, 9, 11), 100000)
        with self.assertRaises(ValueError):
            share_volume(1000, 7000000, 9, 11)

    def test_undated_or_stale_limit_pool_cannot_create_boards(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            for data in ({"pool": []}, {"qdate": 20261021, "pool": []}):
                with patch("qsa.free_provider.get_json", return_value={"data": data}):
                    with self.assertRaises(ValueError):
                        get_limit_pool("2026-10-22", Path(tmp))

    def test_invalid_import_does_not_overwrite_good_snapshot(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            store = SnapshotStore(tmp)
            store.save(self.demo)
            bad = copy.deepcopy(self.demo)
            bad["stocks"][0]["bars"][-1]["close"] = -1
            with self.assertRaises(ValueError):
                store.save(bad)
            reloaded = SnapshotStore(tmp)
            self.assertGreater(reloaded.active["stocks"][0]["bars"][-1]["close"], 0)


if __name__ == "__main__":
    unittest.main()
