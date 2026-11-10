"""Session-specific editorial titles from facts already admitted by the review."""
from __future__ import annotations

import math


TITLE_POLICY_VERSION = "2.0"
INDEX_LABELS = {"上证指数":"沪指", "深证成指":"深成指", "创业板指":"创业板"}


def title_plan(indices, sectors, facts, recent=()):
    evidence = []
    moves = [(name, value['pct']) for name, value in indices.items()
        if name in INDEX_LABELS and type(value.get('pct')) in (int,float) and math.isfinite(value['pct'])]
    moves.sort(key=lambda item:(-abs(item[1]), item[0]))
    sector = sectors[0] if sectors else None
    name = sector['name'] if sector and len(sector['name'])<=8 else None

    def index_phrase(item):
        label,pct=INDEX_LABELS[item[0]],item[1]
        return f"{label}{'涨' if pct>0 else '跌'}{abs(pct):.2f}%" if pct else f"{label}收平"

    def index_evidence(item):
        evidence.append({'field':'index_return','name':item[0],'value':item[1],
            'scope':'两平台同日收盘核对','source':'复盘已接纳的指数来源'})

    positive = [m for m in moves if m[1]>.3]
    negative = [m for m in moves if m[1]<-.3]
    if positive and negative:
        anchor,contrast=index_phrase(positive[0]),index_phrase(negative[0])
        index_evidence(positive[0]);index_evidence(negative[0])
        lines = [(anchor,contrast),(contrast,anchor),
            (anchor,INDEX_LABELS[negative[0][0]]+'仍收跌'),
            (contrast,INDEX_LABELS[positive[0][0]]+'仍收涨'),
            (anchor,'指数分化，明日看什么？'),(contrast,'指数分化，强弱怎么变？')]
        angle,scope='指数分化','指数为同日收盘核对值'
        support=f"{positive[0][0]}与{negative[0][0]}收盘方向不同"
    elif moves:
        main=moves[0];anchor=index_phrase(main);index_evidence(main)
        if name:
            ending='能延续吗？'
            lines=[(anchor,name+ending),(anchor,name+'走强'),(name+'走强',anchor),
                (name+ending,anchor),(anchor,name+'延续待验'),(anchor,'强势方向还看'+name)]
            angle='指数与题材' if main[1]>=0 else '指数与强势样本'
            scope='指数已核对 · 题材仅已展开同分类样本'
            support=f"{name}成分等权 {sector['avg_pct']:+.2f}% · 非全市场排名"
        else:
            lines=[(anchor,'明日关键看什么？'),(anchor,'收盘之后看哪些信号？'),
                ('明日关键看什么？',anchor),(anchor,'强弱信号在哪里？'),
                (anchor,'下一交易日如何验证？'),('收盘之后看哪些信号？',anchor)]
            angle,scope='指数收盘','指数为同日收盘核对值'
            support='仅用已核验事实提问，不预设下一交易日涨跌'
    elif sector:
        label=sector['name']
        anchor=f"{label}样本涨{sector['avg_pct']:.2f}%"
        lines=[(anchor,'强势能延续吗？'),(anchor,'下一步看扩散'),
            ('强势能延续吗？',anchor),(anchor,'延续还需验证'),
            (anchor,'代表股反馈是关键'),('下一步看扩散',anchor)]
        angle,scope='强势板块样本','仅已展开同分类成分样本 · 非全市场排名'
        support=f"{label}样本上涨占比{sector['breadth']:.1f}%"
    elif 'turnover_yuan' in facts:
        value=facts['turnover_yuan']['value']/1e12
        anchor=f"沪深成交{value:.2f}万亿"
        lines=[(anchor,'收盘信号怎么看？'),(anchor,'强弱仍需哪些证据？'),
            ('收盘信号怎么看？',anchor),(anchor,'明日还看哪些信号？'),
            (anchor,'复盘先看真实成交'),('复盘先看真实成交',anchor)]
        angle,scope='成交额事实',facts['turnover_yuan']['scope']
        support='没有同口径历史对比，不判断放量或缩量'
        evidence.append({'field':'turnover_yuan',**facts['turnover_yuan']})
    else:
        value=facts['limit_up']['value']
        anchor=f"同口径涨停{value}只"
        lines=[(anchor,'接力还看哪些信号？'),(anchor,'延续需要什么证据？'),
            ('接力还看哪些信号？',anchor),(anchor,'下一交易日看反馈'),
            (anchor,'强弱仍需复核'),('下一交易日看反馈',anchor)]
        angle,scope='同口径涨停统计',facts['limit_up']['scope']
        support='统计范围详见核验来源，不与其他口径混算'
        evidence.append({'field':'limit_up',**facts['limit_up']})
    if sector and (name or not moves):
        evidence.append({'field':'sector_return','name':sector['name'],'value':sector['avg_pct'],
            'scope':'已展开同分类成分等权，非全市场排名','source':'local-snapshot',
            'available_count':sector['available_count'],'member_count':sector['member_count']})
    variants=[]
    for first,second in lines:
        text=first+'，'+second
        if text not in [v['title'] for v in variants]:
            variants.append({'title':text,'lines':[first,second]})
    recent=list(recent)[:5]
    used={r.get('title') for r in recent if r.get('title')}
    unused=[v for v in variants if v['title'] not in used]
    ordered=unused+[v for v in variants if v['title'] in used]
    chosen=ordered[0]
    note=f"已对比最近{len(recent)}次存档标题，去重保留当天事实" if recent else "没有可读取的历史标题，本次未做历史去重"
    if not unused:note+='；事实型候选均已使用，保留事实，不换成空泛标题'
    return {'version':TITLE_POLICY_VERSION,'angle':angle,'scope':scope,'support':support,
        'evidence':evidence,'options':ordered[:3],'selected_index':0,'dedup_note':note,
        'selected_title':chosen['title'],'selected_lines':chosen['lines']}
