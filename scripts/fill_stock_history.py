"""Fill one real stock's missing history using the same validated UI workflow."""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from qsa.data import SnapshotStore
from qsa.stock_history import fetch_history, merge_history, quote_identity


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--code", required=True)
    args = parser.parse_args()
    store = SnapshotStore(ROOT / "data")
    data = store.read()
    if data["meta"].get("mode") == "demo":
        raise ValueError("需要真实快照")
    identity = quote_identity(data, args.code)
    bars, source, evidence = fetch_history(data, args.code, store.root, lambda s: print(s, flush=True))
    latest = store.read()
    if quote_identity(latest, args.code) != identity:
        raise ValueError("快照已改变，请重新执行")
    enriched = merge_history(latest, args.code, bars, source, evidence)
    filename = store.save(enriched)
    print(f"Saved {args.code}: {len(bars)} sessions / {source} / {filename}")


if __name__ == "__main__":
    main()
