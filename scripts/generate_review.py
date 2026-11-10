"""Generate a local review bundle. Does not publish, send messages or place orders."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from qsa.data import SnapshotStore, validate_dataset
from qsa.review_service import ReviewService


def main():
    parser = argparse.ArgumentParser(description="生成A股抖音收盘复盘草稿、来源记录和封面")
    parser.add_argument("--date", required=True)
    parser.add_argument("--offline", action="store_true", help="仅使用以前实际读取的同日来源缓存")
    parser.add_argument("--refresh", action="store_true", help="先更新真实行情")
    parser.add_argument("--source-url", action="append", default=[], help="读取额外的同日收盘文章，最多8个")
    args = parser.parse_args()
    from qsa.data import iso_date
    from qsa.review import trading_day
    day = iso_date(args.date)
    if trading_day(day) is not True:
        print(json.dumps({"status": "skipped", "reason": "休市或交易日历未核验"}, ensure_ascii=False))
        return
    snapshots = SnapshotStore(ROOT / "data")
    def refresh(day):
        from qsa.free_provider import fetch_free
        value = fetch_free({"date": day, "industry_limit": 12, "theme_limit": 8}, snapshots.root, lambda *_: None)
        validate_dataset(value)
        snapshots.save(value)
    service = ReviewService(snapshots, refresh)
    report = service.generate(day, refresh=args.refresh, offline=args.offline, extra_urls=args.source_url or None)
    print(json.dumps({"status": report["status"], "id": report.get("id"), "title": report.get("title"),
        "characters": report.get("char_count"), "sources": len(report["sources"]), "failure": report.get("failure"),
        "folder": str(service.store.root / report["id"]) if report.get("id") else None}, ensure_ascii=False))


if __name__ == "__main__":
    main()
