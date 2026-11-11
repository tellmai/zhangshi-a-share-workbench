import copy
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from qsa.archive import backup, migrate, restore, rotate_backups, verify_backup
from qsa.contracts import ResearchConfig, digest, load_config, timestamp
from qsa.data import SnapshotStore
from qsa.jobs import JobStore
from qsa.ledger import Ledger
from qsa.mainline import evaluate_day
from qsa.market_rules import dated_cost, trading_rule
from qsa.quality import assess, normalize
from qsa.repository import Conflict, Repository
from qsa.risk import budget
from qsa.strict_backtest import point_in_time_frame, replay
from qsa.warehouse import Warehouse
from v2_fixture import frames


class V2CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.root = Path(self.temp.name)
        self.repo = Repository(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_contracts_reject_unknown_nonfinite_and_naive_time(self):
        for value in ({"capital": True}, {"capital": float("nan")}, {"boards": []}, {"capital2": 100}, {"max_positions": 1.5}):
            with self.assertRaises(ValueError): ResearchConfig.model_validate(value)
        with self.assertRaises(ValueError): timestamp("2026-01-01T10:00:00")
        self.assertEqual(load_config("capital: 20000\nholding_days: 5")["max_positions"], 2)
        with self.assertRaises(Exception): load_config("!!python/object/apply:os.system ['echo wrong']")

    def test_default_configuration_has_a_stable_freeze_digest(self):
        default=ResearchConfig().model_dump()
        frozen=ResearchConfig.model_validate(default).model_dump()
        self.assertEqual(digest(default),digest(frozen))
        self.assertEqual(digest(load_config('{}')),digest(frozen))

    def test_numeric_normalization_keeps_original_values_and_multiplier(self):
        from qsa.free_provider import normalization_record
        converted = normalization_record(123, 45, 12300, 450000)
        self.assertEqual(converted["volume"]["raw_value"], 123)
        self.assertEqual(converted["volume"]["multiplier"], 100)
        self.assertEqual(converted["amount"]["multiplier"], 10000)

    def test_range_history_preserves_full_partition_and_late_knowledge(self):
        from qsa.providers import FreeProvider
        data=frames(1)[0]["dataset"]
        bars=data["stocks"][0]["bars"]
        provider=FreeProvider(self.repo)
        request={"code":"600001.SH","start":bars[-4]["date"],"end":bars[-1]["date"],"warmup":0}
        with patch.object(provider,"_range",return_value=(bars,{"synthetic":True})) as fetch:
            result=provider.get_history(request)
            self.assertEqual(result.status,"supported",result.metadata)
            again=provider.get_history(request)
            self.assertEqual(fetch.call_count,1)
        self.assertEqual(again.data,result.data)
        cache=self.repo.list("history_cache")[0]
        self.assertEqual(len(Warehouse(self.repo).bars(cache["parquet_id"])),len(bars))
        self.assertIsNone(result.metadata["historical_available_at"])

    def test_packaged_worker_has_an_explicit_non_gui_subcommand(self):
        from qsa.jobs import worker_command
        with patch("qsa.jobs.sys.frozen", True, create=True), patch("qsa.jobs.sys.executable", str(self.root/"Zhangshi.exe")):
            command = worker_command(self.root, "job-test")
        self.assertEqual(Path(command[0]).name, "ZhangshiWorker.exe")
        self.assertEqual(command[-3:], ["execute", "--job-id", "job-test"])

    def test_unknown_legacy_times_do_not_become_historical_knowledge(self):
        store = SnapshotStore(self.root)
        value = normalize(store.demo)
        self.assertIsNone(value["meta"]["available_at"])
        self.assertIsNone(value["stocks"][0]["bars"][0]["provenance"]["available_at"])
        self.assertEqual(assess(value)["status"], "PARTIAL")

    def test_immutable_artifact_rejects_tampering_and_versions_use_cas(self):
        key = self.repo.object({"x": 1})
        self.assertEqual(self.repo.read_object(key), {"x": 1})
        first = self.repo.put("notes", "a", {"text": "one"})
        self.repo.put("notes", "a", {"text": "two"}, expected=first["_revision"])
        with self.assertRaises(Conflict): self.repo.put("notes", "a", {"text": "bad"}, expected=1)
        self.assertEqual(len(self.repo.revisions("notes", "a")), 2)
        with self.repo.connect() as db: path = db.execute("SELECT path FROM artifacts WHERE digest=?", (key,)).fetchone()[0]
        self.repo.safe_path(path).write_bytes(b"broken")
        with self.assertRaises(Exception): self.repo.read_object(key)

    def test_snapshot_asof_ignores_later_correction(self):
        value = frames(1)[0]["dataset"]
        early = self.repo.register_snapshot(value)
        corrected = copy.deepcopy(value)
        corrected["meta"]["available_at"] = corrected["meta"]["trade_date"]+"T20:30:00+08:00"
        corrected["meta"]["correction"] = True
        self.repo.register_snapshot(corrected)
        got = self.repo.asof_snapshot(value["meta"]["trade_date"], value["meta"]["trade_date"]+"T19:30:00+08:00")
        self.assertNotIn("correction", got["meta"])
        self.assertEqual(digest(got), early["snapshot_id"])

    def test_parquet_roundtrip_and_hash_check(self):
        value = frames(1)[0]["dataset"]
        key = self.repo.register_snapshot(value)["snapshot_id"]
        warehouse = Warehouse(self.repo)
        record = warehouse.publish(value, key)
        rows = warehouse.bars(key, "600001.SH")
        self.assertEqual(len(rows), len(value["stocks"][0]["bars"]))
        self.assertEqual(float(rows[-1]["close"]), value["stocks"][0]["bars"][-1]["close"])
        self.assertEqual(warehouse.publish(value, key)["_revision"], 1)
        path = self.repo.safe_path(next(f for f in record["files"] if f["kind"] == "bars")["path"])
        path.write_bytes(b"tampered")
        with self.assertRaises(ValueError): warehouse.bars(key)

    def test_job_deduplication_single_owner_cancel_and_recovery(self):
        jobs = JobStore(self.repo)
        job = jobs.enqueue("scan", "2026-10-23", {"x": 1}, "frozen")
        self.assertEqual(jobs.enqueue("scan", "2026-10-23", {"x": 1}, "frozen")["id"], job["id"])
        owners = []
        threads = [threading.Thread(target=lambda: owners.append(jobs.claim(job["id"]))) for _ in range(4)]
        [t.start() for t in threads]; [t.join() for t in threads]
        self.assertEqual(sum(bool(x) for x in owners), 1)
        owner = next(x for x in owners if x)
        jobs.progress(job["id"], owner, 25, "first", {"cursor": 3})
        jobs.cancel(job["id"])
        with self.assertRaises(InterruptedError): jobs.progress(job["id"], owner, 30, "next")
        jobs.finish(job["id"], owner, "cancelled")
        jobs.retry(job["id"])
        owner = jobs.claim(job["id"])
        with self.repo.connect(write=True) as db: db.execute("UPDATE jobs SET heartbeat='1970-01-01T00:00:00.000000+00:00' WHERE id=?", (job["id"],))
        self.assertEqual(jobs.recover(), [job["id"]])
        self.assertEqual(jobs.get(job["id"])["checkpoint"]["cursor"], 3)
        with self.assertRaises(Conflict): jobs.finish(job["id"], owner, "completed")

    def test_dated_rules_and_fees(self):
        self.assertEqual(trading_rule("300001.SZ", "2020-08-23")["limit_ratio"], .1)
        self.assertEqual(trading_rule("300001.SZ", "2020-08-24")["limit_ratio"], .2)
        self.assertEqual(trading_rule("600001.SH", "2026-07-05", True)["limit_ratio"], .05)
        self.assertEqual(trading_rule("600001.SH", "2026-07-06", True)["limit_ratio"], .1)
        self.assertEqual(dated_cost(10000, "sell", "2023-08-27", {})["stamp"], 10)
        self.assertEqual(dated_cost(10000, "sell", "2023-08-28", {})["stamp"], 5)
        self.assertEqual(dated_cost(10000, "buy", "2023-08-28", {})["stamp"], 0)

    def test_budget_covers_fees_risk_and_portfolio_limits(self):
        b = budget("600001.SH", "2026-10-23", 12.5, 12.125)
        self.assertEqual(b["shares"], 200)
        self.assertLessEqual(b["planned_risk"], 100)
        self.assertEqual(budget("600001.SH", "2026-10-23", 50, 45)["shares"], 0)
        self.assertEqual(budget("600001.SH", "2026-10-23", 12.5, 12.125, regime="UNCERTAIN")["shares"], 0)
        self.assertEqual(budget("600001.SH", "2026-10-23", 10, 9.8, portfolio={"cash": 1000})["shares"], 0)
        self.assertEqual(budget("600001.SH", "2026-10-23", 12.5, 12, portfolio={"positions": [{"code": "600002.SH", "overlap_group": "g"}]}, overlap_group="g")["shares"], 0)

    def test_manual_facts_corrections_outside_plan_and_t1(self):
        ledger = Ledger(self.repo)
        row = ledger.append_trade({"code": "600001", "side": "buy", "qty": 100, "price": 10, "fees": 5,
            "executed_at": "2026-10-23T10:00:00+08:00", "receipt": "r1"})
        self.assertIn("系统外交易", row["warnings"])
        self.assertEqual(ledger.append_trade({"code": "600001", "side": "buy", "qty": 100, "price": 10, "fees": 5,
            "executed_at": "2026-10-23T10:00:00+08:00", "receipt": "r1"})["_revision"], 1)
        p = ledger.portfolio(asof="2026-10-23T14:00:00+08:00")
        self.assertEqual(p["positions"][0]["sellable_qty"], 0)
        self.assertEqual(p["cash"], 18995)
        changed = {k:v for k,v in row.items() if not k.startswith("_") and k not in ("warnings", "recorded_at", "exit_plan")}
        changed.update(price=11, expected_revision=1)
        ledger.append_trade(changed)
        self.assertEqual(ledger.portfolio(asof="2026-10-23T14:00:00+08:00")["cash"], 18895)
        self.assertEqual(len(self.repo.revisions("trades", row["id"])), 2)
        ledger.append_trade({"code": "600001", "side": "sell", "qty": 200, "price": 10,
            "executed_at": "2026-10-23T11:00:00+08:00"})
        self.assertTrue(ledger.portfolio()["incomplete"])

    def test_csv_validation_atomicity_and_idempotency(self):
        ledger = Ledger(self.repo)
        bad = ledger.preview_csv("code,side,qty,price,executed_at\n600001,buy,100,10,2026-10-23T10:00:00+08:00\n600002,buy,wrong,10,2026-10-23T10:00:00+08:00")
        with self.assertRaises(ValueError): ledger.commit_csv(bad["id"])
        self.assertEqual(ledger.trades(), [])
        good = ledger.preview_csv("code,side,qty,price,executed_at,invalidation_price\n600001,BUY,100,10,2026-10-23T10:00:00+08:00,9.5")
        self.assertEqual(ledger.commit_csv(good["id"])["added"], 1)
        self.assertEqual(ledger.commit_csv(good["id"])["added"], 0)
        self.assertEqual(ledger.trades()[0]["exit_plan"]["invalidation_price"], 9.5)

    def test_stale_marks_are_explicit_and_never_precede_entry(self):
        ledger = Ledger(self.repo)
        ledger.append_trade({"code":"600001.SH", "side":"buy", "qty":100, "price":10,
            "executed_at":"2026-10-23T10:00:00+08:00"})
        data = {"meta":{"trade_date":"2026-10-22", "calendar":[], "calendar_verified":False},
            "stocks":[{"code":"600001.SH", "bars":[{"date":"2026-10-22", "close":20}]}]}
        p = ledger.portfolio(data, asof="2026-10-23T16:00:00+08:00")
        self.assertEqual(p["equity"], 20000)
        self.assertIsNone(p["positions"][0]["mark_date"])
        self.assertIsNone(p["positions"][0]["holding_sessions"])
        data["meta"]["trade_date"] = data["stocks"][0]["bars"][0]["date"] = "2026-10-23"
        p = ledger.portfolio(data, asof="2026-10-26T16:00:00+08:00")
        self.assertEqual(p["equity"], 21000)
        self.assertIn("保留该日参考价格", p["issues"][0])

    def test_daily_close_status_cannot_drive_morning_entry(self):
        data = frames(4)
        for f in data:
            for stock in f["dataset"]["stocks"]:
                stock["status_history"][0]["known_at"] = f["trade_date"]+"T16:00:00+08:00"
        result = replay(data, template="trend")
        self.assertEqual(result["orders"], [])
        self.assertGreater(result["skips"].get("开盘前状态未知、风险警示或停牌", 0), 0)

    def test_known_suspension_preserves_position_but_unknown_gap_is_incomplete(self):
        data = frames(5)
        before = replay(data, template="trend")
        code = before["orders"][0]["code"]
        stock = next(s for s in data[2]["dataset"]["stocks"] if s["code"] == code)
        stock["bars"].pop()
        stock["status_history"][0]["halted"] = True
        result = replay(data, template="trend")
        self.assertEqual(result["status"], "completed", result.get("failure"))
        self.assertFalse(any(o["code"] == code and o["date"] == data[2]["trade_date"] for o in result["orders"]))
        stock["status_history"][0]["halted"] = False
        result = replay(data, template="trend")
        self.assertIsNone(result["metrics"])

    def test_mainline_three_distinct_days_and_fixed_scores(self):
        prior = []
        for frame in frames(3):
            report = evaluate_day(frame["dataset"], prior=prior, decision_time=frame["decision_time"], snapshot_id=frame["snapshot_id"])
            prior.append(report)
        self.assertFalse(prior[0]["themes"][0]["persistent"])
        self.assertTrue(prior[-1]["themes"][0]["persistent"])
        self.assertEqual(prior[-1]["market"]["state"], "STRONG")
        self.assertFalse(prior[-1]["weak_to_strong"]["enabled"])

    def test_leader_roles_distinguish_new_top_three_and_require_a_core_for_catch_up(self):
        sample=frames(3)
        current=evaluate_day(sample[-1]["dataset"], decision_time=sample[-1]["decision_time"])
        sector=next(t for t in current["themes"] if t["type"]=="theme")
        self.assertGreaterEqual(len(sector["leaders"]),3)
        first,second,third=[x["code"] for x in sector["leaders"][:3]]
        previous=[]
        for f in sample[:2]:
            previous.append({"context":{"trade_date":f["trade_date"],"decision_time":f["decision_time"]},
                "themes":[{**sector,"rank":1,"leaders":[{"code":first,"rank":1},{"code":second,"rank":4},{"code":third,"rank":2}]}]})
        result=evaluate_day(sample[-1]["dataset"], prior=previous, decision_time=sample[-1]["decision_time"])
        roles={x["code"]:x["role"] for t in result["themes"] if t["type"]=="theme" for x in t["leaders"]}
        self.assertEqual(roles[first],"CORE_LEADER")
        self.assertEqual(roles[second],"EMERGING_LEADER")
        self.assertEqual(roles[third],"CATCH_UP")
        for p in previous:p["themes"][0]["leaders"][0]["rank"]=4
        result=evaluate_day(sample[-1]["dataset"], prior=previous, decision_time=sample[-1]["decision_time"])
        roles={x["code"]:x["role"] for t in result["themes"] if t["type"]=="theme" for x in t["leaders"]}
        self.assertEqual(roles[first],"EMERGING_LEADER")
        self.assertEqual(roles[third],"FOLLOWER")

    def test_late_or_unknown_calendar_cannot_drive_past_replay_or_signal_expiry(self):
        sample=frames(4)
        sample[0]["dataset"]["meta"]["calendar_known_at"]=sample[0]["trade_date"]+"T21:00:00+08:00"
        result=replay(sample,template="trend")
        self.assertEqual(result["status"],"BLOCKED")
        self.assertEqual(result["orders"],[])
        data=sample[-1]["dataset"]
        data["meta"].pop("calendar_known_at")
        report=evaluate_day(data,decision_time=sample[-1]["decision_time"])
        self.assertEqual(report["market"]["state"],"UNCERTAIN")
        self.assertTrue(report["candidates"])
        self.assertTrue(all(c["valid_until"] is None for c in report["candidates"]))

    def test_strict_replay_next_day_cash_and_causality(self):
        data = frames(10)
        result = replay(data, template="trend")
        self.assertEqual(result["status"], "completed", result.get("failure"))
        self.assertTrue(result["orders"])
        first_buy = next(o for o in result["orders"] if o["side"] == "buy")
        self.assertEqual(first_buy["date"], data[1]["trade_date"])
        self.assertTrue(all(p["cash"] >= 0 for p in result["curve"]))
        changed = copy.deepcopy(data)
        changed[-1]["dataset"]["stocks"][1]["bars"][-1]["volume_shares"] *= 2
        other = replay(changed, template="trend")
        self.assertEqual(result["curve"][:-1], other["curve"][:-1])
        self.assertTrue(all(t["entry_date"] < t["exit_date"] for t in result["trades"]))
        loader_calls = []
        descriptors = [{"trade_date": f["trade_date"], "snapshot_id": i, "decision_time": f["decision_time"]} for i,f in enumerate(data)]
        streamed = replay(descriptors, template="trend", loader=lambda d: (loader_calls.append(d["snapshot_id"]) or data[d["snapshot_id"]]["dataset"]))
        self.assertEqual(streamed["curve"], result["curve"])

    def test_strict_limits_unknown_and_late_data_fail_closed(self):
        data = frames(4)
        data[1]["dataset"]["stocks"][1]["bars"][-1]["up_limit"] = None
        result = replay(data, template="trend")
        self.assertIsNone(result["metrics"])
        data = frames(4)
        data[1]["dataset"]["meta"]["available_at"] = data[1]["trade_date"]+"T21:00:00+08:00"
        with self.assertRaises(ValueError): point_in_time_frame(data[1]["dataset"], data[1]["decision_time"])

    def test_migration_and_backup_recovery_preserve_original(self):
        store = SnapshotStore(self.root)
        store.save(store.demo)
        before = (self.root/"active.json").read_bytes()
        first = migrate(self.repo)
        second = migrate(self.repo)
        self.assertGreater(first["imported"], 0)
        self.assertEqual(second["imported"], 0)
        self.assertEqual((self.root/"active.json").read_bytes(), before)
        self.repo.put("notes", "keep", {"value": 42})
        saved = backup(self.repo)
        self.assertTrue(verify_backup(saved["path"])["files"])
        destination = self.root/"restore"
        restore(saved["path"], destination)
        restored = Repository(destination)
        self.assertEqual(restored.get("notes", "keep")["value"], 42)
        self.assertEqual((destination/"active.json").read_bytes(), before)
        with self.assertRaises(ValueError): restore(saved["path"], self.root)

    def test_backup_retention_keeps_daily_and_earlier_distinct_weeks(self):
        from datetime import datetime, timedelta
        folder=self.repo.home/"backups";folder.mkdir()
        latest=datetime(2026,11,10,21,30)
        for i in range(49):
            for copy in range(2):
                p=folder/f"{i:02}-{copy}.zip";p.write_bytes(b"test restore point")
                stamp=(latest-timedelta(days=i,minutes=copy)).timestamp()
                os.utime(p,(stamp,stamp))
        result=rotate_backups(self.repo)
        self.assertEqual(result["kept"],11)
        kept={int(p.name.split("-")[0]) for p in folder.glob("*.zip")}
        self.assertTrue(set(range(7)).issubset(kept))
        self.assertEqual(len(kept-set(range(7))),4)


if __name__ == "__main__":
    unittest.main()
