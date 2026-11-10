"""Isolate optional SDK globals and stop hung public feeds with a parent timeout."""
import contextlib
import io
import json
import sys
import tempfile
from datetime import date,timedelta
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
if sys.version_info[:2] == (3,11) and (ROOT/'.vendor').exists():sys.path.insert(0,str(ROOT/'.vendor'))


def baostock_history(code,day):
    import baostock as bs
    symbol=code[-2:].lower()+'.'+code[:6]
    start=(date.fromisoformat(day)-timedelta(days=370)).isoformat()
    login=bs.login()
    if login.error_code!='0':raise ValueError('BaoStock服务登录失败：'+str(login.error_code))
    try:
        fields='date,code,open,high,low,close,preclose,volume,amount,turn,tradestatus,pctChg'
        series={}
        for flag in ('3','2'):
            rs=bs.query_history_k_data_plus(symbol,fields,start_date=start,end_date=day,frequency='d',adjustflag=flag)
            if rs.error_code!='0':raise ValueError('BaoStock查询失败：'+str(rs.error_code))
            rows=[]
            while rs.next():rows.append(dict(zip(rs.fields,rs.get_row_data())))
            series[flag]=rows
        qfq={r['date']:float(r['close']) for r in series['2'] if r['close'] and r['tradestatus']=='1'}
        bars=[]
        for r in series['3']:
            if r['tradestatus']!='1' or not r['close'] or r['date'] not in qfq:continue
            bars.append(dict(date=r['date'],open=float(r['open']),high=float(r['high']),low=float(r['low']),close=float(r['close']),
                pre_close=float(r['preclose']),volume_shares=float(r['volume']),amount_yuan=float(r['amount']),
                turnover_rate=float(r['turn']) if r['turn'] else None,adj_factor=qfq[r['date']]/float(r['close'])))
        return {'bars':bars,'raw':series,'source':'BaoStock','adjustment':'同源不复权与前复权收盘比值；不与其他源拼接'}
    finally:bs.logout()


def akshare_history(code,day):
    import akshare as ak
    start=(date.fromisoformat(day)-timedelta(days=370)).strftime('%Y%m%d')
    series={adjust:ak.stock_zh_a_hist(symbol=code[:6],period='daily',start_date=start,end_date=day.replace('-',''),adjust=adjust).to_dict('records') for adjust in ('','qfq')}
    qfq={str(r['日期']):float(r['收盘']) for r in series['qfq']}
    bars=[]
    for r in series['']:
        dt=str(r['日期']);close=float(r['收盘']);pct=float(r['涨跌幅'])
        if dt not in qfq or pct<=-100:continue
        bars.append(dict(date=dt,open=float(r['开盘']),high=float(r['最高']),low=float(r['最低']),close=close,
            pre_close=close/(1+pct/100),volume_shares=float(r['成交量'])*100,amount_yuan=float(r['成交额']),
            turnover_rate=float(r['换手率']),adj_factor=qfq[dt]/close))
    return {'bars':bars,'source':'AKShare/东方财富','adjustment':'同源不复权与前复权收盘比值；前收盘由供应商两位涨幅重建，仅作历史指标，不确认历史涨停'}


def work(task):
    provider,action=task['provider'],task['action'];code=task.get('code','600838.SH');day=task['date']
    if action=='runtime_check':
        import akshare,baostock,pandas,numpy
        return {'akshare':akshare.__version__,'baostock':str(getattr(baostock,'__version__','available')),
            'pandas':pandas.__version__,'numpy':numpy.__version__,'scope':'本机打包依赖检查，未请求行情'}
    if provider=='ifind':
        import iFinDPy as sdk
        result=sdk.THS_iFinDLogin(task['username'],task['password'])
        if result not in (0,-201):raise ValueError('iFinD登录未成功，返回码 '+str(result))
        if result==0:sdk.THS_iFinDLogout()
        return {'message':'官方SDK登录成功；未测试具体行情权限，未接入主行情。'+('沿用既有登录，未登出。' if result==-201 else '本次检测会话已登出。'),'login_code':result,'scope':'仅身份验证，不表示数据权限已开通'}
    if provider=='baostock' and action=='calendar':
        import baostock as bs
        login=bs.login()
        if login.error_code!='0':raise ValueError('BaoStock日历登录失败：'+str(login.error_code))
        try:
            result=bs.query_trade_dates(start_date=task['start'],end_date=task['end'])
            if result.error_code!='0':raise ValueError('BaoStock日历查询失败：'+str(result.error_code))
            rows=[]
            while result.next():rows.append(dict(zip(result.fields,result.get_row_data())))
            return {'rows':rows,'sessions':[r['calendar_date'] for r in rows if r['is_trading_day']=='1'],
                'source':'BaoStock交易日历','start':task['start'],'end':task['end']}
        finally:bs.logout()
    if provider in ('baostock','akshare'):
        return baostock_history(code,day) if provider=='baostock' else akshare_history(code,day)
    if provider=='ths':
        import akshare as ak
        symbol=task.get('symbol') or '半导体'
        frame=ak.stock_board_industry_index_ths(symbol=symbol,start_date=(date.fromisoformat(day)-timedelta(days=45)).strftime('%Y%m%d'),end_date=day.replace('-',''))
        rows=[{str(k):str(v) for k,v in r.items()} for r in frame.to_dict('records')]
        return {'rows':rows,'source':'AKShare/同花顺公开行业指数','symbol':symbol,'scope':'所选单一行业指数，仅独立研究，不是全市场排名'}
    from qsa.free_provider import tencent_quotes,sina_dated_reference,history
    with tempfile.TemporaryDirectory() as temp:
        evidence=Path(temp)
        if provider=='tencent':
            rows=tencent_quotes([code],day,evidence)
            if code not in rows:raise ValueError('未取得目标日收盘报价')
            return {'quote':rows[code],'scope':'单股带日期收盘字段测试'}
        if provider=='sina':
            rows=sina_dated_reference([code],day,evidence)
            if code not in rows:raise ValueError('未取得目标日参考收盘')
            return {'date':day,'close':rows[code],'scope':'仅参考收盘字段测试'}
        if provider=='eastmoney':return {'bars':history(code,day,evidence,evidence=evidence),'scope':'东财历史通道测试，不代表全量列表已可用'}
    raise ValueError('不支持的来源')


def main():
    # PyInstaller's isolated interpreter may ignore PYTHONIOENCODING on Windows.
    for stream in (sys.stdin,sys.stdout,sys.stderr):
        if stream and hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8')
    task=json.loads(sys.stdin.read())
    try:
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):result=work(task)
        print(json.dumps({'ok':True,'data':result},ensure_ascii=False,default=str,allow_nan=False))
    except Exception as error:
        message=('iFinD连接失败：'+type(error).__name__+'；请检查官方SDK、账号和接口权限') if task.get('provider')=='ifind' and not isinstance(error,ValueError) else str(error)[:230]
        if task.get('provider')=='ifind':
            for key in ('username','password'):
                if task.get(key):message=message.replace(task[key],'[已隐藏]')
        print(json.dumps({'ok':False,'error':message},ensure_ascii=False))


if __name__=='__main__':main()
