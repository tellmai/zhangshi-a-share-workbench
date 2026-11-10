"""Hand-checkable synthetic cases for causal signals and cash-account replay."""
import copy
import csv
import io
import json
import math
import tempfile
import threading
import unittest
from datetime import date,timedelta
from pathlib import Path
from unittest.mock import patch

from qsa.data import SnapshotStore
from qsa.quant import (adjustment_changed, config_settings, cost, evaluate, features,
                       fill_block, replay, scan)
from qsa.quant_service import QuantService, QuantStore


def toy():
    cursor=date(2026,6,1);days=[]
    while len(days)<80:
        if cursor.weekday()<5:days.append(cursor.isoformat())
        cursor+=timedelta(days=1)
    bars=[{"date":d,"open":10.,"high":11.,"low":9.,"close":10.,"pre_close":10.,
           "volume_shares":10000.,"amount_yuan":1e9,"turnover_rate":5.,"adj_factor":1.} for d in days]
    data={"schema_version":1,"meta":{"mode":"demo","source":"合成测试，非真实市场","trade_date":days[-1],
        "fetched_at":"2026-10-23T19:00:00+08:00","calendar":days,"currency":"CNY"},
        "stocks":[{"code":"DEMO001","name":"合成研究股","is_st":False,"bars":bars,"verification":{"status":"demo"}}],"sectors":[]}
    cfg=config_settings({"ma_alignment":False,"breakout_days":0,"min_bars":21,"min_volume_ratio":0,
        "min_return5":-50,"max_extension20":200,"min_amount":0,"close_position_min":0,
        "commission_bps":0,"minimum_commission":0,"stamp_bps":0,"transfer_bps":0,"slippage_bps":0,
        "max_positions":1,"holding_days":2,"stop_loss_pct":0,"take_profit_pct":0})
    return data,cfg


def price(bar,opening,close=None,low=None,high=None):
    bar.update(open=opening,close=opening if close is None else close,
               low=opening-.5 if low is None else low,high=opening+.5 if high is None else high)


