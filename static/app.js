'use strict';
const $ = (s, el=document) => el.querySelector(s);
const $$ = (s, el=document) => [...el.querySelectorAll(s)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, x => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));
const num = (n,d=2) => n == null || !Number.isFinite(Number(n)) ? '—' : Number(n).toLocaleString('zh-CN',{minimumFractionDigits:d,maximumFractionDigits:d});
const pct = (n,d=2) => n == null ? '—' : `${n>0?'+':''}${num(n,d)}%`;
const amount = n => n == null ? '—' : n>=1e12 ? `${num(n/1e12,2)}万亿` : `${num(n/1e8,2)}亿`;
const color = n => n>0?'red':n<0?'green':'';
const iconPaths = {
 grid:'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
 radar:'<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><path d="M12 3v9l6 6"/><circle cx="16" cy="8" r="1"/>',
 chart:'<path d="M3 3v18h18M6 15l5-5 4 3 6-8"/>',
 layers:'<path d="m12 3 10 5-10 5L2 8l10-5Zm-9 9 9 5 9-5M3 16l9 5 9-5"/>',
 data:'<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/>',
 arrow:'<path d="M5 12h14m-5-5 5 5-5 5"/>',
 download:'<path d="M12 3v12m-4-4 4 4 4-4M4 16v5h16v-5"/>',
 refresh:'<path d="M20 7v5h-5M4 17v-5h5M6 7a7 7 0 0 1 11-2l3 3M4 16l3 3a7 7 0 0 0 11-2"/>',
 calendar:'<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v5m10-5v5M3 11h18"/>',
 filter:'<path d="M3 6h18M6 12h12m-8 6h4"/><circle cx="8" cy="6" r="2" fill="currentColor"/><circle cx="15" cy="12" r="2" fill="currentColor"/>',
 spark:'<path d="m12 3 2.4 6.6L21 12l-6.6 2.4L12 21l-2.4-6.6L3 12l6.6-2.4L12 3Z"/>',
 shield:'<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z"/><path d="m8 12 3 3 5-6"/>',
 flag:'<path d="M5 22V3m0 1c5-4 9 4 14 0v10c-5 4-9-4-14 0"/>',
 empty:'<path d="M3 7h18v14H3zM7 7V3h10v4M3 13h6l2 3h2l2-3h6"/>',
 clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
 experiment:'<path d="M9 3h6M10 3v6l-6 10a1.3 1.3 0 0 0 1 2h14a1.3 1.3 0 0 0 1-2L14 9V3M8 14h8"/><path d="M9 18h2m3 0h1"/>'
};
const icon = (name, size=18) => `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${iconPaths[name]||iconPaths.chart}</svg>`;
const navs = [['overview','盘后总览','grid'],['workbench','主线龙头工作台','radar'],['shortline','短线复盘','clock'],['stocks','强势股精选','chart'],['sectors','板块雷达','radar'],['ladder','情绪梯队','layers'],['quant','策略实验室','experiment'],['portfolio','持仓与复盘','shield'],['tasks','任务与数据','clock'],['review','抖音复盘','spark'],['data','数据与规则','data']];
const styles = [['balanced','综合共振'],['relay','连板接力'],['trend','趋势短线']];
let saved = [];
try { saved = JSON.parse(localStorage.getItem('after-close-watch')||'[]'); if(!Array.isArray(saved)) saved=[]; } catch {}
const state = {analysis:null,status:null,mode:'active',view:'overview',role:'all',search:'',sector:null,sectorOrder:'daily',showExcluded:false,watchOnly:false,sort:'score',sortDirection:-1,saved,request:0,loading:false,feedback:null};
let toastTimer, pollTimer, syncClockTimer, lastFocus;
Object.assign(state,{syncStarting:false,syncPolling:false,syncConnectionError:null,syncDismissed:null,syncHandled:null});

