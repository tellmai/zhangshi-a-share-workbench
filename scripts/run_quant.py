"""Run and archive a local strategy, using precisely the same page engine."""
import argparse
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from qsa.data import SnapshotStore
from qsa.quant import PRESETS,config_settings
from qsa.quant_service import QuantService


def main():
    parser=argparse.ArgumentParser(description="本地策略研究：当日筛选与历史样本回放")
    parser.add_argument('--type',choices=['scan','backtest','stock_backtest'],default='scan')
    parser.add_argument('--codes',nargs='+',help='指定一只或多只股票的完整代码、六位代码或完整名称')
    parser.add_argument('--auto-history',action='store_true',help='运行前自动补齐所选股票历史（默认）；冻结输入复现时不再联网')
    parser.add_argument('--cached-only',action='store_false',dest='auto_history',help='明确只使用本机缓存，不请求行情接口')
    parser.set_defaults(auto_history=True)
    parser.add_argument('--template',choices=list(PRESETS),default='trend')
    parser.add_argument('--mode',choices=['active','demo'],default='active')
    parser.add_argument('--start');parser.add_argument('--end')
    parser.add_argument('--config',help='读取已导出的JSON参数文件')
    parser.add_argument('--save-name',help='保存为策略版本；同名且参数相同时复用版本')
    parser.add_argument('--replay-id',help='使用指定运行冻结的输入与参数复现')
    args=parser.parse_args()
    service=QuantService(SnapshotStore(ROOT/'data'))
    value=json.loads(Path(args.config).read_text(encoding='utf-8')) if args.config else {'template':args.template}
    cfg=config_settings(value.get('config',value))
    payload={'type':args.type,'mode':args.mode,'config':cfg,'start':args.start,'end':args.end,'codes':args.codes,'auto_history':args.auto_history}
    if args.save_name:
        item=next((s for s in service.store.strategies() if s['name']==args.save_name),None)
        if not item or item['versions'][-1]['config']!=cfg:
            item=service.store.save_strategy({'id':item['id'] if item else None,'name':args.save_name,'config':cfg})
        payload.update(strategy_id=item['id'],strategy_version=item['versions'][-1]['version'])
    service.start(payload,replay_id=args.replay_id)
    service.worker.join()
    result=service.store.read_run(service.job['result_id'])
    folder=service.store.run_folder(result['id'])
    (folder/'export.csv').write_bytes(service.store.export(result['id'],'csv')[0])
    print(json.dumps({k:result.get(k) for k in ['id','type','status','strategy_name','strategy_version','mode','trade_date','funnel','metrics','summary','failure','input_digest']},ensure_ascii=False))
    print(str(folder))


if __name__=='__main__':main()