class QuantEngineTests(unittest.TestCase):
    def test_validation_rejects_unknown_nonfinite_boolean_and_bad_bounds(self):
        for bad in ([],{"arbitrary_code":"exec"},{"capital":True},{"min_amount":math.nan},
                    {"max_positions":1.5},{"holding_days":0},{"min_turnover":20,"max_turnover":10},
                    {"slippage_bps":-1},{"ma_alignment":"false"}):
            with self.assertRaises(ValueError,msg=str(bad)):config_settings(bad)

    def test_breakout_and_volume_use_only_preceding_bars(self):
        d,c=toy();bars=d["stocks"][0]["bars"][:31]
        c.update(breakout_days=20)
        bars[-1].update(close=11.5,high=12.,volume_shares=30000.)
        f,error=features(bars,c)
        self.assertIsNone(error)
        self.assertTrue(f["breakout"])
        self.assertEqual(f["volume_ratio"],3.)
        # Including today's high in its own breakout window would fail this case.
        self.assertLess(f["close"],bars[-1]["high"])

    def test_future_price_change_cannot_change_past_curve_or_orders(self):
        d,c=toy();days=d["meta"]["calendar"][30:38]
        before=replay(d,c,days[0],days[-1])
        changed=copy.deepcopy(d)
        last=changed["stocks"][0]["bars"][37]
        last.update(close=14.,high=15.)
        after=replay(changed,c,days[0],days[-1])
        self.assertEqual(before["curve"][:-1],after["curve"][:-1])
        self.assertEqual(before["orders"],after["orders"])
        self.assertNotEqual(before["curve"][-1]["equity"],after["curve"][-1]["equity"])

    def test_close_signal_enters_only_next_session_open(self):
        d,c=toy();days=d["meta"]["calendar"][30:35]
        price(d["stocks"][0]["bars"][31],12.)
        result=replay(d,c,days[0],days[-1])
        first=result["orders"][0]
        self.assertEqual(first["date"],days[1])
        self.assertEqual(first["price"],12.)
        self.assertEqual(result["curve"][0]["equity"],c["capital"])
        self.assertEqual(first["shares"]%100,0)

    def test_t_plus_one_blocks_same_day_stop_and_gap_exits_at_open(self):
        d,c=toy();c.update(holding_days=30,stop_loss_pct=5)
        days=d["meta"]["calendar"][30:33]
        price(d["stocks"][0]["bars"][31],10.,close=6.,low=5.,high=11.)
        price(d["stocks"][0]["bars"][32],8.)
        result=replay(d,c,days[0],days[-1]);t=result["trades"][0]
        self.assertEqual(t["entry_date"],days[1]);self.assertEqual(t["exit_date"],days[2])
        self.assertEqual(t["exit_price"],8.)
        self.assertEqual(t["pnl"],-20000.)
        self.assertEqual(result["metrics"]["total_return_pct"],-20.)

    def test_same_bar_stop_and_profit_uses_adverse_stop(self):
        d,c=toy();c.update(holding_days=30,stop_loss_pct=10,take_profit_pct=10)
        days=d["meta"]["calendar"][30:33]
        price(d["stocks"][0]["bars"][32],10.,low=8.,high=12.)
        t=replay(d,c,days[0],days[-1])["trades"][0]
        self.assertEqual(t["exit_price"],9.)
        self.assertIn("止损",t["reason"])

    def test_lot_rounding_and_costs_cannot_overdraw_cash(self):
        d,c=toy();c.update(capital=10000,commission_bps=3,minimum_commission=5,transfer_bps=.1,slippage_bps=5)
        days=d["meta"]["calendar"][30:33]
        r=replay(d,c,days[0],days[-1])
        self.assertEqual(r["orders"][0]["shares"],900)
        self.assertTrue(all(p["cash"]>=0 for p in r["curve"]))
        self.assertAlmostEqual(r["curve"][1]["cash"],990.41,places=2)
        self.assertGreater(r["metrics"]["total_fees"],0)

    def test_commission_floor_sell_only_stamp_and_separate_slippage(self):
        _,c=toy();c.update(commission_bps=3,minimum_commission=5,stamp_bps=5,transfer_bps=.1,slippage_bps=5)
        buy,sell=cost(1000,"buy",c),cost(1000,"sell",c)
        self.assertEqual(buy["commission"],5.)
        self.assertEqual(buy["stamp"],0)
        self.assertEqual(sell["stamp"],.5)
        self.assertAlmostEqual(sum(buy.values()),5.51)
        self.assertAlmostEqual(sum(sell.values()),6.01)

    def test_limit_down_delays_expiry_exit(self):
        d,c=toy();c.update(holding_days=1)
        days=d["meta"]["calendar"][30:34]
        locked=d["stocks"][0]["bars"][32]
        price(locked,9.,low=9.,high=9.)
        locked.update(up_limit=11.,down_limit=9.)
        price(d["stocks"][0]["bars"][33],9.5)
        r=replay(d,c,days[0],days[-1])
        self.assertEqual(r["trades"][0]["exit_date"],days[3])
        self.assertEqual(r["trades"][0]["exit_price"],9.5)
        self.assertIn("跌停价卖出不假设能够成交",r["skips"])

    def test_limit_up_flat_unknown_and_strict_missing_limits_skip_entry(self):
        d,c=toy();bar=copy.deepcopy(d["stocks"][0]["bars"][31])
        bar.update(open=11.,up_limit=11.,down_limit=9.)
        self.assertIsNotNone(fill_block(bar,"buy"))
        bar.update(open=10.,high=10.,low=10.,close=10.,up_limit=None,down_limit=None)
        self.assertIsNotNone(fill_block(bar,"buy"))
        c["strict_limits"]=True
        days=d["meta"]["calendar"][30:35]
        r=replay(d,c,days[0],days[-1])
        self.assertFalse(r["orders"]);self.assertIsNone(r["metrics"]["win_rate_pct"])

    def test_end_of_period_does_not_force_liquidation_or_invent_win_rate(self):
        d,c=toy();c["holding_days"]=30;days=d["meta"]["calendar"][30:33]
        r=replay(d,c,days[0],days[-1])
        self.assertFalse(r["trades"]);self.assertEqual(len(r["open_positions"]),1)
        self.assertIsNone(r["metrics"]["win_rate_pct"])

    def test_unexplained_corporate_action_stops_instead_of_fabricating_cash(self):
        d,c=toy();days=d["meta"]["calendar"][30:34]
        d["stocks"][0]["bars"][32]["adj_factor"]=1.2
        r=replay(d,c,days[0],days[-1])
        self.assertEqual(r["status"],"incomplete");self.assertIsNone(r["metrics"])
        self.assertIn("复权因子跳变",r["failure"])
        self.assertEqual(r["computed_through"],days[1])
        a=d["stocks"][0]["bars"][30];b={**a,"adj_factor":1.0002}
        self.assertFalse(adjustment_changed(a,b))

    def test_missing_daily_sector_or_relay_data_cannot_be_backtested(self):
        d,c=toy();days=d["meta"]["calendar"][30:34]
        for extra in ({"min_boards":2},{"sector_min":40},{"ranking":"relay"}):
            with self.assertRaises(ValueError):replay(d,{**c,**extra},days[0],days[-1])

    def test_live_scan_rejects_stale_reference_date_and_short_history(self):
        d,c=toy();s=d["stocks"][0];d["meta"]["mode"]="import";s["code"]="600838.SH"
        s["verification"]={"status":"matched","date":"2020-01-01"}
        self.assertFalse(scan(d,c)["signals"])
        s["verification"]["date"]=d["meta"]["trade_date"]
        self.assertEqual(len(scan(d,c)["signals"]),1)
        s["bars"]=s["bars"][-1:]
        self.assertFalse(scan(d,c)["signals"])


class QuantStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.snapshots=SnapshotStore(Path(self.temp.name))
        self.data,self.cfg=toy();self.snapshots.demo=self.data
        self.service=QuantService(self.snapshots);self.store=self.service.store

    def tearDown(self):
        worker=getattr(self.service,"worker",None)
        if worker and worker.is_alive():self.service.cancel.set();worker.join(5)
        self.temp.cleanup()

    def finish(self,payload,replay_id=None):
        self.service.start(payload,replay_id=replay_id)
        self.service.worker.join(5)
        self.assertFalse(self.service.worker.is_alive())
        return self.store.read_run(self.service.job["result_id"])

    def test_strategy_versions_preserve_old_parameters(self):
        first=self.store.save_strategy({"name":"测试规则","config":self.cfg})
        newer=self.store.save_strategy({"id":first["id"],"name":"测试规则","config":{**self.cfg,"holding_days":4}})
        self.assertEqual(len(newer["versions"]),2)
        self.assertEqual(newer["versions"][0]["config"]["holding_days"],2)
        self.assertEqual(newer["versions"][1]["config"]["holding_days"],4)
        with self.assertRaises(ValueError):self.service.start({"mode":"demo","strategy_id":first["id"],"strategy_version":1,"config":newer["versions"][1]["config"]})

    def test_frozen_input_reproduces_run_after_current_data_changes(self):
        days=self.data["meta"]["calendar"][30:36]
        first=self.finish({"type":"backtest","mode":"demo","config":self.cfg,"start":days[0],"end":days[-1]})
        self.assertEqual(first["status"],"completed")
        self.snapshots.demo["stocks"][0]["bars"][34]["close"]=20.
        second=self.finish({},replay_id=first["id"])
        self.assertNotEqual(first["id"],second["id"])
        self.assertEqual(first["input_digest"],second["input_digest"])
        self.assertEqual(first["curve"],second["curve"])
        self.assertEqual(first["trades"],second["trades"])
        self.assertEqual(second["replayed_from"],first["id"])

    def test_tampered_input_and_path_traversal_are_rejected(self):
        digest=self.store.freeze_input(self.data)
        (self.store.root/"inputs"/(digest+".json")).write_text('{}',encoding='utf-8')
        with self.assertRaises(ValueError):self.store.read_input(digest)
        with self.assertRaises(ValueError):self.store.read_run('../../active')

    def test_csv_exports_neutralize_formula_names(self):
        self.data["stocks"][0]["name"]="=1+1"
        r=self.finish({"type":"scan","mode":"demo","config":self.cfg})
        raw,mime=self.store.export(r["id"],"csv")
        rows=list(csv.reader(io.StringIO(raw.decode('utf-8-sig'))))
        self.assertEqual(rows[1][4],"'=1+1")
        self.assertTrue(mime.startswith('text/csv'))

    def test_cancel_is_recorded_without_erasing_previous_results(self):
        first=self.finish({"type":"scan","mode":"demo","config":self.cfg})
        entered=threading.Event()
        def wait_scan(dataset,cfg,progress,cancelled):
            entered.set()
            self.service.cancel.wait(3)
            raise InterruptedError('cancelled')
        with patch('qsa.quant_service.scan',side_effect=wait_scan):
            self.service.start({"type":"scan","mode":"demo","config":self.cfg})
            self.assertTrue(entered.wait(2));self.service.cancel_run();self.service.worker.join(5)
        self.assertEqual(self.service.job['state'],'cancelled')
        self.assertEqual(self.store.read_run(first['id'])['status'],'completed')
        self.assertEqual(self.store.history()[0]['status'],'cancelled')


if __name__=='__main__':unittest.main()