function toast(message) { $('#toast').textContent=message; $('#toast').classList.add('show'); clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('#toast').classList.remove('show'),4300); }
async function api(path, body) { if(window.__ZHANGSHI_DESKTOP__){await window.desktopReady;const r=await window.pywebview.api.request(path,body===undefined?null:body);if(r.status>=400)throw new Error(r.data?.error?.message||r.data?.error||'操作失败');return r.data;}const response=await fetch(path, body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Workbench-Token':state.status?.csrf||''},body:JSON.stringify(body)}); const result=await response.json(); if(!response.ok) throw new Error(result.error?.message||result.error||'请求失败'); return result; }
function empty(title, note='可调整筛选门槛，或在“数据与规则”中查看数据缺失项。') { return `<div class="empty-state">${icon('empty')}<b>${esc(title)}</b>${esc(note)}</div>`; }
function safeURL(url){try{const u=new URL(url);return u.protocol==='https:'?esc(u.href):'#';}catch{return '#';}}
function navigate(view) { location.hash=view; }
function watchKey(code) { return `${state.analysis.meta.mode==='demo'?'demo':'real'}:${code}`; }
function isSaved(code) { return state.saved.includes(watchKey(code)); }
function toggleWatch(code) { const key=watchKey(code); state.saved=state.saved.includes(key)?state.saved.filter(x=>x!==key):[...state.saved,key]; localStorage.setItem('after-close-watch',JSON.stringify(state.saved)); if(typeof v2SyncWatch==='function'&&window.v2SharedReady)v2SyncWatch(key,state.saved.includes(key)); if(state.view==='shortline')render();else renderTable(); const b=$('#stock-drawer [data-watch]'); if(b){b.textContent=isSaved(code)?'已加入自选':'加入本机自选';} }

function analysisFeedback(message, kind='success') {
 state.feedback={message,kind};
 const node=$('#analysis-feedback');
 if(!node)return;
 node.hidden=false;
 node.className=`analysis-feedback ${kind}`;
 node.innerHTML=`${kind==='loading'?'<span class="spinner" aria-hidden="true"></span>':icon(kind==='error'?'flag':'shield',15)}<span>${esc(message)}</span>`;
}
function analysisBusy(busy) {
 state.loading=busy;
 $('#content').setAttribute('aria-busy',String(busy));
 $('#style-tabs').setAttribute('aria-busy',String(busy));
 $$('[data-style],[data-kind],#filter-button,#filter-panel button').forEach(button=>{button.disabled=busy;});
}
async function load(config, options={}) {
 const id=++state.request;
 analysisBusy(true);
 analysisFeedback(options.pending||'正在计算筛选结果，请稍候…','loading');
 try {
  const analysis=config?await api('/api/analyze',{mode:state.mode,config}):await api(`/api/analysis?mode=${state.mode}`);
  if(id!==state.request)return false;
  state.analysis=analysis;
  analysisBusy(false);
  const label=styles.find(x=>x[0]===analysis.config.style)[1];
  analysisFeedback(`${options.switched?'已切换到':'当前模式：'}${label} · ${analysis.candidates.length}只观察候选。${state.view==='sectors'?'各板块的候选数量与股票预览已更新。':''}`);
  render();
  return true;
 } catch(error){
  if(id!==state.request)return false;
  if(!state.analysis)$('#content').innerHTML=`<div class="retry-panel">读取失败：${esc(error.message)}<br><button class="button" data-action="reload">重新读取</button></div>`;
  analysisFeedback(`计算失败：${error.message}${state.analysis?'；已保留上一模式的结果。':''}`,'error');
  toast(error.message);
  return false;
 } finally { if(id===state.request)analysisBusy(false); }
}
async function changeStyle(style) {
 if(state.loading||!styles.some(x=>x[0]===style)||!state.analysis)return;
 const name=styles.find(x=>x[0]===style)[1];
 if(state.analysis.config.style===style){
  analysisFeedback(`当前已是${name} · ${state.analysis.candidates.length}只观察候选。${state.view==='sectors'?'可查看下方各板块的候选股票，或点击“查看全部候选”。':''}`);
  return;
 }
 state.showExcluded=false;
 await load({...state.analysis.config,style},{switched:true,pending:`正在切换到${name}，重新计算个股评分与候选池…`});
}
function render() {
 if(!state.analysis)return;
 const a=state.analysis,m=a.market;
 state.view=navs.some(n=>n[0]===location.hash.slice(1))?location.hash.slice(1):'overview';
 $('#navigation').innerHTML=navs.map(([key,label,ic])=>`<a class="nav-item ${state.view===key?'active':''}" href="#${key}" ${state.view===key?'aria-current="page"':''}>${icon(ic)}<span>${label}</span></a>`).join('');
 const names={overview:['把市场脉络，看得更清楚。','从市场情绪到板块共振，找到值得进一步研究的强势股。'],sectors:['先看主线，再看个股。','行业与题材分开比较；研究强度有依据，成分交叉有标注。'],stocks:['强势有迹可循。','综合、接力与趋势三种视角，逐项查看入选理由与风险。'],ladder:['从高度，看见市场温度。','连板、晋级与封板质量，均以可验证的同日数据为依据。'],data:['每个判断，都能找到依据。','日期、口径、来源与规则留痕；关键数据不足时暂停相应排名。']};
 names.review=['让收盘复盘，有据也有序。','从真实行情到抖音文稿，保留每个结论背后的证据。'];
 names.quant=['设计策略，再用数据检验。','从一个明确的交易想法开始，运行、复盘、迭代，逐步理解量化。'];
 names.shortline=['先读情绪，再找强势。','收盘看市场与主线，把次日要观察的股票和条件留下来。'];
 names.workbench=['主线与龙头，每一步有依据。','按历史时点保存研究结果，查看持续性、失效条件与风险预算。'];
 names.portfolio=['记录实际成交，核对执行偏差。','真实账本与影子账户独立，保留每次更正、费用和退出计划。'];
 names.tasks=['让数据积累，持续而可追溯。','管理独立后台任务、原始证据、质量、备份和恢复点。'];
 names.stocks=['找到强势股，留下观察重点。','先过数据与流动性门槛，再看短线形态、板块共振与次日确认。'];
 $('#crumb').textContent=navs.find(n=>n[0]===state.view)[1];$('#page-title').textContent=names[state.view][0];$('#page-subtitle').textContent=names[state.view][1];
 $('#date-label').innerHTML=`${icon('calendar',13)} ${esc(a.meta.trade_date)}`;
 $('#export-button').innerHTML=`${icon('download')} ${state.view==='quant'?'导出结果':state.view==='stocks'?'导出候选':state.view==='shortline'?'导出观察':'导出复盘'}`;
 renderSyncProgress();
 const demo=a.meta.mode==='demo';
 const badge=demo?'演示模式':a.meta.mode==='import'?'导入快照':'盘后快照';
 $('#data-banner').innerHTML=`<div class="data-banner ${demo?'':a.quality.conflicts?'error':'live'}"><p><strong>${esc(badge)}</strong>${demo?'全部证券、价格与板块均为合成数据，仅供体验。接入真实数据后才会生成实际候选。':`${esc(a.meta.trade_date)} · ${esc(a.meta.source)} · ${esc(a.meta.universe)}${a.quality.conflicts?` · ${a.quality.conflicts} 只报价冲突，已从候选池剔除`:''}`}</p><span class="banner-tag">${demo?'非真实行情':a.quality.matched?`${a.quality.matched}只参考收盘一致`:'单源待核验'}</span></div>`;
 $('#style-tabs').innerHTML=styles.map(([key,name])=>`<button data-style="${key}" class="${a.config.style===key?'active':''}" aria-pressed="${a.config.style===key}">${name}</button>`).join('');
 $('#scope-label').textContent=`${a.meta.mode==='demo'?'虚构样本':a.meta.sector_universe_complete===false?'已展开板块研究范围':'当前行情样本'} · ${m.active_count}只有效证券`;
 $('#filter-button').innerHTML=`${icon('filter')} 筛选规则`;
 $('.control-bar').hidden=['review','quant','workbench','portfolio','tasks'].includes(state.view);
 if(['review','quant','workbench','portfolio','tasks'].includes(state.view)){$('#filter-panel').hidden=true;$('#analysis-feedback').hidden=true;}
 renderFilters();
 const views={overview:overviewView,sectors:sectorsView,stocks:stocksView,ladder:ladderView,data:dataView};
 if(typeof shortlineView==='function')views.shortline=shortlineView;
 if(typeof reviewView==='function')views.review=reviewView;
 if(typeof quantView==='function')views.quant=quantView;
 if(typeof workbenchView==='function'){views.workbench=workbenchView;views.portfolio=portfolioV2View;views.tasks=tasksV2View;}
 $('#content').innerHTML=views[state.view]();
 renderTable();analysisBusy(state.loading);requestAnimationFrame(drawAll);
 if(state.view==='review'&&typeof ensureReviewLoaded==='function')ensureReviewLoaded();
 if(state.view==='quant'&&typeof ensureQuantLoaded==='function')ensureQuantLoaded();
 if(state.view==='data'&&typeof ensureSourcesLoaded==='function')ensureSourcesLoaded();
 if(['shortline','stocks'].includes(state.view)&&typeof paintShortline==='function')paintShortline();
 if(['workbench','portfolio','tasks'].includes(state.view)&&typeof ensureV2Loaded==='function')ensureV2Loaded();
}
function renderFilters(){const c=state.analysis.config;$('#filter-panel').innerHTML=`<label>最低成交额（亿元）<input id="f-amount" type="number" min="0" max="10000" step="0.5" value="${c.min_amount/1e8}"></label><label>换手率下限（%）<input id="f-min-turn" type="number" min="0" max="100" value="${c.min_turnover}"></label><label>换手率上限（%）<input id="f-max-turn" type="number" min="0" max="100" value="${c.max_turnover}"></label><label>综合评分下限<input id="f-score" type="number" min="0" max="100" value="${c.min_score}"></label><label>板块强度下限<input id="f-sector" type="number" min="0" max="100" value="${c.min_sector_score}"></label><button class="button primary" data-action="apply-filters">应用筛选</button><label class="checkbox"><input id="f-one-word" type="checkbox" ${c.include_one_word?'checked':''}> 包含一字板观察候选</label><span class="stat-label">ST/退市名称、上市时间与关键数据质量过滤始终启用。</span>`;}
function statCards(){const m=state.analysis.market;const bars=[12,18,14,21,20,27,23,29,22,30,25];return `<section class="stats-grid" aria-label="市场关键指标">${[
 ['样本成交额',amount(m.amount_yuan),'chart',`有效收盘样本 <b>${num(m.active_count,0)}只</b>`,''],
 ['收盘涨停 / 跌停',`${num(m.limit_up,0)} <small>/ ${num(m.limit_down,0)}</small>`,'flag',`${m.limit_up==null?'涨跌停状态不完整':'以实际涨跌停价或供应商榜单确认'}`,'red'],
 ['空间高度',`${num(m.height??m.confirmed_height,0)}<small>${m.height==null?'已知样本':'连板'}</small>`,'layers',`昨日连板晋级率 <b>${m.promotion_rate==null?'待核验':num(m.promotion_rate,1)+'%'}</b>`,'amber'],
 ['强势观察候选',`${state.analysis.candidates.length}<small>只</small>`,'radar',`当前模式 <b>${styles.find(x=>x[0]===state.analysis.config.style)[1]}</b>`,'teal']
 ].map(([title,value,ic,foot,cl],i)=>`<article class="stat-card"><div class="stat-title">${title}${icon(ic)}</div><div class="stat-value ${cl}">${value}</div><div class="stat-foot">${foot}</div><div class="mini-bars">${bars.slice(i,i+8).map(h=>`<i style="height:${h}px"></i>`).join('')}</div></article>`).join('')}</section>`;}
function sectorSwitch(){const c=state.analysis.config;return `<div class="switch-small"><button data-kind="industry" class="${c.sector_type==='industry'?'active':''}">行业</button><button data-kind="theme" class="${c.sector_type==='theme'?'active':''}">题材</button></div>`;}
function sectorRanking(){
 const a=state.analysis;
 if(state.sectorOrder==='strength'){
  const rows=a.sectors.filter(s=>s.score!=null&&s.avg_pct>0).slice().sort((x,y)=>y.score-x.score||y.avg_pct-x.avg_pct||x.id.localeCompare(y.id));
  return {rows:rows.map(s=>({...s,daily_pct:s.avg_pct,expanded:true})),basis:'综合强度优先 · 同日成分等权涨幅',scope:'仅已展开且关键数据完整的板块；未覆盖全市场',asof_date:a.meta.trade_date,complete_universe:false,available_count:a.sectors.filter(s=>s.score!=null).length,expanded_count:a.sectors.length};
 }
 return a.sector_leaders||{rows:a.sectors.filter(s=>s.score!=null&&s.avg_pct>0).map(s=>({...s,daily_pct:s.avg_pct,expanded:true})).sort((x,y)=>y.daily_pct-x.daily_pct||y.breadth-x.breadth||x.id.localeCompare(y.id)),basis:'同日成分等权涨幅',scope:'仅当前已展开的可比板块；未覆盖全市场',asof_date:a.meta.trade_date,complete_universe:false,available_count:a.sectors.filter(s=>s.score!=null).length,expanded_count:a.sectors.length};
}
function sectorOrderSwitch(){return `<div class="sector-rank-controls"><div class="switch-small rank-switch" role="group" aria-label="板块排序">${[['daily','当日领涨'],['strength','综合强度']].map(([key,label])=>`<button class="${state.sectorOrder===key?'active':''}" data-sector-order="${key}" aria-pressed="${state.sectorOrder===key}">${label}</button>`).join('')}</div>${sectorSwitch()}</div>`;}
function sectorRankCaption(r){const demo=state.analysis.meta.mode==='demo';return `${demo?'合成演示 · 非真实当日行情':'行情日 '+esc(r.asof_date||state.analysis.meta.trade_date)} · ${esc(r.basis)}<br>${r.complete_universe?`同一供应商完整分类 ${r.available_count}个，已展开${r.expanded_count}个；指数涨幅与成分等权口径分开。`:`可比${r.available_count}个 / 已展开${r.expanded_count}个 · ${esc(r.scope)}`} 仅展示上涨板块，不用下跌板块凑榜。`;}
function sectorClick(s){return s.expanded===false?`data-sector-unexpanded="${esc(s.id)}"`:`data-sector="${esc(s.id)}"`;}
function sectorEmpty(r){return r.available_count?empty('当前可比范围暂无上涨板块','保留空榜，等待新的有效收盘数据。'):empty('板块同日数据不足','请更新数据并检查成分覆盖；不使用旧日或无日期榜单补位。');}
function sectorDemoNote(){if(state.analysis.meta.mode!=='demo')return '';const real=state.status?.active_meta&&state.status.active_meta.mode!=='demo';return `<div class="sector-demo-note"><span>当前为合成演示，板块名称和涨幅不代表真实市场。</span>${real?'<button class="text-button" data-action="active">读取真实收盘 '+icon('arrow',12)+'</button>':'<button class="text-button" data-action="sync">更新真实数据 '+icon('arrow',12)+'</button>'}</div>`;}
function heatmap(){
 const ranking=sectorRanking(),ss=ranking.rows.slice(0,5);
 return `<section class="panel sector-leaders"><div class="panel-heading"><div><h2>当日强势板块 <span class="sub">DAILY SECTOR LEADERS</span></h2><p>${state.sectorOrder==='daily'?'按同日涨幅从高到低，优先看当天领涨方向':'按价格、广度和涨停密度的综合分排序'}</p></div>${sectorOrderSwitch()}</div>${sectorDemoNote()}${ss.length?`<div class="heatmap" data-count="${ss.length}">${ss.map((s,i)=>`<button class="heat-tile daily-leader" ${sectorClick(s)} aria-label="第${i+1}名 ${esc(s.name)} 涨幅${pct(s.daily_pct)}"><span class="heat-score">${state.sectorOrder==='daily'?'领涨 '+String(i+1).padStart(2,'0'):'强度 '+num(s.score,1)}</span><b>${esc(s.name)}</b><strong>${pct(s.daily_pct)}</strong><small>${s.expanded===false?'指数榜单 · 成分待展开':`上涨 ${num(s.breadth,0)}% · 涨停 ${num(s.limit_up,0)}只`}</small></button>`).join('')}</div>`:sectorEmpty(ranking)}<div class="panel-caption sector-ranking-caption">${sectorRankCaption(ranking)}</div></section>`;
}
function overviewView(){const a=state.analysis,m=a.market;return `${typeof shortEntryHTML==='function'?shortEntryHTML():''}${statCards()}<div class="dashboard-grid"><div class="main-column">${heatmap()}${stockPanel('今日强势观察池',true)}</div><div class="side-column"><section class="panel insight-panel"><div class="insight-top"><span class="insight-icon">${icon('spark')}</span><div><b>盘后研究笔记</b><small>RESEARCH BRIEF</small></div></div><div class="insight-content"><p>${esc(a.narrative[0])}</p><div class="insight-row"><span>${esc(m.phase)}</span>上涨广度 ${num(m.breadth,1)}%</div></div></section><section class="panel"><div class="panel-heading"><h2>近20日固定样本广度</h2><span class="tag">上涨占比</span></div><div class="chart-frame"><canvas id="breadth-chart" role="img" aria-label="近20个样本交易日上涨占比折线图"></canvas></div><div class="chart-legend"><span><i></i>上涨占比</span><span>50% 为图中参考线</span></div><div class="panel-caption">固定已补齐样本 ${m.history_sample_count??0} 只；历史ST状态未还原。</div></section><section class="panel"><div class="panel-heading"><h2>连板梯队</h2><button class="text-button" data-nav="ladder">查看梯队 ${icon('arrow',12)}</button></div>${miniLadder()}</section>${qualityPanel()}</div></div>`;}
function miniLadder(){const ls=state.analysis.market.ladder.slice(0,4);return ls.length?`<div class="ladder-mini">${ls.map(t=>`<div class="ladder-row"><span class="ladder-height">${t.height}板</span><span class="ladder-bar" style="width:${Math.min(110,23+t.count*18)}px">${t.count}只</span><span class="ladder-names">${esc(t.stocks.map(x=>x.name).join(' · '))}</span></div>`).join('')}</div>`:empty('高度待核验','连板信息不完整，暂不展示完整天梯。');}
function qualityPanel(){const q=state.analysis.quality;return `<section class="panel"><div class="panel-heading"><h2>${icon('shield',15)} 数据状态</h2><span class="tag ${q.demo?'warm':q.conflicts?'danger':'mint'}">${q.demo?'合成演示':q.conflicts?'存在冲突':'检查记录'}</span></div><div class="quality-meter">${[0,1,2,3,4,5,6,7].map((_,i)=>`<i class="${!q.demo&&i<Math.round(8*q.matched/q.actual_count)?'on':''}"></i>`).join('')}</div><div class="quality-details"><div class="quality-item"><span>参考收盘价一致</span><b>${q.demo?'不适用':q.matched+' / '+q.actual_count}</b></div><div class="quality-item"><span>复权 / 20日历史缺失</span><b>${q.missing_adjustment}只</b></div><div class="quality-item"><span>公告检查</span><b>尚未接入独立核验</b></div></div></section>`;}
function stockPanel(title,compact=false){return `<section class="panel"><div class="panel-heading"><h2>${esc(title)} <span class="inline-count">${state.analysis.candidates.length}</span></h2>${compact?'<button class="text-button" data-nav="stocks">全部候选 '+icon('arrow',12)+'</button>':'<button class="text-button" data-action="csv">导出当前列表 '+icon('download',12)+'</button>'}</div><div class="table-toolbar"><div class="pills">${[['all','全部'],['首板观察','首板'],['连板观察','连板'],['趋势','趋势']].map(([k,v])=>`<button class="pill ${state.role===k?'active':''}" data-role="${k}">${v}</button>`).join('')}${!compact?`<button class="pill ${state.watchOnly?'active':''}" data-action="watch-only">自选</button>`:''}</div><input class="search-input" id="stock-search" type="search" placeholder="搜索股票 / 代码 / 板块" aria-label="搜索股票" value="${esc(state.search)}"></div>${state.sector?`<div class="sector-selected" style="padding:0 20px">筛选板块：${esc(state.analysis.sectors.find(s=>s.id===state.sector)?.name||state.sector)} <button class="text-button" data-action="clear-sector">清除 ×</button></div>`:''}<div class="table-wrap"><table><thead><tr><th>#</th><th>股票 / 代码</th><th>所属板块</th><th><button class="text-button" data-sort="pct_chg">涨跌幅 ↕</button></th><th><button class="text-button" data-sort="amount">成交额 ↕</button></th><th>换手率</th><th>短线地位</th><th><button class="text-button" data-sort="score">评分 ↕</button></th><th>自选</th></tr></thead><tbody id="stock-rows" data-compact="${compact}"></tbody></table><div id="stock-empty"></div></div><div class="table-footer"><span id="table-count"></span><span>点击股票查看K线、逐项评分和风险</span></div></section>`;}
function filteredStocks(){let rows=state.analysis.stocks.filter(s=>state.showExcluded?!s.selected:s.selected);if(state.sector){const sector=state.analysis.sectors.find(s=>s.id===state.sector);rows=rows.filter(s=>sector?.members.includes(s.code));}if(state.role!=='all')rows=rows.filter(s=>state.role==='趋势'?s.role?.startsWith('趋势'):s.role===state.role);if(state.watchOnly)rows=rows.filter(s=>isSaved(s.code));const search=state.search.trim().toLowerCase();if(search)rows=rows.filter(s=>`${s.name} ${s.code} ${s.sector_names.join(' ')}`.toLowerCase().includes(search));if(state.view==='stocks'&&!state.showExcluded&&typeof shortFilterRows==='function')rows=shortFilterRows(rows);const key=state.sort;return rows.sort((a,b)=>{const av=key==='amount'?a.current?.amount_yuan:a[key],bv=key==='amount'?b.current?.amount_yuan:b[key];if(av==null)return bv==null?0:1;if(bv==null)return -1;return (av-bv)*state.sortDirection;});}
function renderTable(){if(state.view==='stocks'&&typeof paintStrongResults==='function')paintStrongResults();const tbody=$('#stock-rows');if(!tbody||!state.analysis)return;const all=filteredStocks(),compact=tbody.dataset.compact==='true',rows=all.slice(0,compact?8:150);tbody.innerHTML=rows.map((s,i)=>`<tr><td><span class="rank-number ${i<3?'top':''}">${String(i+1).padStart(2,'0')}</span></td><td class="stock-cell" data-stock="${esc(s.code)}" tabindex="0" role="button" aria-label="查看${esc(s.name)}详情"><b>${esc(s.name)}</b><small>${esc(s.code)}</small></td><td><span class="tag">${esc(s.primary_sector||'未分类')}</span></td><td class="${color(s.pct_chg)}">${pct(s.pct_chg)}</td><td>${amount(s.current?.amount_yuan)}</td><td>${num(s.current?.turnover_rate,1)}%</td><td><span class="tag ${s.role==='连板观察'?'warm':s.role==='首板观察'?'danger':'mint'}">${s.consecutive>0?s.consecutive+'板 · ':''}${esc(s.role)}</span></td><td><span class="score-badge"><i></i>${num(s.score,1)}</span></td><td><button class="stock-watch ${isSaved(s.code)?'saved':''}" data-watch="${esc(s.code)}" aria-label="${isSaved(s.code)?'取消自选':'加入自选'}${esc(s.name)}" aria-pressed="${isSaved(s.code)}">${isSaved(s.code)?'★':'☆'}</button></td></tr>`).join('');$('#stock-empty').innerHTML=rows.length?'':empty('当前条件下没有候选','可调整风格、板块或筛选阈值；数据不足的股票不会被强行补入。');$('#table-count').textContent=`显示 ${rows.length} / ${all.length} 只${state.analysis.meta.mode==='demo'?' · 合成数据':''}`;$$('[data-role]').forEach(b=>b.classList.toggle('active',b.dataset.role===state.role));}
function sectorCandidates(sector){
 const members=new Set(sector.members);
 return state.analysis.stocks.filter(stock=>stock.selected&&members.has(stock.code));
}
function sectorCandidatePreview(sector){
 if(sector.expanded===false)return `<div class="sector-selection"><p class="sector-no-candidate">已进入同日领涨榜，成分尚未展开，暂无个股筛选结果。</p></div><button class="button small" data-sector-unexpanded="${esc(sector.id)}">查看数据说明 ${icon('arrow',12)}</button>`;
 const rows=sectorCandidates(sector);
 const label=styles.find(x=>x[0]===state.analysis.config.style)[1];
 return `<div class="sector-selection"><div class="sector-selection-heading"><span>${label}候选</span><b>${rows.length}<small>只</small></b></div>${rows.length?rows.slice(0,3).map(stock=>`<button class="sector-stock-preview" data-stock="${esc(stock.code)}" aria-label="查看${esc(stock.name)}详情"><span>${esc(stock.name)}</span><small>${esc(stock.role)}</small><b>${num(stock.score,1)}</b>${icon('arrow',11)}</button>`).join(''):'<p class="sector-no-candidate">当前模式与门槛下暂无候选</p>'}</div><button class="button small" data-sector="${esc(sector.id)}">查看板块候选（${rows.length}） ${icon('arrow',12)}</button>`;
}
function sectorsView(){
 const a=state.analysis,ranking=sectorRanking(),ranked=ranking.rows;
 const sectors=a.sectors.slice().sort((x,y)=>state.sectorOrder==='strength'?(y.score??-1)-(x.score??-1):Number(y.complete)-Number(x.complete)||(y.avg_pct??-Infinity)-(x.avg_pct??-Infinity)||x.id.localeCompare(y.id));
 const label=styles.find(s=>s[0]===a.config.style)[1];
 const descriptions={balanced:'兼顾板块共振、价格量能、趋势与短线地位',relay:'提高连板地位权重，优先观察接力候选',trend:'提高均线与突破结构权重，优先观察趋势候选'};
 return `<section class="mode-result" aria-label="当前模式选股结果"><div><span class="eyebrow">当前筛选结果</span><h2>${label}<span> · </span><strong>${a.candidates.length}</strong>只观察候选</h2><p>${descriptions[a.config.style]}。切换模式会更新候选数量、个股评分与下方股票预览；板块行情强度采用同一口径。</p></div><button class="button primary small" data-action="mode-candidates">查看全部候选 ${icon('arrow',13)}</button></section>
 <div class="view-info">${sectorRankCaption(ranking)}<br>行业与题材独立排名；各题材候选可能交叉，全池按股票代码去重。</div>
 <div class="panel-heading sector-ranking-heading" style="padding:0 0 16px"><h2>当日强势板块与${label}候选 <span class="sub">SECTOR & STOCK SELECTION</span></h2>${sectorOrderSwitch()}</div>
 <div class="sector-cards">${ranked.slice(0,6).map((s,i)=>`<article class="sector-card" data-sector-card="${esc(s.id)}"><span class="sector-num">${String(i+1).padStart(2,'0')}</span><h3>${esc(s.name)}</h3><span class="stat-label">${esc(s.classification)}</span><div class="sector-return ${color(s.daily_pct)}">${pct(s.daily_pct)}</div><span class="tag mint">${s.score==null?'综合强度待核验':'板块强度 '+num(s.score,1)+' / 100'}</span><div class="sector-metrics"><div><small>上涨占比</small><b>${num(s.breadth,1)}%</b></div><div><small>收盘涨停</small><b>${num(s.limit_up,0)}只</b></div><div><small>有效成分</small><b>${s.expanded===false?'待展开':s.available_count+'/'+s.member_count}</b></div></div>${sectorCandidatePreview(s)}</article>`).join('')||sectorEmpty(ranking)}</div>
 <section class="panel"><div class="panel-heading"><h2>板块完整度与排序依据</h2><span class="tag">${sectors.length}个已展开板块</span></div><div class="table-wrap"><table><thead><tr><th>板块</th><th>等权涨幅</th><th>上涨占比</th><th>涨停家数</th><th>样本成交额</th><th>覆盖率</th><th>板块强度</th><th>${label}候选</th></tr></thead><tbody>${sectors.map(s=>`<tr><td><b>${esc(s.name)}</b><br><small class="stat-label">${esc(s.incomplete_reason||s.classification)}</small></td><td class="${color(s.avg_pct)}">${pct(s.avg_pct)}</td><td>${num(s.breadth,1)}%</td><td>${num(s.limit_up,0)}</td><td>${amount(s.amount_yuan)}</td><td>${num(s.coverage,1)}%</td><td>${num(s.score,1)}</td><td><button class="text-button sector-count-button" data-sector="${esc(s.id)}">${sectorCandidates(s).length}只 ${icon('arrow',11)}</button></td></tr>`).join('')}</tbody></table></div><p class="note-block">强度 = 35% 价格表现 + 35% 上涨广度 + 30% 涨停密度。少于5只有效成分、成分不完整或涨停状态缺失时不评分。可比成交额倍数作为补充信息，不参与这一版本强度评分。选股模式仅调整个股权重，板块强度不随模式切换。</p></section>${providerRanking()}`;
}
function providerRanking(){const a=state.analysis,rows=(a.provider_sector_ranking||[]).filter(s=>s.type===a.config.sector_type&&s.asof_date===a.meta.trade_date&&Number.isFinite(s.pct_chg)).slice().sort((x,y)=>y.pct_chg-x.pct_chg||x.id.localeCompare(y.id)).slice(0,20);return rows.length?`<section class="panel" style="margin-top:20px"><div class="panel-heading"><h2>供应商同日板块指数记录</h2><span class="tag">指数与成分等权口径分开展示</span></div><div class="table-wrap"><table><thead><tr><th>展示序号</th><th>板块指数</th><th>供应商涨幅</th><th>来源</th></tr></thead><tbody>${rows.map((s,i)=>`<tr><td>${i+1}</td><td>${esc(s.name)}</td><td class="${color(s.pct_chg)}">${pct(s.pct_chg)}</td><td>${esc(s.source)}</td></tr>`).join('')}</tbody></table></div></section>`:'';}
function stocksView(){if(typeof strongStocksView==='function')return strongStocksView();return `<div class="view-info">${styles.find(s=>s[0]===state.analysis.config.style)[1]}：按板块共振、价格、量能、趋势和短线地位排序。所有权重与扣分可在详情中查看；评分不是胜率。</div><div class="panel-heading" style="padding:0 0 15px"><div class="pills"><button class="pill ${!state.showExcluded?'active':''}" data-action="show-candidates">观察候选 ${state.analysis.candidates.length}</button><button class="pill ${state.showExcluded?'active':''}" data-action="show-excluded">查看未入选 ${state.analysis.stocks.length-state.analysis.candidates.length}</button></div><span class="stat-label">自选只保存在本机浏览器</span></div>${stockPanel(state.showExcluded?'未入选股票 · 点击查看原因':'强势观察候选')}`;}
function ladderView(){const m=state.analysis.market;return `${statCards()}<div class="view-info warning">空间高度是连续交易日收盘涨停的连板数；“N天M板”不计为连续M板。完整性不足时显示缺失，避免把局部样本叫作全市场高度。</div><div class="page-grid"><section class="panel"><div class="panel-heading"><h2>${m.height_complete?'市场连板天梯':'已核验样本连板天梯'}</h2><span class="tag">排除 ST / 退市名称</span></div>${m.ladder.length?`<div class="ladder-full">${m.ladder.map(t=>`<div class="ladder-tier"><div class="tier-label"><b>${t.height}<small>${t.count}只 · 连板</small></b></div><div class="tier-stocks">${t.stocks.map(s=>`<button class="tier-stock" data-stock="${esc(s.code)}"><b>${esc(s.name)}</b><small>${esc(s.code)}</small></button>`).join('')}</div></div>`).join('')}</div>`:empty('连板数据尚未完整','免费涨停榜可能缺少部分证券；先等待核验，不估算空间高度。')}</section><div class="main-column"><section class="panel"><div class="panel-heading"><h2>情绪观察指标</h2></div><div class="data-list">${[['上涨 / 下跌',`${m.advancing} / ${m.declining}`],['上涨占比',num(m.breadth,1)+'%'],['炸板数量',num(m.broken,0)],['炸板率',m.broken_rate==null?'—':num(m.broken_rate,1)+'%'],['昨日连板晋级率',m.promotion_rate==null?'—':num(m.promotion_rate,1)+'%'],['晋级率样本数',num(m.promotion_denominator,0)],['规则情绪描述',m.phase]].map(([k,v])=>`<div class="data-row"><span>${k}</span><span>${v}</span></div>`).join('')}</div></section><section class="panel insight-panel"><div class="panel-heading"><h2>观察顺序</h2></div><div class="note-block">先看上涨广度与涨跌停分布，再看连板高度及昨日连板晋级，最后结合板块扩散与个股成交质量。<br><br>炸板率 = 收盘未封住的触板股 ÷（收盘涨停股 + 收盘未封住的触板股）。<br><br>晋级率 = 昨日已至少2连板且今日继续涨停的数量 ÷ 昨日至少2连板股票数。样本不完整时不计算。</div></section></div></div>`;}
function syncJobKey(j){return j?.id||j?.started_at||`${j?.state}:${j?.message}`;}
function syncElapsed(j){
 const start=Date.parse(j.started_at),end=j.ended_at?Date.parse(j.ended_at):Date.now();
 if(!Number.isFinite(start)||!Number.isFinite(end))return '计时中';
 const seconds=Math.max(0,Math.floor((end-start)/1000));
 return seconds<60?`${seconds}秒`:seconds<3600?`${Math.floor(seconds/60)}分${String(seconds%60).padStart(2,'0')}秒`:`${Math.floor(seconds/3600)}小时${Math.floor(seconds%3600/60)}分`;
}
function jobHTML(){
 const j=state.status?.job;if(!j||j.state==='idle'||state.syncDismissed===syncJobKey(j))return '';
 const running=j.state==='running',done=j.state==='done',detail=j.detail||{};
 const progress=done?100:Math.max(0,Math.min(99,Math.floor(Number(j.progress)||0)));
 const stages=[['quotes','收盘报价'],['sectors','板块与情绪'],['history','历史补齐'],['validate','校验与保存']];
 const index={connecting:0,quotes:0,sectors:1,limits:1,history:2,validate:3,save:3,complete:4}[j.stage]??0;
 const count=Number.isInteger(detail.completed)&&Number.isInteger(detail.total)?`${esc(detail.group||'')}已处理 <b>${detail.completed} / ${detail.total}</b> ${esc(detail.unit||'项')}${Number.isInteger(detail.succeeded)?` · 成功 ${detail.succeeded} · 失败 ${detail.failed||0}`:''}`:'';
 const summary=Number.isInteger(detail.history_total)?`历史补齐成功 ${detail.history_success??0} / ${detail.history_total} 只`:'';
 const source={eastmoney:'东方财富通道',sina:'新浪＋腾讯通道'}[detail.source]||'';
 const connection=state.syncConnectionError?`<div class="sync-connection" role="status">${icon('clock',13)}进度连接中断，正在自动重连；当前显示上次进度。<button type="button" data-action="sync-check">立即查询</button></div>`:'';
 return `<div class="sync-progress-head"><div class="sync-progress-title">${running?'<span class="spinner" aria-hidden="true"></span>':icon(done?'shield':'flag',19)}<div><b>${running?'正在更新行情':done?'行情更新完成':'行情更新未完成'}</b><small>${source}${detail.attempt>1?` · 第 ${detail.attempt} / ${detail.attempt_total} 轮，本轮进度重新计算`:''}${source?' · ':''}${running?'可继续浏览其他页面':'任务已结束'}</small></div></div><div class="sync-progress-value"><strong>${progress}<small>%</small></strong><span>${running?'已用时':'耗时'} <span id="sync-elapsed">${syncElapsed(j)}</span></span>${!running?'<button type="button" class="icon-button" data-action="sync-dismiss" aria-label="收起更新结果">×</button>':''}</div></div><div class="sync-progress-track" role="progressbar" aria-label="行情更新，本轮阶段进度" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${progress}" aria-valuetext="${progress}%，${esc(j.message)}"><i style="width:${progress}%"></i></div><div class="sync-progress-detail"><span role="status">${esc(j.message)}</span>${count||summary?`<span class="sync-count">${count||summary}</span>`:''}</div>${running?`<ol class="sync-steps">${stages.map(([key,label],i)=>`<li class="${i<index?'complete':i===index?'current':''}" ${i===index?'aria-current="step"':''}><i>${i<index?'✓':String(i+1).padStart(2,'0')}</i>${label}</li>`).join('')}</ol><p class="sync-progress-note">进度按采集阶段估算，接口等待与历史补齐耗时可能较长。</p>`:!done?'<div class="sync-error-actions"><button type="button" class="button small" data-action="sync">重新更新</button><span>原行情快照可继续查看。</span></div>':''}${connection}`;
}
function renderSyncProgress(){
 clearTimeout(syncClockTimer);
 const j=state.status?.job,busy=state.syncStarting||j?.state==='running',button=$('#sync-button');
 if(button){button.disabled=busy;button.innerHTML=`${icon('refresh')} ${busy?'更新中…':'更新数据'}`;button.setAttribute('aria-busy',String(busy));}
 const submit=$('#sync-form button[type="submit"]');if(submit)submit.disabled=busy;
 const panel=$('#sync-progress');if(!panel)return;
 const html=jobHTML();panel.hidden=!html;panel.className=`sync-progress ${j?.state==='running'?'is-running':j?.state==='error'?'is-error':'is-done'}`;panel.innerHTML=html;
 function tick(){const node=$('#sync-elapsed');if(node&&state.status?.job?.state==='running'){node.textContent=syncElapsed(state.status.job);syncClockTimer=setTimeout(tick,1000);}}
 if(j?.state==='running'&&html)syncClockTimer=setTimeout(tick,1000);
}
function dataView(){const a=state.analysis,q=a.quality;return `${sourceHubHTML()}<div class="page-grid"><div class="main-column"><section class="panel"><div class="panel-heading"><h2>行情快照与来源</h2><span class="tag ${q.demo?'warm':'mint'}">${q.demo?'演示':'本地快照'}</span></div><div class="data-list">${[['分析日',a.meta.trade_date],['主数据源',a.meta.source],['记录数量',`${q.actual_count}只 / 预期${q.expected_count??'未知'}只`],['获取时间',a.meta.fetched_at.replace('T',' ')],['市场范围',a.meta.universe],['日历来源',a.meta.calendar_source||'用户提供'],['参考收盘价一致',`${q.matched}只（只核对收盘）`],['参考源冲突',`${q.conflicts}只，禁止入选`],['单源待核验',`${q.single_source}只`],['证据目录',a.meta.evidence_directory||'内置合成数据，无真实证据'],['原始数据单位','人民币元 / 股 / 百分比']].map(([k,v])=>`<div class="data-row"><span>${esc(k)}</span><span>${esc(v)}</span></div>`).join('')}</div><div class="action-list"><button class="button primary" data-action="sync">更新免费数据</button><button class="button" data-action="import">导入行情快照</button><button class="button" data-action="reference">核对参考收盘</button><button class="button" data-action="snapshot">下载完整快照</button><button class="button" data-action="demo">切换演示</button><button class="button" data-action="active">读取最新快照</button></div></section><section class="panel"><div class="panel-heading"><h2>数据边界与缺失项</h2></div><ul class="note-list">${(a.meta.notes||[]).map(x=>`<li>${esc(x)}</li>`).join('')}<li>公告检索与筹码分布未接入；所有候选均需额外检查公告。日线前高、前低仅作观察参照。</li><li>现有规则尚未经过历史收益有效性验证，不展示胜率或收益预测。</li></ul><div class="data-list">${(a.meta.source_urls||['https://akshare.akfamily.xyz/data/stock/stock.html']).map(url=>`<a class="source-link" href="${safeURL(url)}" target="_blank" rel="noopener noreferrer">${esc(url)} ↗</a>`).join('')}</div></section></div><div class="main-column"><section class="panel"><div class="panel-heading"><h2>当前模式评分权重</h2><span class="tag">规则 v${a.methodology.version}</span></div><div class="method-list">${Object.entries(a.weights).map(([key,value])=>`<div class="weight-line"><span>${({sector:'板块共振',price:'价格强度',volume:'量能活跃',trend:'趋势结构',status:'短线地位'})[key]}</span><div class="weight-track"><i style="width:${value*2}%"></i></div><b>${value}分</b></div>`).join('')}<div class="method-item"><b>扣分后得到综合分，最低0分</b><p>一字板 −15；偏离20日均线超过25% −10；量能超过前20根均量5倍 −8；5板及以上 −5；距日内高点回落超过5% −8。</p></div></div></section><section class="panel"><div class="panel-heading"><h2>计算口径</h2></div><div class="method-list">${[['板块强度','sector'],['价格强度','price'],['量能活跃','volume'],['趋势结构','trend'],['短线地位','status'],['数据缺失处理','missing'],['统计范围','scope']].map(([name,key])=>`<div class="method-item"><b>${name}</b><p>${esc(a.methodology[key])}</p></div>`).join('')}</div></section></div></div>`;}

function canvasContext(canvas){const box=canvas.getBoundingClientRect(),dpr=window.devicePixelRatio||1;canvas.width=Math.max(1,Math.round(box.width*dpr));canvas.height=Math.max(1,Math.round(box.height*dpr));const ctx=canvas.getContext('2d');ctx.scale(dpr,dpr);ctx.font='9px Segoe UI, Microsoft YaHei';return {ctx,w:box.width,h:box.height};}
function drawBreadth(){const canvas=$('#breadth-chart');if(!canvas)return;const {ctx,w,h}=canvasContext(canvas),points=state.analysis.market.history.filter(p=>p.breadth!=null);if(points.length<2)return;const l=27,r=w-7,t=8,b=h-22;const x=i=>l+i*(r-l)/(points.length-1),y=n=>b-n/100*(b-t);for(const n of [0,50,100]){ctx.strokeStyle='#edf1f0';ctx.beginPath();ctx.moveTo(l,y(n));ctx.lineTo(r,y(n));ctx.stroke();ctx.fillStyle='#a6b2b3';ctx.fillText(`${n}`,1,y(n)+3);}const grad=ctx.createLinearGradient(0,t,0,b);grad.addColorStop(0,'#75b79b45');grad.addColorStop(1,'#75b79b03');ctx.beginPath();ctx.moveTo(x(0),b);points.forEach((p,i)=>ctx.lineTo(x(i),y(p.breadth)));ctx.lineTo(x(points.length-1),b);ctx.closePath();ctx.fillStyle=grad;ctx.fill();ctx.beginPath();points.forEach((p,i)=>i?ctx.lineTo(x(i),y(p.breadth)):ctx.moveTo(x(i),y(p.breadth)));ctx.strokeStyle='#7db29b';ctx.lineWidth=1.6;ctx.stroke();ctx.fillStyle='#a2b0b2';ctx.fillText(points[0].date.slice(5),l,h-4);ctx.fillText(points.at(-1).date.slice(5),r-29,h-4);}

let currentDetail=null;
function drawAll(){drawBreadth();if(currentDetail&&!$('#stock-drawer').hidden)drawCandles(currentDetail);}
function renderStockDrawer(row,detail=null){const code=row.code,c=row.current,t=detail?.technical||row.technical;$('#stock-drawer').innerHTML=`<div class="drawer-head"><div><div class="eyebrow">STOCK RESEARCH / ${esc(state.analysis.meta.trade_date)}</div><h2>${esc(row.name)}</h2><small>${esc(row.code)} · ${esc(row.board)} · ${esc(row.primary_sector||'未分类')}</small></div><button class="icon-button" data-action="close-drawer" aria-label="关闭个股详情">×</button></div><div class="drawer-price"><b class="${color(row.pct_chg)}">${num(c?.close)}</b><span class="${color(row.pct_chg)}">${pct(row.pct_chg)}</span><span class="tag ${row.selected?'mint':'warm'}">${row.selected?'观察候选':'未入选'}</span><button class="button small" data-watch="${esc(code)}">${isSaved(code)?'已加入自选':'加入本机自选'}</button></div><div class="drawer-grid"><div><small>成交额</small><b>${amount(c?.amount_yuan)}</b></div><div><small>换手率</small><b>${num(c?.turnover_rate,2)}%</b></div><div><small>20日均量倍数</small><b>${num(t.volume_ratio,2)} ×</b></div><div><small>确认连板</small><b>${num(row.consecutive,0)} 板</b></div></div><div class="drawer-chart"><canvas id="stock-chart" role="img" aria-label="日K线、均线和成交量"></canvas><div id="stock-chart-status" class="stock-chart-status" role="status" aria-live="polite" hidden></div></div><div class="panel-caption" id="stock-chart-caption">正在读取同日行情与历史数据…</div><section class="drawer-section"><h3>综合分如何得到 <span class="score-total">${num(row.score,1)}<small style="font-size:10px;color:#96aaa0"> / 100</small></span></h3>${row.components.map(x=>`<div class="component"><span>${esc(x.label)}</span><div class="track"><i style="width:${x.earned/x.max*100}%"></i></div><b>${x.earned} / ${x.max}</b><small>${esc(x.evidence)}</small></div>`).join('')||'<p>关键数据不足，无法计算完整评分。</p>'}${row.penalties.map(p=>`<div class="penalty">−${p.points}分 · ${esc(p.label)}</div>`).join('')}</section>${row.exclusions.length?`<section class="drawer-section"><h3>未入选原因</h3>${row.exclusions.map(x=>`<p>· ${esc(x)}</p>`).join('')}</section>`:''}<section class="drawer-section"><h3>量价与趋势证据</h3><div class="drawer-grid"><div><small>5日复权涨幅</small><b class="${color(t.return5)}">${pct(t.return5)}</b></div><div><small>20日复权涨幅</small><b class="${color(t.return20)}">${pct(t.return20)}</b></div><div><small>距MA20</small><b>${pct(t.extension20)}</b></div><div><small>20根新高突破</small><b>${t.breakout20==null?'待核验':t.breakout20?'是':'否'}</b></div></div><p>${t.macd?`MACD：DIFF ${num(t.macd.diff,4)} / DEA ${num(t.macd.dea,4)} / 柱 ${num(t.macd.hist,4)}，${t.macd.above_zero?'DIFF与DEA均在零轴上':'DIFF/DEA未同时位于零轴上'}，柱值${t.macd.expanding?'上升':'下降'}。`:'MACD：复权日线不足120根，不展示未充分预热的结果。'}</p><p>阳/阴K线均量比：${num(t.up_down_volume?.ratio,2)}（阳K ${t.up_down_volume?.positive_days??'—'}根 / 阴K ${t.up_down_volume?.negative_days??'—'}根；不能据此判断主力身份）。</p></section><section class="drawer-section"><h3>风险与核验状态</h3><div class="drawer-badges">${row.risk_flags.map(x=>`<span class="tag warm">${esc(x)}</span>`).join('')||'<span class="tag">暂无额外记录，仍需核验公告</span>'}</div><p>${state.analysis.meta.mode==='demo'?'合成行情，所有价格与证券均为虚构。':row.verification.status==='matched'?'参考源同日收盘价一致；仅核对收盘，不代表所有字段已独立核验。':'当前收盘价尚未完成有效交叉核验，或存在冲突。'}</p><p>筹码峰待核验；公告与除权事件需另行查证。</p></section><section class="drawer-section"><h3>下一交易日观察</h3><p class="observation-label">确认条件</p><p>${esc(row.confirmation||'关键数据不足，暂不形成观察条件。')}</p><p class="observation-label">失效条件</p><p>${esc(row.invalidation||'补齐数据后再评估。')}</p><p>本日高 / 低：${num(c?.high)} / ${num(c?.low)}，仅作价格观察参照，除权时需重算；T+1、跳空和跌停可能限制卖出。</p></section>`;}
function closeDrawer(){cancelStockRequest();$('#stock-drawer').hidden=true;$('#drawer-overlay').hidden=true;document.body.style.overflow='';currentDetail=null;lastFocus?.focus();}
function download(name,text,mime='text/plain;charset=utf-8'){if(window.__ZHANGSHI_DESKTOP__){window.desktopSaveBlob(name,text,mime).catch(error=>toast('导出未完成：'+error.message));return;}const url=URL.createObjectURL(new Blob([text],{type:mime}));const link=document.createElement('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
function report(){const a=state.analysis,m=a.market,candidates=a.stocks.filter(s=>s.selected);const lines=[`# 涨势盘后研究 · ${a.meta.trade_date}`,``,a.meta.mode==='demo'?'**合成演示数据，非真实行情，不能用于交易。**':`数据源：${a.meta.source}`,``, `范围：${a.meta.universe}`,`获取时间：${a.meta.fetched_at}`,`模式：${styles.find(x=>x[0]===a.config.style)[1]}；规则版本 ${a.methodology.version}`,``, `## 市场样本`,`成交额：${amount(m.amount_yuan)}；上涨/下跌：${m.advancing}/${m.declining}；涨停/跌停：${m.limit_up??'缺失'}/${m.limit_down??'缺失'}；连续涨停高度：${m.height??'缺失'}。`,``, `## 板块研究排序（仅当前已展开且完整的同类板块）`,...a.sectors.filter(s=>s.score!=null).slice(0,5).map((s,i)=>`${i+1}. ${s.name}：规则强度${s.score}；等权涨幅${pct(s.avg_pct)}；上涨占比${s.breadth}%；收盘涨停${s.limit_up}只；成分${s.available_count}/${s.member_count}。`),``, `## 强势观察候选`];if(!candidates.length)lines.push('当前规则没有候选，保留空池。');candidates.slice(0,20).forEach((s,i)=>lines.push(``, `### ${i+1}. ${s.name} ${s.code}`,`综合分：${s.score}；${s.role}；收盘${num(s.current?.close)}；涨幅${pct(s.pct_chg)}。`,`依据：${s.reasons.join('；')||'详见评分明细'}。`,`风险：${s.risk_flags.join('；')||'需核验公告'}。`,`确认：${s.confirmation}`,`失效：${s.invalidation}`));lines.push(``,`## 数据边界`,...(a.meta.notes||[]).map(x=>`- ${x}`),'- 公告、筹码与除权事件未完成独立核验。','- 规则分是观察排序，尚未验证收益有效性；不代表胜率或收益预测。',``,`## 来源`,...(a.meta.source_urls||[]).map(url=>`- ${url}`));download(`盘后研究-${a.meta.trade_date}${a.meta.mode==='demo'?'-演示':''}.md`,lines.join('\n'));toast('已导出复盘报告');}
function exportCSV(){const rows=filteredStocks();const cell=x=>{let s=String(x??'');if(/^[=+\-@\t\r]/.test(s))s="'"+s;return `"${s.replace(/"/g,'""')}"`;};const lines=[['日期','数据模式','代码','名称','板块','评分','收盘','涨跌幅%','成交额元','换手率%','角色','入选','原因或缺失','风险'],...rows.map(s=>[state.analysis.meta.trade_date,state.analysis.meta.mode,s.code,s.name,s.primary_sector,s.score,s.current?.close,s.pct_chg,s.current?.amount_yuan,s.current?.turnover_rate,s.role,s.selected?'是':'否',s.selected?s.reasons.join('；'):s.exclusions.join('；'),s.risk_flags.join('；')])];download(`观察池-${state.analysis.meta.trade_date}.csv`,'\uFEFF'+lines.map(row=>row.map(cell).join(',')).join('\r\n'),'text/csv;charset=utf-8');toast(`已导出${rows.length}只股票`);}
function parseCSV(text){const rows=[];let row=[],value='',quoted=false;for(let i=0;i<text.length;i++){const c=text[i];if(c==='"'){if(quoted&&text[i+1]==='"'){value+='"';i++;}else quoted=!quoted;}else if(c===','&&!quoted){row.push(value);value='';}else if((c==='\n'||c==='\r')&&!quoted){if(c==='\r'&&text[i+1]==='\n')i++;row.push(value);if(row.some(x=>x.trim()))rows.push(row);row=[];value='';}else value+=c;}if(quoted)throw new Error('CSV 引号未闭合');row.push(value);if(row.some(x=>x.trim()))rows.push(row);const aliases={'代码':'code','股票代码':'code','证券代码':'code','收盘':'close','收盘价':'close','日期':'date','交易日期':'date'};const headers=(rows.shift()||[]).map(x=>{const h=x.replace(/^\uFEFF/,'').trim();return aliases[h]||h;});if(new Set(headers).size!==headers.length)throw new Error('CSV列名重复或含重复别名');if(!headers.includes('code')||!headers.includes('close'))throw new Error('CSV 缺少 code 或 close 列');return rows.map(r=>Object.fromEntries(headers.map((h,i)=>[h,(r[i]||'').trim()])));}

function openSync(){
 if(state.syncStarting||state.status?.job?.state==='running'){renderSyncProgress();toast('行情正在更新，顶部可查看进度。');return;}
 const form=$('#sync-form');form.elements.date.value=state.status.today;$('#sync-dialog').showModal();
}
async function poll(){
 clearTimeout(pollTimer);if(state.syncPolling)return;state.syncPolling=true;
 try{
  const status=await api('/api/status');if(state.syncStarting)return;
  state.status=status;state.syncConnectionError=null;renderSyncProgress();
  const j=state.status.job,key=syncJobKey(j);
  if(j.state==='done'&&state.syncHandled!==key){
   state.syncHandled=key;state.mode='active';
   const loaded=await load(state.analysis?.config);
   if(state.view==='quant'&&typeof loadQuant==='function')await loadQuant();
   if(loaded)toast('真实行情已更新，请检查数据完整度');
  }else if(j.state==='error'&&state.syncHandled!==key){state.syncHandled=key;toast(j.message);}
 }catch(error){state.syncConnectionError=error.message;renderSyncProgress();}
 finally{state.syncPolling=false;pollTimer=setTimeout(poll,state.syncConnectionError?3000:state.status?.job?.state==='running'?1000:5000);}
}
async function startSync(event){
 event.preventDefault();if(state.syncStarting||state.status?.job?.state==='running')return;
 const form=new FormData(event.target),previous=state.status.job;
 state.syncStarting=true;state.syncConnectionError=null;state.syncDismissed=null;
 state.status.job={id:'submitting',state:'running',stage:'connecting',progress:0,started_at:new Date().toISOString(),message:'正在提交更新请求…'};
 renderSyncProgress();$('#sync-dialog').close();
 try{
  const response=await api('/api/sync',{date:form.get('date'),industry_limit:Number(form.get('industry_limit')),theme_limit:Number(form.get('theme_limit'))});
  if(response.job)state.status.job=response.job;
 }catch(error){state.status.job=previous;toast(`更新请求未确认：${error.message}；正在查询任务状态。`);}
 finally{state.syncStarting=false;renderSyncProgress();poll();}
}
document.addEventListener('click',async event=>{
 const target=event.target.closest('button,[data-stock],a[data-nav]');if(!target)return;
 if(target.dataset.style){await changeStyle(target.dataset.style);}
 else if(target.dataset.sectorOrder){if(['daily','strength'].includes(target.dataset.sectorOrder)){state.sectorOrder=target.dataset.sectorOrder;render();}}
 else if(target.dataset.kind){state.sector=null;await load({...state.analysis.config,sector_type:target.dataset.kind});}
 else if(target.dataset.sectorUnexpanded){navigate('sectors');toast('该板块已有同日指数排名，成分尚未展开。请更新数据扩大板块范围，当前不生成个股候选。');}
 else if(target.dataset.nav){navigate(target.dataset.nav);}
 else if(target.dataset.sector){state.sector=target.dataset.sector;state.showExcluded=false;state.role='all';state.watchOnly=false;navigate('stocks');if(state.view==='stocks')render();}
 else if(target.dataset.stock){openStock(target.dataset.stock);}
 else if(target.dataset.watch){toggleWatch(target.dataset.watch);}
 else if(target.dataset.role){state.role=target.dataset.role;renderTable();}
 else if(target.dataset.sort){state.sortDirection=state.sort===target.dataset.sort?-state.sortDirection:-1;state.sort=target.dataset.sort;renderTable();}
 else if(target.classList.contains('close-dialog')){target.closest('dialog').close();}
 else if(target.dataset.action){try{switch(target.dataset.action){
  case 'reload':await load();break;
  case 'sync':openSync();break;
  case 'sync-dismiss':state.syncDismissed=syncJobKey(state.status.job);renderSyncProgress();break;
  case 'sync-check':poll();break;
  case 'csv':exportCSV();break;
  case 'close-drawer':closeDrawer();break;
  case 'clear-sector':state.sector=null;render();break;
  case 'watch-only':state.watchOnly=!state.watchOnly;target.classList.toggle('active',state.watchOnly);renderTable();break;
  case 'show-excluded':state.showExcluded=true;render();break;
  case 'show-candidates':state.showExcluded=false;render();break;
  case 'mode-candidates':state.showExcluded=false;state.sector=null;state.role='all';state.search='';state.watchOnly=false;navigate('stocks');if(state.view==='stocks')render();break;
  case 'import':$('#snapshot-file').click();break;
  case 'reference':$('#reference-form').elements.trade_date.value=state.analysis.meta.trade_date;$('#reference-dialog').showModal();break;
  case 'snapshot':download(`行情快照-${state.analysis.meta.trade_date}.json`,JSON.stringify(await api(`/api/snapshot?mode=${state.mode}`)), 'application/json');break;
  case 'demo':state.mode='demo';state.sector=null;await load(state.analysis.config);break;
  case 'active':state.mode='active';state.sector=null;await load(state.analysis.config);break;
  case 'apply-filters':await load({...state.analysis.config,min_amount:Number($('#f-amount').value)*1e8,min_turnover:Number($('#f-min-turn').value),max_turnover:Number($('#f-max-turn').value),min_score:Number($('#f-score').value),min_sector_score:Number($('#f-sector').value),include_one_word:$('#f-one-word').checked});toast('筛选规则已应用');break;
 }}catch(error){toast(error.message);}}
});
document.addEventListener('input',event=>{if(event.target.id==='stock-search'){state.search=event.target.value;renderTable();}});
document.addEventListener('keydown',event=>{if(event.key==='Enter'&&event.target.dataset.stock&&event.target.tagName!=='BUTTON')openStock(event.target.dataset.stock);if(event.key==='Escape'&&!$('#stock-drawer').hidden)closeDrawer();if(event.key==='Tab'&&!$('#stock-drawer').hidden){const controls=$$('button,a,input,[tabindex="0"]',$('#stock-drawer'));if(!controls.length)return;const first=controls[0],last=controls.at(-1);if(event.shiftKey&&document.activeElement===first){last.focus();event.preventDefault();}else if(!event.shiftKey&&document.activeElement===last){first.focus();event.preventDefault();}}});
$('#filter-button').addEventListener('click',()=>{$('#filter-panel').hidden=!$('#filter-panel').hidden;});
$('#sync-button').addEventListener('click',openSync);$('#export-button').addEventListener('click',()=>state.view==='quant'?exportQuant():state.view==='stocks'?exportCSV():state.view==='shortline'&&typeof exportShortPlan==='function'?exportShortPlan():state.view==='review'?exportReviewBundle():report());$('#help-button').addEventListener('click',()=>navigate('data'));$('#drawer-overlay').addEventListener('click',closeDrawer);
$('#sync-form').addEventListener('submit',startSync);
$('#snapshot-file').addEventListener('change',async event=>{const file=event.target.files[0];if(!file)return;try{if(file.size>80*1024*1024)throw new Error('快照不能超过80MB');const dataset=JSON.parse(await file.text());await api('/api/import',{dataset});state.mode='active';state.sector=null;await load(state.analysis.config);toast('快照已导入');}catch(error){toast(error.message);}finally{event.target.value='';}});
$('#reference-form').addEventListener('submit',async event=>{event.preventDefault();const fd=new FormData(event.target);try{const rows=await readSourceReferenceCSV(fd.get('file'));await api('/api/reference',{source:fd.get('source'),trade_date:fd.get('trade_date'),rows,mode:state.mode});$('#reference-dialog').close();state.mode='active';await load(state.analysis.config);toast('参考收盘核对完成');}catch(error){toast(error.message);}});
window.addEventListener('hashchange',()=>{state.role='all';state.search='';state.watchOnly=false;state.showExcluded=false;if(state.view==='stocks'&&location.hash!=='#stocks')state.sector=null;render();});let resizeTimer;window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(drawAll,120);});
async function boot(){try{state.status=await api('/api/status');state.syncHandled=['done','error'].includes(state.status.job.state)?syncJobKey(state.status.job):null;renderSyncProgress();await load();poll();}catch(error){toast(error.message);$('#content').innerHTML=window.__ZHANGSHI_DESKTOP__?empty('桌面功能加载失败','请重新启动桌面研究台；详细错误可在数据目录的 desktop.log 中查看。'):empty('网页服务尚未启动','请双击“启动网页版研究台.cmd”，再刷新此页面。');}}
boot();
