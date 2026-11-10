"""Read-only diagnostics: saves evidence, never replaces the active snapshot."""
import argparse
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
if (ROOT/'.vendor').exists():sys.path.insert(0,str(ROOT/'.vendor'))
from qsa.data import SnapshotStore
from qsa.source_service import SourceService


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--provider',default='all');p.add_argument('--date');p.add_argument('--code',default='600838.SH');p.add_argument('--symbol',default='半导体')
    args=p.parse_args();service=SourceService(SnapshotStore(ROOT/'data'))
    service.start(vars(args));service.worker.join()
    for name,value in service.status()['health'].items():print(json.dumps({'provider':name,**value},ensure_ascii=False))
