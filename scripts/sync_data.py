"""Save a real after-close snapshot without keeping the web UI open."""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qsa.data import SnapshotStore
from qsa.free_provider import fetch_free


def main():
    parser = argparse.ArgumentParser(description="Free A-share after-close snapshot")
    parser.add_argument("--date", required=True, help="YYYY-MM-DD, latest verified close only")
    parser.add_argument("--industry-limit", type=int, default=12)
    parser.add_argument("--theme-limit", type=int, default=8)
    parser.add_argument("--history-limit", type=int, default=120)
    args = parser.parse_args()
    def progress(percent, message):
        print(f"[{percent:3d}%] {message}", flush=True)
    store = SnapshotStore(ROOT / "data")
    dataset = fetch_free(vars(args), store.root, progress)
    filename = store.save(dataset)
    print(f"Saved: data/snapshots/{filename}")
    print("Reload the web app to read this snapshot.")


if __name__ == "__main__":
    main()
