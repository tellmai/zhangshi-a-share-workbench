"""Non-secret, local choices shared by UI, scheduled reviews and CLI sync."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

from .data import atomic_json

CATALOG = {
    'tencent':dict(name='腾讯行情',role='带日期报价 / 历史日线',access='免费公开接口',family='腾讯',module=None,docs='https://gu.qq.com/'),
    'sina':dict(name='新浪财经',role='参考收盘核对 / 板块分类',access='免费公开接口',family='新浪',module=None,docs='https://vip.stock.finance.sina.com.cn/mkt/'),
    'eastmoney':dict(name='东方财富',role='证券列表 / 板块 / 涨停池 / 历史',access='免费公开接口',family='东方财富',module=None,docs='https://quote.eastmoney.com/'),
    'baostock':dict(name='BaoStock',role='日线与复权备用',access='免费库，无需个人账号',family='BaoStock',module='baostock',docs='https://www.baostock.com/'),
    'akshare':dict(name='AKShare / 东财日线',role='历史日线的同源备用适配',access='免费开源库',family='东方财富',module='akshare',docs='https://akshare.akfamily.xyz/data/stock/stock.html'),
    'ths':dict(name='同花顺公开板块',role='独立板块指数研究，不混入现有分类榜',access='AKShare公开页面，无需个人登录',family='同花顺公开页面',module='akshare',docs='https://akshare.akfamily.xyz/data/stock/stock.html#id380'),
    'ifind':dict(name='同花顺 iFinD',role='官方数据接口账号登录检测',access='需官方SDK及账号权限，非默认免费',family='同花顺 iFinD',module='iFinDPy',docs='https://quantapi.51ifind.com/gwstatic/static/ds_web/quantapi-web/help-center/manual.html'),
}
HISTORY_IDS=('tencent','eastmoney','baostock','akshare')
DEFAULTS={'market_provider':'auto','market_fallback':True,'history_order':['tencent','eastmoney','baostock'], 'library_timeout':30}


def validate_settings(value):
    if not isinstance(value,dict) or set(value)-set(DEFAULTS):raise ValueError('数据源设置包含不支持的字段；不接收密码或Cookie')
    result={**copy.deepcopy(DEFAULTS),**value}
    if result['market_provider'] not in ('auto','eastmoney','sina'):raise ValueError('无效的行情通道')
    if type(result['market_fallback']) is not bool:raise ValueError('备用切换需要是开关')
    order=result['history_order']
    if not isinstance(order,list) or not order or any(type(x) is not str or x not in HISTORY_IDS for x in order) or len(set(order))!=len(order):raise ValueError('历史来源需至少选择一个，且不能重复')
    if type(result['library_timeout']) is not int or not 10<=result['library_timeout']<=60:raise ValueError('库调用超时需为10—60秒')
    return result


def read_settings(root):
    path=Path(root)/'sources'/'settings.json'
    return validate_settings(json.loads(path.read_text(encoding='utf-8')) if path.exists() else {})


def save_settings(root,value):
    settings=validate_settings(value)
    atomic_json(Path(root)/'sources'/'settings.json',settings)
    return settings


def installed(module):
    if not module:return True
    try:return importlib.util.find_spec(module) is not None
    except (ImportError,ValueError):return False
