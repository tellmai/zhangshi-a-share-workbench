import tempfile
import unittest
from pathlib import Path

from qsa.execution import review
from qsa.ledger import Ledger
from qsa.repository import Repository


class ExecutionReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.repo=Repository(self.temp.name)
        self.ledger=Ledger(self.repo)

    def tearDown(self):
        self.temp.cleanup()

    def signal(self, key="signal-a", decision="2026-10-20T19:00:00+08:00"):
        self.repo.put("signals",key,{"id":key,"code":"600001.SH","decision_time":decision,
            "strategy_id":"mainline_pullback","strategy_version":"1.0.0","setup_passed":True,
            "budget":{"shares":100},"invalidation_price":9},immutable=True)

    def trade(self, account, side, price, day, qty=100, signal="signal-a", fees=5):
        return self.ledger.append_trade({"account":account,"code":"600001.SH","side":side,"qty":qty,
            "price":price,"executed_at":day+"T10:00:00+08:00","signal_id":signal,"fees":fees})

    def result(self):
        return review(self.repo,self.ledger,self.ledger.portfolio(asof="2026-10-24T18:00:00+08:00"))

    def test_aggregated_average_price_and_partial_fifo_preserve_cents(self):
        self.trade("manual","buy",10.005,"2026-10-21",200,None)
        p=self.ledger.portfolio(asof="2026-10-21T18:00:00+08:00")
        self.assertEqual(p["cash"],17994)
        self.trade("manual","sell",10.5,"2026-10-22",100,None,1)
        p=self.ledger.portfolio(asof="2026-10-22T18:00:00+08:00")
        self.assertEqual(p["positions"][0]["cost"],1003)
        self.assertEqual(p["closed_pnl"],46)
        self.trade("manual","sell",10.4,"2026-10-23",100,None,1)
        p=self.ledger.portfolio(asof="2026-10-24T18:00:00+08:00")
        self.assertEqual(p["cash"],20082)
        self.assertEqual(p["closed_pnl"],82)

    def test_exact_signal_day_and_direction_comparison_and_strategy_pnl(self):
        self.signal()
        self.trade("manual","buy",10.02,"2026-10-21")
        self.trade("shadow","buy",10,"2026-10-21")
        self.trade("manual","sell",10.4,"2026-10-22")
        self.trade("shadow","sell",10.5,"2026-10-22")
        r=self.result()
        self.assertEqual(r["comparable_groups"],2)
        buy=next(x for x in r["rows"] if x["side"]=="buy")
        sell=next(x for x in r["rows"] if x["side"]=="sell")
        self.assertEqual(buy["adverse_price_bps"],20)
        self.assertEqual(buy["adverse_price_amount"],2)
        self.assertEqual(sell["adverse_price_amount"],10)
        self.assertEqual(r["strategy_realized"][0]["pnl"],28)
        self.assertEqual(r["strategy_realized"][0]["strategy_version"],"1.0.0")

    def test_different_execution_days_do_not_become_price_slippage(self):
        self.signal()
        self.trade("manual","buy",10.5,"2026-10-22")
        self.trade("shadow","buy",10,"2026-10-21")
        row=self.result()["rows"][0]
        self.assertEqual(row["status"],"DIFFERENT_EXECUTION_DATE")
        self.assertIsNone(row["adverse_price_bps"])

    def test_early_or_unlinked_executions_remain_facts_without_false_comparison(self):
        self.signal(decision="2026-10-22T19:00:00+08:00")
        early=self.trade("manual","buy",10,"2026-10-21")
        self.trade("manual","sell",11,"2026-10-23")
        self.assertTrue(any("早于信号" in w for w in early["warnings"]))
        r=self.result()
        self.assertTrue(all(x["status"]=="BEFORE_SIGNAL" or x["status"]=="NO_SHADOW_RECORD" for x in r["rows"]))
        self.assertEqual(r["strategy_realized"][0]["strategy_id"],"BEFORE_SIGNAL")

    def test_mixed_buy_sources_are_not_arbitrarily_assigned_to_one_signal(self):
        self.signal("a");self.signal("b")
        self.trade("manual","buy",10,"2026-10-21",signal="a")
        self.trade("manual","buy",11,"2026-10-21",signal="b")
        self.trade("manual","sell",12,"2026-10-23",qty=200,signal=None)
        group=self.result()["strategy_realized"][0]
        self.assertEqual(group["strategy_id"],"MIXED_SOURCES")
        self.assertEqual(group["pnl"],285)

    def test_a_shadow_record_does_not_assert_an_actual_execution(self):
        self.signal()
        self.trade("shadow","buy",10,"2026-10-21")
        row=self.result()["rows"][0]
        self.assertEqual(row["status"],"NO_ACTUAL_RECORD")
        self.assertIsNone(row["adverse_price_amount"])

    def test_shadow_account_is_labeled_as_an_independent_ledger(self):
        self.signal()
        self.trade("shadow","buy",10,"2026-10-21")
        portfolio=self.ledger.portfolio(account="shadow",asof="2026-10-24T18:00:00+08:00")
        r=review(self.repo,self.ledger,portfolio)
        self.assertIsNone(r["reference_account"])
        self.assertEqual(r["rows"][0]["status"],"ACCOUNT_ONLY")


if __name__=="__main__":unittest.main()
