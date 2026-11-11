"""Fictitious, hand-checkable imported frames; never production market evidence."""
from datetime import date, timedelta

from qsa.contracts import timestamp


def frames(count=9):
    days, cursor = [], date(2026, 3, 2)
    while len(days) < 80 + count:
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    result = []
    for index in range(80, 80+count):
        day = days[index]
        stocks = []
        for number in range(5):
            code = f"60000{number+1}.SH"
            bars = []
            for i, dt in enumerate(days[:index+1]):
                close = round(10 + i*.02 + number*.1, 2)
                previous = round(close/1.1, 4) if number == 0 else round(close-.02, 2)
                bars.append({"date": dt, "open": round(close-.02, 2), "high": close,
                    "low": round(close-.04, 2), "close": close, "pre_close": previous,
                    "volume_shares": round(100000*1.03**i), "amount_yuan": 1e9, "turnover_rate": 5,
                    "adj_factor": 1, "up_limit": close if number == 0 else round(previous*1.1, 2),
                    "down_limit": round(previous*.9, 2), "provenance": {
                        "provider_id": "unit-test", "source_family": "synthetic", "adapter_version": "fixture-1",
                        "event_time": timestamp(dt+"T15:00:00+08:00"), "available_at": timestamp(dt+"T15:05:00+08:00"),
                        "known_at": timestamp(dt+"T15:05:00+08:00"), "ingested_at": timestamp(dt+"T15:05:00+08:00"),
                        "precision": "synthetic", "verified_fields": [], "evidence_digest": None}})
            stocks.append({"code": code, "name": "合成测试"+str(number), "exchange": "SH", "board": "主板", "board_id": "MAIN",
                "list_date": "2020-01-01", "is_st": False, "bars": bars,
                "limit": {"status": "U" if number == 0 else "N", "consecutive": 2 if number == 0 else 0},
                "verification": {"status": "matched", "date": day}, "announcements": [],
                "status_history": [{"valid_from": day, "valid_to": None, "known_at": day+"T08:00:00+08:00",
                    "is_st": False, "halted": False, "board": "MAIN"}]})
        sectors = [{"id": kind+"-test", "name": "合成分类", "type": kind, "classification": "unit-test",
            "members": [s["code"] for s in stocks], "asof_date": day, "complete": True,
            "valid_from": day, "known_at": day+"T08:00:00+08:00", "available_at": day+"T08:00:00+08:00"} for kind in ("industry", "theme")]
        dataset = {"schema_version": 2, "meta": {"trade_date": day, "source": "synthetic-test-evidence",
            "mode": "import", "synthetic": True, "calendar": days, "calendar_verified": True,
            "calendar_known_at": "2026-01-01T00:00:00+08:00", "calendar_source": "synthetic-test-calendar",
            "security_master_complete": True, "complete": True, "expected_count": 5, "currency": "CNY",
            "available_at": timestamp(day+"T17:00:00+08:00"), "fetched_at": day+"T17:00:00+08:00",
            "universe": "五只合成测试证券，不是真实行情"}, "stocks": stocks, "sectors": sectors}
        result.append({"trade_date": day, "decision_time": day+"T19:30:00+08:00", "dataset": dataset, "snapshot_id": "test-"+day})
    return result
