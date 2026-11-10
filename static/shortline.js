'use strict';

// The short-term workspace uses the same validated snapshot and screening rules.
const shortState={layout:'cards',mainline:false,breakout:false,avoidExtended:false,matched:false,limit:24,storageError:false};
let shortPlans={};
try{const value=JSON.parse(localStorage.getItem('after-close-short-plans')||'{}');if(value&&typeof value==='object'&&!Array.isArray(value))shortPlans=value;}catch{}
function shortPlanKey(){return `${state.analysis.meta.mode==='demo'?'demo':'real'}:${state.analysis.meta.trade_date}`;}
function shortPlan(){
 const saved=shortPlans[shortPlanKey()]||{};
 return {codes:Array.isArray(saved.codes)?[...new Set(saved.codes.filter(c=>typeof c==='string'))].slice(0,10):[],note:typeof saved.note==='string'?saved.note.slice(0,2000):''};
}
function saveShortPlan(plan){
 const next={...shortPlans,[shortPlanKey()]:plan};
 shortPlans=next;
 if(typeof v2SyncPlan==='function'&&window.v2SharedReady)v2SyncPlan(shortPlanKey(),plan);
 try{localStorage.setItem('after-close-short-plans',JSON.stringify(next));shortState.storageError=false;return true;}
 catch{shortState.storageError=true;toast('本机存储不可用，观察清单仅暂存在当前页面，请导出后保留。');return false;}
}
function shortDailyRanking(){
 const a=state.analysis;
 if(a.sector_leaders)return a.sector_leaders;
 return {rows:(a.sectors||[]).filter(s=>s.complete&&s.avg_pct>0).map(s=>({...s,daily_pct:s.avg_pct,expanded:true})).sort((a,b)=>b.daily_pct-a.daily_pct||a.id.localeCompare(b.id)),basis:'同日成分等权涨幅',scope:'当前已展开板块',complete_universe:false};
}
function shortLeaders(){return (shortDailyRanking().rows||[]).filter(s=>s.asof_date===state.analysis.meta.trade_date&&s.daily_pct>0).slice(0,3);}
function shortFilterRows(rows){
 let result=rows;
 if(shortState.mainline){const members=new Set(shortLeaders().filter(s=>s.expanded!==false).flatMap(s=>s.members||[]));result=result.filter(s=>members.has(s.code));}
 if(shortState.breakout)result=result.filter(s=>s.technical?.breakout20===true);
 if(shortState.avoidExtended)result=result.filter(s=>s.technical?.extension20!=null&&s.technical.extension20<=25);
 if(shortState.matched)result=result.filter(s=>s.verification?.status==='matched');
 return result;
}
function shortEntryHTML(){return `<div class="short-entry"><div><span class="short-kicker">SHORT TERM DESK</span><b>短线工作流，从这里开始</b><small>读情绪 → 看主线 → 选强势 → 留下次日观察条件</small></div><div><button class="button" data-nav="shortline">短线复盘 ${icon('arrow',13)}</button><button class="button primary" data-nav="stocks">强势股精选 ${icon('arrow',13)}</button></div></div>`;}
function shortMood(){
 const m=state.analysis.market;
 if(!m.active_count)return {label:'数据待齐',note:'暂无有效收盘样本，先更新或核验数据。',tone:'muted'};
 if(m.limit_up==null)return {label:m.breadth>=65?'上涨占优':m.breadth>=40?'涨跌分化':'下跌占优',note:'按有效样本的上涨广度描述；涨跌停覆盖不足，暂不确认完整情绪周期。',tone:m.breadth>=65?'warm':m.breadth>=40?'neutral':'cool'};
 return {label:m.phase,note:'按上涨广度与收盘涨停密度描述当日状态，不作为次日涨跌预测。',tone:m.breadth>=65?'warm':m.breadth>=40?'neutral':'cool'};
}
function shortMetric(label,value,unit,note,tone=''){return `<article class="short-metric"><span>${label}</span><div class="${tone}"><b>${value}</b><small>${unit}</small></div><p>${note}</p></article>`;}
function shortNextFocus(m){if(!m.active_count)return '先补齐同日有效行情，再评估主线与候选的延续条件。';return m.breadth>=65?'上涨能否从少数领涨股扩散到更多成分，主线回落时是否仍有承接。':m.breadth>=40?'领涨板块能否保持一致，分歧后是否出现清晰的强弱分层。':'下跌广度是否收敛，强势方向能否独立保持，而不是仅靠少数股票反抽。';}
function shortlineView(){
 const a=state.analysis,m=a.market,mood=shortMood(),known=m.active_count?(m.limit_up??m.limit_up_confirmed):null,down=m.active_count?(m.limit_down??m.limit_down_confirmed):null;
 const height=m.active_count?(m.height??m.confirmed_height):null;
 const stats=shortMetric('收盘涨停',num(known,0),'只',!m.active_count?'暂无有效收盘样本':m.limit_up==null?'已确认样本 · 全量状态未齐':'当前有效收盘范围','red')+
  shortMetric('空间高度',num(height,0),'板',!m.active_count?'暂无有效收盘样本':m.height==null?'已确认连板高度 · 覆盖不全':'连续交易日收盘涨停','amber')+
  shortMetric('炸板率',m.broken_rate==null?'—':num(m.broken_rate,1),m.broken_rate==null?'':'%',m.broken_rate==null?(m.broken_confirmed===0&&m.limit_up===0?'当前范围无已确认触板样本':'触板状态不完整，暂不计算'):'炸板 / 当日触板样本')+
  shortMetric('收盘跌停',num(down,0),'只',!m.active_count?'暂无有效收盘样本':m.limit_down==null?'已确认样本 · 全量状态未齐':'当前有效收盘范围','green')+
  shortMetric('上涨广度',m.active_count?num(m.breadth,1):'—',m.active_count?'%':'',`${num(m.advancing,0)} 涨 / ${num(m.declining,0)} 跌`)+
  shortMetric('强势候选',String(a.candidates.length),'只',`${styles.find(s=>s[0]===a.config.style)?.[1]||''} · 已过当前门槛`,'teal');
 return `<div class="short-hero"><div><div class="short-kicker">SHORT TERM / ${esc(a.meta.trade_date)}</div><h2>今天谁在领涨，<br>明天看谁能延续。</h2><p>先看市场是否配合，再把注意力收敛到有证据的强势。</p><div class="short-hero-actions"><button class="button" data-nav="stocks">筛选强势股 ${icon('arrow',14)}</button><button class="text-button" data-nav="ladder">查看连板梯队 ${icon('arrow',12)}</button></div></div><div class="short-mood ${mood.tone}"><span>收盘情绪速读</span><strong>${esc(mood.label)}</strong><p>${esc(mood.note)}</p><small>${a.meta.mode==='demo'?'合成演示样本':`${num(m.active_count,0)}只有效收盘样本`} · 非盘中监控</small></div></div><div class="short-metrics">${stats}</div><div class="short-review-grid"><div class="short-main"><section class="panel"><div class="panel-heading"><h2>当日主线 <span class="sub">DAILY LEADERS</span></h2>${sectorSwitch()}</div>${shortSectorHTML()}<div class="panel-caption">${esc(shortDailyRanking().basis)} · ${esc(shortDailyRanking().scope||'同一分类内比较')}。行业与题材分开，重叠成分不重复计为全市场。</div></section><section class="panel"><div class="panel-heading"><h2>强势候选速览 <span class="inline-count">${a.candidates.length}</span></h2><button class="text-button" data-nav="stocks">进入精选 ${icon('arrow',12)}</button></div><div class="short-preview-grid">${a.stocks.filter(s=>s.selected).slice(0,4).map((s,i)=>shortStockCard(s,i,true)).join('')||empty('本轮暂无强势候选','保留空池；先检查行情完整度和筛选条件。')}</div></section></div><div class="short-side"><section class="panel"><div class="panel-heading"><h2>赚钱效应，先看广度</h2><span class="tag">有效样本</span></div>${shortBreadthHTML()}<div class="short-observation"><b>次日先观察</b><p>${shortNextFocus(m)}</p><b>哪些信号会改变判断</b><p>主线广度收缩、前排个股集中转弱，或新的公告改变原有观察逻辑。</p></div></section><section class="panel"><div class="panel-heading"><h2>高度与接力</h2><button class="text-button" data-nav="ladder">完整梯队 ${icon('arrow',12)}</button></div>${miniLadder()}<div class="panel-caption">${m.height_complete?'当前收盘范围连板状态可核验。':'当前仅展示已确认梯队，未知高度不按0板处理。'}昨日连板晋级率：${m.promotion_rate==null?'待核验':num(m.promotion_rate,1)+'%'}。</div></section><div id="short-plan-host"></div></div></div>`;
}
function shortSectorHTML(){
 const leaders=shortLeaders();
 if(!leaders.length)return empty('暂无可比的上涨主线','不会用下跌板块或旧日榜单补位；可切换行业/题材，或更新数据。');
 return `<div class="short-sector-list">${leaders.map((s,i)=>`<button class="short-sector" ${sectorClick(s)}><span class="short-sector-rank">0${i+1}</span><span class="short-sector-name"><b>${esc(s.name)}</b><small>${s.expanded===false?'成分尚未展开':`上涨 ${s.breadth==null?'待核验':num(s.breadth,0)+'%'} · 收盘涨停 ${s.limit_up==null?'待核验':s.limit_up+'只'}`}</small></span><strong class="red">${pct(s.daily_pct)}</strong>${icon('arrow',14)}</button>`).join('')}</div>`;
}
function shortBreadthHTML(){
 const m=state.analysis.market,n=m.active_count||0;
 const parts=[['上涨',m.advancing||0,'up'],['平盘',m.unchanged||0,'flat'],['下跌',m.declining||0,'down']];
 return `<div class="short-breadth"><div class="short-breadth-value"><strong>${n?num(m.breadth,1)+'%':'—'}</strong><span>上涨占比</span></div><div class="short-breadth-bar" role="img" aria-label="有效样本：上涨${m.advancing||0}只、平盘${m.unchanged||0}只、下跌${m.declining||0}只">${parts.map(([label,count,tone])=>`<i class="${tone}" style="width:${n?count/n*100:0}%"></i>`).join('')}</div><div class="short-breadth-legend">${parts.map(([label,count,tone])=>`<span><i class="${tone}"></i>${label}<b>${num(count,0)}</b></span>`).join('')}</div><p>涨跌停状态核验覆盖：${m.limit_status_coverage==null?'待核验':num(m.limit_status_coverage,1)+'%'}；缺失状态不当作未涨停。</p></div>`;
}
function shortRoleCounts(){const rows=state.analysis.stocks.filter(s=>s.selected);return {all:rows.length,first:rows.filter(s=>s.role==='首板观察').length,relay:rows.filter(s=>s.role==='连板观察').length,trend:rows.filter(s=>s.role?.startsWith('趋势')).length};}
function strongStocksView(){
 const a=state.analysis,counts=shortRoleCounts();
 const roles=[['all','全部强势',counts.all,'先过质量门槛，再比较强弱'],['首板观察','首板观察',counts.first,'收盘首板，留意次日承接'],['连板观察','连板接力',counts.relay,'连续封板，留意高位分歧'],['趋势','趋势强势',counts.trend,'均线与量价，观察趋势延续']];
 const filters=[['mainline','仅当日主线','按当日领涨前三的已展开成分筛选；未展开板块不会补造候选'],['breakout','突破20日高点','复权收盘高于此前20根复权最高价'],['avoidExtended','剔除高于MA20超25%','仅限制向上偏离；指标缺失也会排除，仅为当前筛选阈值'],['matched','参考收盘已一致','只代表同日收盘核对，不代表所有字段均已验证']];
 return `<div class="short-stock-intro"><div><span class="short-kicker">STRONG STOCK SELECTOR</span><h2>先筛条件，再选观察重点。</h2><p>入选依据、量价结构和失效条件放在一起，减少来回翻页。</p></div><button class="button" data-nav="shortline">先看短线复盘 ${icon('arrow',13)}</button></div><div class="short-role-grid">${roles.map(([key,label,count,note])=>`<button class="short-role ${state.role===key?'active':''}" data-role="${key}" aria-pressed="${state.role===key}"><span>${label}</span><strong>${count}<small>只</small></strong><p>${note}</p></button>`).join('')}</div><div class="short-selection-layout"><div class="short-main"><section class="panel short-selector"><div class="short-filter-row"><div class="pills"><button class="pill ${!state.showExcluded?'active':''}" data-action="show-candidates">强势候选 ${counts.all}</button><button class="pill ${state.showExcluded?'active':''}" data-action="show-excluded">未入选及原因</button><button class="pill ${state.watchOnly?'active':''}" data-action="watch-only">本机自选</button></div><div class="short-layout-switch" role="group" aria-label="候选展示方式">${[['cards','卡片'],['table','表格']].map(([key,label])=>`<button data-short-layout="${key}" aria-pressed="${shortState.layout===key}" class="${shortState.layout===key?'active':''}">${label}</button>`).join('')}</div></div><div class="short-filter-options">${filters.map(([key,label,title])=>`<label title="${title}"><input type="checkbox" data-short-filter="${key}" ${shortState[key]?'checked':''} ${state.showExcluded?'disabled':''}>${label}</label>`).join('')}</div>${state.showExcluded?'<p class="short-filter-note">正在查看未入选原因，快捷条件暂不叠加。</p>':shortState.mainline?'<p class="short-filter-note">主线口径：当日同分类领涨前三，仅使用已展开且有同日成分的板块。</p>':''}<div class="short-search-row"><input class="search-input" id="stock-search" type="search" placeholder="搜索股票、代码或板块" aria-label="搜索股票" value="${esc(state.search)}"><label>排序<select id="short-sort"><option value="score" ${state.sort==='score'?'selected':''}>规则评分</option><option value="pct_chg" ${state.sort==='pct_chg'?'selected':''}>当日涨幅</option><option value="amount" ${state.sort==='amount'?'selected':''}>成交额</option></select></label><button class="text-button" data-short-reset>重置快捷条件</button></div>${state.sector?`<div class="short-filter-note">当前板块：${esc(a.sectors.find(s=>s.id===state.sector)?.name||state.sector)} <button class="text-button" data-action="clear-sector">清除 ×</button></div>`:''}</section><section class="panel short-results-panel"><div class="panel-heading"><h2>${state.showExcluded?'未入选检查':'短线强势候选'} <span id="short-result-count" class="inline-count"></span></h2><button class="text-button" data-action="csv">导出当前列表 ${icon('download',12)}</button></div>${shortState.layout==='cards'?'<div id="short-stock-results" class="short-stock-grid"></div>':shortTableHTML()}<div id="stock-empty"></div><div class="table-footer"><span id="table-count"></span><span>点击股票看K线 · 加入清单留下次日观察</span></div><div id="short-more"></div></section></div><aside class="short-side"><div id="short-plan-host"></div><section class="panel short-rule-card"><div class="panel-heading"><h2>本轮筛选依据</h2><span class="tag">${esc(styles.find(s=>s[0]===a.config.style)?.[1])}</span></div><div><p>成交额 ≥ <b>${amount(a.config.min_amount)}</b></p><p>换手率 <b>${num(a.config.min_turnover,1)}%—${num(a.config.max_turnover,1)}%</b></p><p>规则评分 ≥ <b>${num(a.config.min_score,0)}</b> · 板块强度 ≥ <b>${num(a.config.min_sector_score,0)}</b></p><p>当前比较${a.config.sector_type==='theme'?'题材':'行业'}；ST、历史不足和关键报价冲突等仍按原规则过滤。</p><button class="text-button" data-short-rules>调整完整筛选规则 ${icon('arrow',12)}</button></div></section></aside></div>`;
}
function shortTableHTML(){return `<div class="table-wrap"><table><thead><tr><th>#</th><th>股票 / 代码</th><th>所属板块</th><th><button class="text-button" data-sort="pct_chg">涨跌幅 ↕</button></th><th><button class="text-button" data-sort="amount">成交额 ↕</button></th><th>换手率</th><th>短线地位</th><th><button class="text-button" data-sort="score">评分 ↕</button></th><th>自选</th></tr></thead><tbody id="stock-rows" data-compact="false"></tbody></table></div>`;}
function shortStockCard(s,index,compact=false){
 const t=s.technical||{},c=s.current||{},planned=shortPlan().codes.includes(s.code);
 const reasons=(s.reasons||[]).slice(0,2),risks=(s.risk_flags||[]).slice(0,2);
 return `<article class="short-stock-card ${s.selected?'':'excluded'}"><div class="short-card-heading"><span class="short-card-rank">${String(index+1).padStart(2,'0')}</span><button class="short-stock-name" data-stock="${esc(s.code)}"><b>${esc(s.name)}</b><small>${esc(s.code)} · ${esc(s.primary_sector||'未分类')}</small></button><span class="short-score"><b>${num(s.score,1)}</b><small>规则分</small></span></div><div class="short-card-price"><strong class="${color(s.pct_chg)}">${num(c.close)}</strong><b class="${color(s.pct_chg)}">${pct(s.pct_chg)}</b><span class="tag ${s.role==='连板观察'?'warm':'mint'}">${esc(s.role||'待观察')}${s.consecutive>0?' · '+s.consecutive+'板':''}</span></div><div class="short-card-metrics"><div><span>成交额</span><b>${amount(c.amount_yuan)}</b></div><div><span>20日均量倍数</span><b>${t.volume_ratio==null?'—':num(t.volume_ratio,2)+'×'}</b></div><div><span>距MA20</span><b>${pct(t.extension20,1)}</b></div></div><div class="short-card-evidence"><span>${s.selected?'强在哪里':'未入选原因'}</span><ul>${(s.selected?reasons:s.exclusions||[]).slice(0,compact?2:3).map(r=>`<li>${esc(r)}</li>`).join('')||'<li>关键依据待补齐，暂不形成完整判断。</li>'}</ul></div>${!compact?`<details class="short-card-conditions"><summary>次日确认与失效条件 ${icon('arrow',11)}</summary><p><b>确认：</b>${esc(s.confirmation||'补齐关键数据后再评估。')}</p><p><b>失效：</b>${esc(s.invalidation||'补齐关键数据后再评估。')}</p><p>当日高 / 低 ${num(c.high)} / ${num(c.low)}，仅作观察参照，除权时需重算。</p></details>`:''}${risks.length?`<p class="short-card-risk">${icon('flag',11)}${esc(risks.join(' · '))}</p>`:''}<div class="short-card-actions"><button class="text-button" data-stock="${esc(s.code)}">K线与评分 ${icon('arrow',12)}</button><button class="button small ${planned?'short-planned':''}" data-short-plan="${esc(s.code)}" aria-pressed="${planned}" ${!s.selected?'disabled':''}>${planned?'已加入观察':'＋ 次日观察'}</button></div></article>`;
}
function paintStrongResults(){
 if(state.view!=='stocks'||!state.analysis)return;
 const rows=filteredStocks(),host=$('#short-stock-results');
 if($('#short-result-count'))$('#short-result-count').textContent=String(rows.length);
 $$('[data-role]').forEach(b=>{b.classList.toggle('active',b.dataset.role===state.role);b.setAttribute('aria-pressed',String(b.dataset.role===state.role));});
 if(!host)return;
 host.innerHTML=rows.slice(0,shortState.limit).map((s,i)=>shortStockCard(s,i)).join('');
 $('#stock-empty').innerHTML=rows.length?'':empty('当前条件下暂无候选','可重置快捷条件，或查看未入选原因；数据缺失的股票不会强行补入。');
 $('#table-count').textContent=`显示 ${Math.min(rows.length,shortState.limit)} / ${rows.length} 只${state.analysis.meta.mode==='demo'?' · 合成演示':''}`;
 $('#short-more').innerHTML=rows.length>shortState.limit?'<button class="button small" data-short-more>显示更多候选</button>':'';
}
function shortPlanHTML(){
 const plan=shortPlan(),lookup=new Map(state.analysis.stocks.map(s=>[s.code,s]));
 return `<section class="panel short-plan-panel"><div class="panel-heading"><h2>${icon('flag',14)} 次日观察清单 <span class="inline-count">${plan.codes.length}/10</span></h2></div><p class="short-plan-date">基于 ${esc(state.analysis.meta.trade_date)} 收盘${state.analysis.meta.mode==='demo'?' · 合成演示':''}<br>${shortState.storageError?'暂存当前页面，请导出保留。':'按日期保存在本机，可跨页查看。'}</p><div class="short-plan-list">${plan.codes.map((code,i)=>{const s=lookup.get(code);return `<div class="short-plan-row"><span>${String(i+1).padStart(2,'0')}</span><button class="text-button" ${s?`data-stock="${esc(code)}"`:''}><b>${esc(s?.name||code)}</b><small>${esc(s?.role||'当前快照无记录')}${s&&!s.selected?' · 当前未入选':''}</small></button><button class="icon-button" data-short-plan="${esc(code)}" aria-label="移除${esc(s?.name||code)}">×</button></div>`;}).join('')||'<div class="short-plan-empty">先选少数值得盯的股票。<br>点击候选卡片的“＋ 次日观察”。</div>'}</div><label class="short-plan-note">明日观察笔记<textarea id="short-plan-note" maxlength="2000" placeholder="例如：主线是否扩散？什么条件确认，什么变化放弃？">${esc(plan.note)}</textarea></label><div class="short-plan-note-state" id="short-note-state">${shortState.storageError?'尚未写入本机，请导出保留。':'本机记录，不自动下单。'}</div><div class="short-plan-footer"><button class="button small" data-short-export ${!plan.codes.length&&!plan.note?'disabled':''}>${icon('download',12)}导出观察清单</button><button class="text-button" data-nav="quant">去策略验证 ${icon('arrow',11)}</button></div></section>`;
}
function paintShortline(){const host=$('#short-plan-host');if(host)host.innerHTML=shortPlanHTML();}
function toggleShortPlan(code){
 const plan=shortPlan(),stock=state.analysis.stocks.find(s=>s.code===code);
 if(plan.codes.includes(code))plan.codes=plan.codes.filter(c=>c!==code);
 else{if(!stock?.selected){toast('当前未入选，请先核验其缺失项或未入选原因。');return;}if(plan.codes.length>=10){toast('观察清单最多10只，请先移除一只。');return;}plan.codes.push(code);}
 saveShortPlan(plan);render();
}
function exportShortPlan(){
 const a=state.analysis,plan=shortPlan(),lookup=new Map(a.stocks.map(s=>[s.code,s]));
 const lines=[`# 短线次日观察清单 · 基于 ${a.meta.trade_date} 收盘`,'',a.meta.mode==='demo'?'合成演示数据，非真实行情。':`数据源：${a.meta.source}`,`范围：${a.meta.universe}`,`风格：${styles.find(s=>s[0]===a.config.style)?.[1]}；记录时当前规则，不代表加入清单当时的参数。`,''];
 plan.codes.forEach((code,i)=>{const s=lookup.get(code);lines.push(`## ${i+1}. ${s?.name||code} ${code}`,s?`${s.role}；收盘 ${num(s.current?.close)}；涨幅 ${pct(s.pct_chg)}；当前${s.selected?'已入选':'未入选'}。`:'当前快照无此股票记录。',`依据：${s?.reasons?.join('；')||'待核验'}`,`确认：${s?.confirmation||'待核验'}`,`失效：${s?.invalidation||'待核验'}`,`风险：${s?.risk_flags?.join('；')||'待核验'}`,'');});
 lines.push('## 明日观察笔记',plan.note||'尚未填写。','','价格为收盘观察参照，除权时需重新校准；观察清单不自动下单。');
 download(`短线观察清单-${a.meta.trade_date}${a.meta.mode==='demo'?'-演示':''}.md`,lines.join('\n'));toast('观察清单已导出');
}
document.addEventListener('click',event=>{
 const b=event.target.closest('button');if(!b)return;
 if(b.dataset.shortPlan){toggleShortPlan(b.dataset.shortPlan);return;}
 if(b.dataset.shortLayout){if(['cards','table'].includes(b.dataset.shortLayout)){shortState.layout=b.dataset.shortLayout;render();}return;}
 if(b.hasAttribute('data-short-export')){exportShortPlan();return;}
 if(b.hasAttribute('data-short-reset')){Object.assign(shortState,{mainline:false,breakout:false,avoidExtended:false,matched:false,limit:24});state.role='all';state.search='';state.sector=null;state.watchOnly=false;render();return;}
 if(b.hasAttribute('data-short-more')){shortState.limit+=24;paintStrongResults();return;}
 if(b.hasAttribute('data-short-rules')){$('#filter-panel').hidden=false;$('#filter-panel').scrollIntoView({behavior:'smooth',block:'center'});}
});
document.addEventListener('change',event=>{
 const el=event.target;
 if(el.dataset.shortFilter&&['mainline','breakout','avoidExtended','matched'].includes(el.dataset.shortFilter)){shortState[el.dataset.shortFilter]=el.checked;shortState.limit=24;render();}
 if(el.id==='short-sort'){if(['score','pct_chg','amount'].includes(el.value)){state.sort=el.value;state.sortDirection=-1;renderTable();}}
});
document.addEventListener('input',event=>{
 if(event.target.id==='short-plan-note'){const plan=shortPlan();plan.note=event.target.value.slice(0,2000);const saved=saveShortPlan(plan);$('#short-note-state').textContent=saved?'笔记已保存在本机':'尚未保存，请导出后保留';const button=$('[data-short-export]');if(button)button.disabled=!plan.codes.length&&!plan.note;}
});
