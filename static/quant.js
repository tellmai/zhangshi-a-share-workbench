'use strict';
const quantState={catalog:null,config:null,name:'',strategyId:null,version:null,savedConfig:null,savedName:null,
 result:null,tab:'result',detailTab:'trades',loading:false,pending:false,error:null,loadedAt:0,timer:null,seenJob:null,start:'',end:'',
 selected:[],stockQuery:'',stockOptions:null,stockSearchLoading:false,stockSearchError:'',stockSearchSequence:0,stockTimer:null,bulkText:'',autoHistory:true,activeStock:null,
 readiness:null,readinessPending:false,readinessError:'',readinessSequence:0,readinessTimer:null,readinessKey:''};
const qLabels={scan:'当日筛选',backtest:'组合历史回测',stock_backtest:'指定股票回测',completed:'已完成',partial:'部分完成',skipped:'历史不足',incomplete:'数据中止',error:'运行失败',cancelled:'已取消'};
const qExport=(format,id=quantState.result?.id)=>`/api/quant/export?id=${encodeURIComponent(id||'')}&format=${format}`;
const qBusy=()=>quantState.pending||quantState.catalog?.job.state==='running';
function qDirty(){return !quantState.savedConfig||JSON.stringify(quantState.config)!==quantState.savedConfig||quantState.name!==quantState.savedName;}
function qHistoryBlocked(c=quantState.config){return c&&(c.min_boards>0||c.sector_min>0||c.ranking==='relay');}
function qInput(label,key,{unit=1,suffix='',min=0,max=100,step=1}={}){const c=quantState.config;return `<label>${esc(label)}<span class="q-input-wrap"><input type="number" data-q-key="${key}" data-q-unit="${unit}" value="${Number((c[key]/unit).toFixed(8))}" min="${min}" max="${max}" step="${step}" required>${suffix?`<small>${esc(suffix)}</small>`:''}</span></label>`;}
function qCheck(label,key,note=''){return `<label class="q-check"><input type="checkbox" data-q-key="${key}" ${quantState.config[key]?'checked':''}><span>${esc(label)}${note?`<small>${esc(note)}</small>`:''}</span></label>`;}
function qSelect(label,key,options){return `<label>${esc(label)}<select data-q-key="${key}">${options.map(([v,n])=>`<option value="${esc(v)}" ${String(quantState.config[key])===String(v)?'selected':''}>${esc(n)}</option>`).join('')}</select></label>`;}
function quantView(){
 const q=quantState,d=q.catalog?.data,c=q.config;
 if(!c)return `<section class="q-loading panel"><span class="spinner"></span><h2>准备策略实验室</h2><p>${esc(q.error||'正在读取策略模板与数据范围…')}</p>${q.error?'<button class="button" data-q-action="refresh">重新读取</button>':''}</section>`;
 const busy=qBusy();
 return `<div class="quant-workspace">
  <section class="q-hero"><div><div class="eyebrow">STRATEGY LAB / RESEARCH FIRST</div><h2>把交易想法，变成能检验的规则。</h2><p>选一个模板开始，先看信号，再看回测，最后用新数据验证。</p></div><span class="q-hero-mark">${icon('experiment',34)}<small>本地研究</small></span></section>
  <details class="q-guide" open><summary><span>${icon('spark',16)}第一次跑量化，从这四步开始</span><small>查看说明</small></summary><div class="q-steps">${[['01','设计策略','把“强势”写成具体条件，例如突破、量能、均线。'],['02','运行筛选','用当前收盘数据，查哪些股票同时满足条件。'],['03','历史验证','按过去逐日重演，查看费用后收益和回撤。'],['04','回看与跟踪','保存参数，用以后几天的新行情检验，避免反复调到历史最好。']].map(([n,t,p])=>`<div><b>${n}</b><h3>${t}</h3><p>${p}</p></div>`).join('')}</div></details>
  <div class="q-data-strip"><div><i class="${d?.mode==='demo'?'demo':''}"></i><b>${d?.mode==='demo'?'合成演示':'真实快照'} · ${esc(d?.trade_date)}</b><span>范围内${d?.universe||0}只 · 历史≥60根 ${d?.history_60||0}只</span></div><label>数据<select id="q-mode" ${busy?'disabled':''}><option value="active" ${state.mode!=='demo'?'selected':''}>当前真实快照</option><option value="demo" ${state.mode==='demo'?'selected':''}>合成演示 · 学习使用</option></select></label><button class="button small" data-q-action="refresh" ${q.loading?'disabled':''}>${icon('refresh',14)}读取最新</button><button class="button primary small" data-q-action="scan" ${busy?'disabled':''}>运行筛选 ${icon('arrow',14)}</button></div>
  <div class="q-scope-note">${icon('shield',15)}<span>${esc(d?.sample_warning||'数据范围待确认')}。先用小样本理解规则，完整历史和逐日股票池补齐后再扩大研究。</span></div>
  <section class="q-presets" aria-label="策略模板">${Object.entries(q.catalog.presets).map(([key,p])=>`<button class="q-preset ${c.template===key?'active':''}" data-q-template="${key}" ${busy?'disabled':''}><span>${icon(key==='relay'?'layers':key==='pullback'?'radar':'chart',17)}<small>${esc(p.tag)}</small></span><b>${esc(p.name)}</b><p>${esc(p.description)}</p><i>${c.template===key?'当前模板':'使用模板'} ${icon('arrow',13)}</i></button>`).join('')}</section>
  ${qStockPickerHTML()}
  <div class="q-layout"><section class="panel q-designer"><div class="q-panel-heading"><div><small>01 / DESIGN</small><h2>策略设计器</h2></div><span class="tag" id="q-version-label">${qDirty()?'未保存参数':`已保存 v${q.version}`}</span></div>${qDesignerHTML()}
  <div class="q-run-box"><div class="q-run-note" id="q-run-note">${qHistoryBlocked()?'连板或板块条件缺少逐日历史，目前可运行当日筛选。':'收盘后产生信号，下一交易日模拟执行。'}</div><button class="button primary wide" id="q-run-scan" data-q-action="scan" ${busy?'disabled':''}>${icon('filter',16)}运行当日筛选</button><button class="button wide q-backtest-button" id="q-run-backtest" data-q-action="backtest" ${busy||qHistoryBlocked()?'disabled':''}>${icon('chart',16)}运行历史回测</button><p id="q-window">回放区间 ${esc(q.start)} 至 ${esc(q.end)}</p><small>运行只生成本机研究结果，实际交易需要另行判断。</small></div></section>
  <div class="q-results"><section class="q-job ${q.catalog.job.state==='error'?'error':''}" role="status" aria-live="polite"><div>${busy?'<span class="spinner"></span>':icon('shield',18)}<span><b>${busy?'策略正在运行':'研究运行台'}</b><small>${esc(q.catalog.job.message)}</small></span>${busy?'<button class="button small" data-q-action="cancel">取消运行</button>':'<span class="q-engine">引擎 '+esc(q.catalog.engine_version)+'</span>'}</div>${busy?`<div class="q-progress"><i style="width:${Math.max(1,q.catalog.job.progress||0)}%"></i></div>`:''}</section>
  ${q.error?`<div class="q-error-result">${esc(q.error)}<button class="button small" data-q-action="refresh">重新读取</button></div>`:''}<section class="panel q-output" id="q-output"><div class="q-output-tabs" role="tablist">${[['result','运行结果'],['rules','规则与数据'],['history','历史记录']].map(([key,label])=>`<button class="${q.tab===key?'active':''}" data-q-tab="${key}" role="tab" aria-selected="${q.tab===key}">${label}${key==='history'?`<small>${q.catalog.history.length}</small>`:''}</button>`).join('')}</div>${q.tab==='history'?qHistoryHTML():q.tab==='rules'?qRulesHTML():qResultHTML()}</section>
  </div></div></div>`;
}
function qDesignerHTML(){
 const q=quantState,c=q.config,strategies=q.catalog.strategies;
 return `<form id="q-config-form" class="q-config-form"><fieldset ${qBusy()?'disabled':''}><label>我的策略库<select id="q-strategy"><option value="">选择已保存的策略…</option>${strategies.map(s=>`<option value="${esc(s.id)}" ${s.id===q.strategyId?'selected':''}>${esc(s.name)} · ${s.versions.length}个版本</option>`).join('')}</select></label>
 ${q.strategyId?`<label>参数版本<select id="q-version">${strategies.find(s=>s.id===q.strategyId)?.versions.slice().reverse().map(v=>`<option value="${v.version}" ${v.version===q.version?'selected':''}>v${v.version} · ${esc(v.saved_at.slice(0,16).replace('T',' '))}</option>`).join('')||''}</select></label>`:''}
 <label>策略名称<input id="q-name" maxlength="40" value="${esc(q.name)}" placeholder="为这套规则起个名字"></label><div class="q-save-row"><button class="button small" type="button" data-q-action="save">${q.strategyId?'保存新版本':'保存策略'}</button><button class="q-text-button" type="button" data-q-action="export-config">导出参数</button></div>
 <details class="q-form-section" open><summary><span>入场条件<small>同时满足以下启用项</small></span>${icon('filter',14)}</summary><div class="q-field-grid">
 ${qInput('最低成交额','min_amount',{unit:1e8,suffix:'亿元',max:10000,step:.1})}${qInput('5日涨幅下限','min_return5',{suffix:'%',min:-50,max:100,step:1})}
 ${qInput('20日均量倍数下限','min_volume_ratio',{suffix:'倍',max:30,step:.1})}${qInput('20日均量倍数上限','max_volume_ratio',{suffix:'倍',max:30,step:.1})}
 ${qInput('距MA20最大偏离','max_extension20',{suffix:'%',max:200})}${qInput('最低收盘位置','close_position_min',{unit:.01,suffix:'%',max:100,step:5})}</div><p class="q-field-hint">收盘位置：收盘价在当天高低区间中的位置。均量倍数不包含当日，区别于分时量比。</p>
 ${qCheck('均线顺向排列','ma_alignment','收盘 > MA5 > MA10 > MA20')}${qCheck('20日均线向上','ma20_rising','与5根之前的20日均线比较')}
 <div class="q-field-grid">${qSelect('突破前期高点','breakout_days',[[0,'关闭'],[10,'前10根'],[20,'前20根'],[40,'前40根'],[60,'前60根']])}${qSelect('回踩均线','pullback_ma',[[0,'关闭'],[5,'MA5'],[10,'MA10'],[20,'MA20']])}
 ${qInput('回踩允许距离','pullback_distance_pct',{suffix:'±%',max:30,step:.5})}${qInput('最低已核验连板','min_boards',{suffix:'板',max:20})}</div></details>
 <details class="q-form-section" open><summary><span>资金与退出<small>历史回测使用</small></span>${icon('shield',14)}</summary><div class="q-field-grid">${qInput('研究资金','capital',{suffix:'元',min:1000,max:1e8,step:1000})}${qInput('最多持仓','max_positions',{suffix:'只',min:1,max:20})}${qInput('持有交易日','holding_days',{suffix:'日',min:1,max:30})}${qInput('止损阈值','stop_loss_pct',{suffix:'%',max:50,step:.5})}${qInput('止盈阈值','take_profit_pct',{suffix:'%',max:200,step:1})}</div><p class="q-field-hint">资金按最大持仓数分配，按100股取整；止盈止损设0为关闭。新进入的仓位最早下一交易日退出。</p></details>
 <details class="q-form-section"><summary><span>回测区间与费用</span>${icon('clock',14)}</summary><div class="q-field-grid"><label>起始交易日<input id="q-start" type="date" value="${esc(q.start)}" max="${esc(q.catalog.data.trade_date)}"></label><label>结束交易日<input id="q-end" type="date" value="${esc(q.end)}" max="${esc(q.catalog.data.trade_date)}"></label>
 ${qInput('佣金假设','commission_bps',{suffix:'bp',max:30,step:.1})}${qInput('最低单笔佣金','minimum_commission',{suffix:'元',max:100,step:1})}${qInput('卖出印花税','stamp_bps',{suffix:'bp',max:100,step:.1})}${qInput('额外过户费','transfer_bps',{suffix:'bp',max:10,step:.1})}${qInput('每边滑点成本','slippage_bps',{suffix:'bp',max:100,step:.5})}</div><p class="q-field-hint">1bp = 0.01%。费率是研究假设，应按自己的费用设置；佣金已含过户费时，额外过户费可设0。区间最多252个已保存交易日。</p>${qCheck('严格核对历史涨跌停价','strict_limits','缺失时跳过模拟成交，可能没有交易')}</details>
 <details class="q-form-section"><summary><span>高级筛选与排序</span>${icon('layers',14)}</summary><div class="q-field-grid">${qInput('最低换手率','min_turnover',{suffix:'%',max:100,step:.5})}${qInput('最高换手率','max_turnover',{suffix:'%',max:100,step:.5})}${qInput('最低历史根数','min_bars',{suffix:'根',min:21,max:250})}${qInput('最低板块强度','sector_min',{suffix:'分',max:100})}${qSelect('板块分类','sector_type',[['industry','行业'],['theme','题材']])}${qSelect('排序视角','ranking',[['trend','趋势结构'],['momentum','量价动量'],['pullback','回踩位置'],['relay','连板地位']])}</div><p class="q-field-hint">排序分用于比较规则信号，不代表胜率。板块与连板仅有当前快照时，相关条件支持当日筛选。</p></details>
 </fieldset></form>`;
}
function qStockPickerHTML(){
 const q=quantState,busy=qBusy(),blocked=qHistoryBlocked();
 return `<section class="panel q-stock-picker" aria-label="指定股票回测"><div class="q-stock-picker-head"><div><div class="eyebrow">TEST YOUR STOCKS</div><h2>${icon('radar',20)}指定股票回测</h2><p>选一只深入研究，或选多只逐股对比。使用下方策略设计器的当前规则。</p></div><span class="q-stock-count">${q.selected.length}<small>/ 20只</small></span></div>
 <fieldset ${busy?'disabled':''}><div class="q-stock-picker-grid"><div class="q-stock-selection"><label for="q-stock-search">搜索股票名称或代码</label><div class="q-stock-search-row"><input id="q-stock-search" type="search" value="${esc(q.stockQuery)}" placeholder="例如：上海九百 / 600838" maxlength="80" autocomplete="off"><button class="button small" data-q-action="search-stocks">搜索</button></div><div id="q-stock-options" class="q-stock-options" aria-live="polite">${qStockOptionsHTML()}</div>
 <div class="q-stock-selected">${q.selected.map(s=>`<span class="q-stock-chip"><b>${esc(s.name)}</b><small>${esc(s.code)}</small><button data-q-remove="${esc(s.code)}" aria-label="移除${esc(s.name)}" title="移除">×</button></span>`).join('')||'<p>还没有选择股票。搜索后添加，或批量粘贴代码。</p>'}</div>${q.selected.length?'<button class="q-text-button" data-q-action="clear-stocks">清空所选</button>':''}
 <div id="q-stock-readiness" class="q-stock-readiness" aria-live="polite">${qReadinessHTML()}</div>
 <details class="q-bulk"><summary>批量添加股票代码 / 完整名称</summary><textarea id="q-stock-bulk" rows="2" maxlength="1000" placeholder="600838, 000001, 300750（逗号、空格或换行分隔）">${esc(q.bulkText)}</textarea><button class="button small" data-q-action="add-bulk">添加到所选</button><small>最多20只；有未识别内容时会提示，核对后再添加。</small></details></div>
 <div class="q-stock-run"><label>起始日期<input id="q-stock-start" type="date" value="${esc(q.start)}" max="${esc(q.catalog.data.trade_date)}" required></label><label>结束日期<input id="q-stock-end" type="date" value="${esc(q.end)}" max="${esc(q.catalog.data.trade_date)}" required></label><label class="q-stock-auto"><input id="q-auto-history" type="checkbox" ${q.autoHistory?'checked':''} ${q.catalog.data.mode==='demo'?'disabled':''}><span>回测前自动补齐历史 · 推荐<small>${q.catalog.data.mode==='demo'?'演示数据不请求真实接口':'运行时补足区间与预热数据，校验后缓存。搜索和选股只检查本机数据。'}</small></span></label>
 <div class="q-stock-account"><small>每只股票的独立研究资金</small><b id="q-stock-capital">${num(q.config.capital,0)}<small>元 / 只</small></b><p>每个账户只交易本股，不共享资金。“最多持仓”在此固定为1只，组合回测仍按原设置。</p></div><button id="q-run-stocks" class="button primary" data-q-action="stock-backtest" ${busy||blocked||!q.selected.length?'disabled':''}>${icon('chart',16)}${q.autoHistory?'运行所选股票回测 · 自动准备数据':'仅用本机缓存回测'}</button><p id="q-stock-run-note" class="q-stock-run-note">${blocked?'当前连板或板块条件缺少逐日历史，请更换模板或关闭相关条件。':`使用「${esc(q.name)}」${qDirty()?'的当前参数快照':`v${q.version}`}；每只股票都会保留一份结果。`}</p></div></div></fieldset></section>`;
}
function qReadinessHTML(){
 const q=quantState,d=q.readiness;if(!q.selected.length)return '';
 if(qBusy())return '<p>运行中会再次检查所选股票，完成数据准备后计算策略。</p>';
 if(q.readinessPending)return '<p>正在检查区间、预热日线和复权数据…</p>';
 if(q.readinessError)return `<p class="q-stock-search-error">${esc(q.readinessError)}</p><button class="q-text-button" data-q-action="check-data">重新检查</button>`;
 if(!d||q.readinessKey!==qDataCheckKey())return '<p>运行前会检查所选股票的数据。</p>';
 return `<div class="q-ready-heading"><b>数据准备 ${d.ready}/${d.total}</b><button class="q-text-button" data-q-action="check-data">重新检查</button></div>${d.stocks.map(s=>`<div class="q-ready-stock ${s.status==='ready'?'ready':'missing'}"><span><b>${esc(s.name)}</b><small>${s.history_bars}根历史 · 起点可用${s.coverage.warmup_bars}/${s.coverage.required_bars}根</small></span><em>${s.status==='ready'?'已就绪':q.autoHistory?'运行时补齐':'缓存不足'}</em><p>${esc(s.message)}${s.earliest_start&&s.status!=='ready'?`；已有历史最早可预热的起点为${esc(s.earliest_start)}`:''}</p></div>`).join('')}<p class="q-ready-note ${q.autoHistory?'':'offline'}">${d.ready===d.total?'当前本机数据满足覆盖要求，运行时会再次核验。':q.autoHistory?'点击运行后自动下载缺失历史；验证通过后缓存复用，失败会逐股说明。':'自动补齐已关闭：本次只用本机数据，覆盖不足的股票将跳过。'}</p>`;
}
function qDataCheckKey(){const q=quantState;return JSON.stringify([state.mode,q.catalog?.data.trade_date,q.selected.map(s=>s.code),q.config,q.start,q.end]);}
function qPaintReadiness(){const n=$('#q-stock-readiness');if(n)n.innerHTML=qReadinessHTML();}
function qScheduleDataCheck(){
 const q=quantState;++q.readinessSequence;clearTimeout(q.readinessTimer);q.readiness=null;q.readinessError='';q.readinessKey='';q.readinessPending=q.selected.length>0&&!qBusy();qPaintReadiness();
 if(q.readinessPending)q.readinessTimer=setTimeout(qCheckStocks,300);
}
async function qCheckStocks(){
 const q=quantState;if(!q.selected.length||qBusy())return;
 const sequence=++q.readinessSequence,key=qDataCheckKey();q.readinessPending=true;q.readinessError='';qPaintReadiness();
 try{const data=await api('/api/quant/stocks/check',{mode:state.mode,codes:q.selected.map(s=>s.code),config:{...q.config},start:q.start,end:q.end});
  if(sequence!==q.readinessSequence||key!==qDataCheckKey())return;q.readiness=data;q.readinessKey=key;
 }catch(error){if(sequence===q.readinessSequence)q.readinessError=error.message;}
 finally{if(sequence===q.readinessSequence){q.readinessPending=false;qPaintReadiness();}}
}
function qStockOptionsHTML(){
 const q=quantState;
 if(q.stockSearchLoading)return '<p class="q-muted">正在查找当前快照中的股票…</p>';
 if(q.stockSearchError)return `<p class="q-stock-search-error">${esc(q.stockSearchError)}</p>`;
 if(q.stockOptions===null)return '<p class="q-muted">按名称或代码查找，也可点击搜索查看已有历史的股票。</p>';
 if(!q.stockOptions.length)return '<p class="q-muted">当前快照内没有匹配股票；范围为沪深主板、创业板，排除当前ST及退市名称。</p>';
 return q.stockOptions.map(s=>`<button class="q-stock-option" data-q-add="${esc(s.code)}" ${q.selected.some(x=>x.code===s.code)||qBusy()?'disabled':''}><span><b>${esc(s.name)}</b><small>${esc(s.code)}</small></span><span><small>历史 ${s.history_bars}根</small><em>${q.selected.some(x=>x.code===s.code)?'已添加':'+ 添加'}</em></span></button>`).join('');
}
function qSelectedResult(){const r=quantState.result;return r?.type==='stock_backtest'?r.results?.find(s=>s.code===quantState.activeStock)||r.results?.[0]:r;}
function qStockFailureHTML(s,r){
 if(!s.failure)return '';
 const receipt=r.history_preparation?.find(p=>p.code===s.code);
 const message=receipt?.status==='failed'?'历史补齐未完成，请查看来源失败记录。':s.coverage.history_bars<=1?'本机只有当日报价，尚未加载本次所需的历史。':s.failure;
 return `<small class="q-stock-failure">${esc(message)}</small>${message!==s.failure?`<details class="q-stock-failure-proof"><summary>数据检查详情</summary><p>${esc(s.failure)}</p>${receipt?.message?`<p>${esc(receipt.message)}</p>`:''}</details>`:''}`;
}
function qStockResultsHTML(r){
 const q=quantState,summary=r.summary,selected=qSelectedResult();
 return `<div class="q-stock-results-intro"><h3>同一套规则，逐只检验。</h3><p>每股独立初始资金 <b>${num(r.capital_per_stock,0)}元</b> · 同一区间 · 不共享资金</p><span>${summary.completed}/${summary.selected}只完成</span><small>${summary.skipped}只历史不足 · ${summary.incomplete+summary.errors}只中止或失败。各股收益不能相加作为组合收益。</small>${summary.skipped?`<div class="q-history-repair"><p>${r.request.auto_history?'部分股票的历史仍未满足要求，可查看补齐记录并重试。':'这次仅使用已有缓存。新运行可以自动补齐历史，保留原策略和日期。'}</p><button class="button small" data-q-action="repair-history" ${qBusy()?'disabled':''}>补齐历史并重新回测</button></div>`:''}</div>
 <div class="table-wrap q-stock-comparison"><table><thead><tr><th>股票 / 状态</th><th>区间收益</th><th>最大回撤</th><th>已退出 / 胜率</th><th>持有观察收益</th><th></th></tr></thead><tbody>${r.results.map(s=>{const m=s.metrics;return `<tr class="${selected?.code===s.code?'active':''}"><td><b>${esc(s.name)}</b><small>${esc(s.code)} · ${esc(qLabels[s.status])}</small>${qStockFailureHTML(s,r)}</td><td class="${m?color(m.total_return_pct):''}">${m?pct(m.total_return_pct):'—'}</td><td>${m?num(m.max_drawdown_pct)+'%':'—'}</td><td>${m?`${m.closed_trades}笔<small>${m.win_rate_pct==null?'无已退出交易':num(m.win_rate_pct,1)+'%'}</small>`:'—'}</td><td class="${m?color(m.reference_return_pct):''}">${m?.reference_return_pct!=null?pct(m.reference_return_pct):'—'}</td><td><button class="button small" data-q-stock-result="${esc(s.code)}" aria-pressed="${selected?.code===s.code}">${selected?.code===s.code?'当前':'详情'}</button></td></tr>`;}).join('')}</tbody></table></div>
 ${selected?`<section class="q-stock-detail"><div class="q-stock-detail-heading"><div><small>STOCK / INDEPENDENT ACCOUNT</small><h3>${esc(selected.name)} <span>${esc(selected.code)}</span></h3></div><span class="tag">${esc(qLabels[selected.status])}</span></div><p class="q-stock-coverage">已存历史 ${selected.coverage.history_bars}根 · ${esc(selected.coverage.first_date||'—')} 至 ${esc(selected.coverage.last_date||'—')}<br>区间起点可用 ${selected.coverage.warmup_bars}根 / 需要 ${selected.coverage.required_bars}根；区间缺报价 ${selected.coverage.missing_sessions}日。</p>
 ${selected.failure?`<div class="q-error-result">${esc(selected.failure)}</div>`:''}${selected.curve?qReplayHTML({...selected,id:r.id+'-'+selected.code.replace('.','-')}):'<div class="q-zero">本股没有完整区间指标。可尝试补齐历史，或调整区间后重新运行。</div>'}
 ${selected.entry_exclusions?`<details class="q-stock-diagnostics"><summary>哪些规则没有通过？ · ${selected.signal_days}个收盘日触发信号</summary><p>各交易日按第一项未通过的条件计数；触发信号也可能因持仓、资金或成交限制而没有新交易。</p>${qCountBars(selected.entry_exclusions)}</details>`:''}</section>`:''}
 ${r.history_preparation?.length?`<details class="q-stock-preparation" ${r.history_preparation.some(p=>p.status==='failed')?'open':''}><summary>本次历史补齐记录</summary>${r.history_preparation.map(p=>`<p>${esc(p.code)} · ${p.status==='fetched'?`${esc(p.source)}，${p.bars}根${p.cache_message?'；'+esc(p.cache_message):''}`:esc(p.message)}</p>`).join('')}<small>补齐后的校验数据已随本次输入冻结，复现不会重新请求行情。</small></details>`:''}<div class="q-result-notes">${r.warnings.map(w=>`<p>${esc(w)}</p>`).join('')}<p>CSV导出逐股汇总；完整JSON包含每只股票的曲线、模拟订单、已退出交易与期末持仓。</p></div>`;
}
function qRestoreStocks(r){
 const codes=r.request?.codes||[];quantState.selected=codes.map(code=>{
  const s=r.results?.find(s=>s.code===code);return {code,name:s?.name||code,history_bars:s?.coverage.history_bars||0};
 });quantState.autoHistory=true;quantState.activeStock=null;qScheduleDataCheck();
}
async function qSearchStocks(){
 const q=quantState,query=q.stockQuery,mode=state.mode,sequence=++q.stockSearchSequence;
 q.stockSearchLoading=true;q.stockSearchError='';const paint=()=>{const node=$('#q-stock-options');if(node)node.innerHTML=qStockOptionsHTML();};paint();
 try{const response=await api(`/api/quant/stocks?mode=${encodeURIComponent(mode)}&q=${encodeURIComponent(query)}`);if(sequence!==q.stockSearchSequence||mode!==state.mode)return;q.stockOptions=response.stocks;}
 catch(error){if(sequence===q.stockSearchSequence)q.stockSearchError=error.message;}
 finally{if(sequence===q.stockSearchSequence){q.stockSearchLoading=false;paint();}}
}
function qAddStocks(stocks){
 const rows=[...quantState.selected];for(const s of stocks)if(!rows.some(x=>x.code===s.code))rows.push(s);
 if(rows.length>20){toast('一次最多选择20只股票，请先减少所选股票');return false;}
 quantState.selected=rows;qScheduleDataCheck();paintQuant();return true;
}
function qReuseRun(r){const q=quantState;q.config={...r.request.config};q.name=r.strategy_name;q.strategyId=null;q.version=null;q.savedConfig=null;q.savedName=null;if(r.request.start)q.start=r.request.start;if(r.request.end)q.end=r.request.end;qRestoreStocks(r);}
function qEmptyHTML(){return `<div class="q-empty"><span>${icon('experiment',36)}</span><h2>让一套规则，完成第一次运行。</h2><p>建议先保持默认参数，点击“运行当日筛选”。<br>熟悉每只股票为什么入选后，再运行历史回测。</p><div><b>筛选回答“谁符合规则”</b><b>回测回答“过去表现如何”</b></div><small>当前可用历史 ${quantState.catalog.data.history_60}只，历史结果受样本范围影响。</small></div>`;}
function qResultHTML(){
 const r=quantState.result;if(!r)return qEmptyHTML();
 const demo=r.mode==='demo';
 return `<div class="q-result-heading"><div><span class="tag ${demo?'blue':'mint'}">${demo?'合成演示 · 非真实收益':'真实数据 · 当前样本'}</span><span class="tag ${r.status==='completed'?'':'warm'}">${esc(qLabels[r.status])}</span><h2>${esc(r.strategy_name)}<small>${r.strategy_version?`v${r.strategy_version}`:'参数快照'}</small></h2><p>${esc(qLabels[r.type])} · ${r.type==='scan'?esc(r.trade_date):`${esc(r.start||r.request.start)} 至 ${esc(r.end||r.request.end)}`}</p></div><div class="q-result-download"><a class="button small" href="${qExport('csv')}" download="策略结果-${r.id}.csv">CSV</a><a class="button small" href="${qExport('json')}" download="策略结果-${r.id}.json">完整JSON</a></div></div>
 ${demo?'<div class="q-demo-banner">当前是合成演示，股票、价格和收益均为虚构，只用于理解操作。</div>':''}
 ${r.failure||r.status==='cancelled'?`<div class="q-error-result">${icon('flag',18)}<div><b>${r.status==='cancelled'?'运行已取消':'本次结果需要处理数据问题'}</b><p>${esc(r.failure||r.message)}</p></div></div>`:''}
 ${r.type==='scan'&&r.funnel?qScanHTML(r):r.type==='backtest'&&r.curve?qReplayHTML(r):r.type==='stock_backtest'&&r.results?qStockResultsHTML(r):''}`;
}
function qScanHTML(r){
 const f=r.funnel;
 return `<div class="q-funnel">${[['行情记录',f.total],['市场范围',f.universe],['历史与量价可用',f.history_ready],['通过策略',f.passed]].map(([k,v],i)=>`<div class="${i===3?'last':''}"><small>${k}</small><b>${v}</b></div>`).join('')}</div><div class="q-caption">以下为收盘后信号，前${r.shortlist.length}只列入观察。排序分不是上涨概率。</div>
 ${r.signals.length?`<div class="table-wrap"><table class="q-signal-table"><thead><tr><th>排序 / 股票</th><th>规则分</th><th>收盘涨幅</th><th>5日涨幅</th><th>20日均量倍数</th></tr></thead><tbody>${r.signals.slice(0,100).map((s,i)=>`<tr><td><div class="q-stock-name"><span>${String(i+1).padStart(2,'0')}</span><div><b>${esc(s.name)}${i<r.shortlist.length?'<i>观察</i>':''}</b><small>${esc(s.code)}</small></div></div><details class="q-signal-proof"><summary>入选依据</summary><p>${qSignalProof(s,r.config).map(esc).join('<br>')}</p></details></td><td><b class="q-score">${num(s.score,1)}</b></td><td class="${color(s.features.pct)}">${pct(s.features.pct)}</td><td class="${color(s.features.return5)}">${pct(s.features.return5)}</td><td>${num(s.features.volume_ratio,2)} ×</td></tr>`).join('')}</tbody></table></div>`:'<div class="q-zero"><b>当前没有股票同时满足这套规则。</b><p>空结果也有价值。先查看未通过原因，再决定是否调整条件。</p></div>'}
 <div class="q-exclusion-block"><h3>哪些条件挡住了其他股票？</h3>${qCountBars(r.exclusions)}<details><summary>查看未通过示例</summary>${r.rejected_examples.map(s=>`<p><b>${esc(s.name)}</b><small>${esc(s.code)}</small><span>${esc(s.reasons.join('；'))}</span></p>`).join('')}</details></div><div class="q-result-notes">${r.warnings.map(n=>`<p>${esc(n)}</p>`).join('')}</div>`;
}
function qSignalProof(s,c){const f=s.features,lines=[`成交额${num(f.amount/1e8,2)}亿元，换手${num(f.turnover,2)}%`,`均量倍数${num(f.volume_ratio,2)}，收盘位置${num(f.close_position*100,1)}%`,`距MA20 ${pct(f.extension20)}，20日涨幅${pct(f.return20)}`];if(c.ma_alignment)lines.push(`收盘${num(f.close)} > MA5 ${num(f.ma5)} > MA10 ${num(f.ma10)} > MA20 ${num(f.ma20)}`);if(c.ma20_rising)lines.push('MA20高于5根之前');if(c.breakout_days)lines.push(`已突破前${c.breakout_days}根最高价`);if(c.pullback_ma)lines.push(`距MA${c.pullback_ma} ${pct(f.distance_ma)}`);if(c.min_boards)lines.push(`已核验连续涨停${s.boards}板`);if(c.sector_min)lines.push(`板块强度${num(s.sector_score,1)}`);return lines;}
function qCountBars(counts){
 const entries=Object.entries(counts||{}).sort((a,b)=>b[1]-a[1]);const max=Math.max(1,...entries.map(x=>x[1]));
 return entries.slice(0,8).map(([name,count])=>`<div class="q-count-row"><span>${esc(name)}</span><i><b style="width:${count/max*100}%"></b></i><strong>${count}</strong></div>`).join('')||'<p class="q-muted">没有记录到跳过情况。</p>';
}
function qReplayHTML(r){
 const m=r.metrics;
 const cards=m?[[`区间收益`,pct(m.total_return_pct),color(m.total_return_pct)],['最大回撤',`${num(m.max_drawdown_pct)}%`,''],['已闭合交易胜率',m.win_rate_pct==null?'—':`${num(m.win_rate_pct,1)}%`,''],['已闭合交易',`${m.closed_trades}笔`,'']]:[['区间收益','未计算',''],['最大回撤','未计算',''],['胜率','未计算',''],['区间状态','数据中止','']];
 return `<div class="q-replay-warning">研究性样本回放。日线无法完整还原涨跌停排队与盘中路径，结果不等于可实现收益。</div><div class="q-metric-grid">${cards.map(([k,v,cl])=>`<div><small>${esc(k)}</small><b class="${cl}">${esc(v)}</b></div>`).join('')}</div>
 <div class="q-chart-box"><div class="q-chart-header"><h3>资金曲线<small>${r.sample_count}只历史样本 · 计算至${esc(r.computed_through)}</small></h3><span class="q-chart-readout" id="q-chart-readout">初始研究资金 ${num(r.config.capital,0)}元</span></div>${qEquityChart(r)}<div class="q-chart-legend"><span><i></i>策略净值 · 含设置的费用</span><span><i class="reference"></i>${r.reference_count===0?'起点无有效报价，持有观察线未计算':r.code?'本股持有观察线 · 复权、不计费':'同样本等权观察线 · 不计费，非指数'}</span></div></div>
 <div class="q-secondary-metrics"><span>费用与滑点<b>${num(m?.total_fees??Object.values(r.fees).reduce((a,b)=>a+b,0),2)}元</b></span><span>期末未平仓<b>${r.open_positions.length}只</b></span><span>期末待观察信号<b>${r.pending_signals}只</b></span>${m?`<span>期末总资产<b>${num(m.final_equity,2)}元</b></span>`:''}</div>
 <div class="q-trade-tabs">${[['trades','模拟成交'],['skips','未成交原因'],['positions','期末持仓']].map(([key,label])=>`<button class="pill ${quantState.detailTab===key?'active':''}" data-q-detail="${key}">${label}</button>`).join('')}</div>
 ${quantState.detailTab==='skips'?`<div class="q-skips">${qCountBars(r.skips)}<div class="q-fee-breakdown">${[['commission','佣金'],['stamp','印花税'],['transfer','过户费'],['slippage','滑点成本']].map(([key,label])=>`<span>${label}<b>${num(r.fees[key],2)}元</b></span>`).join('')}</div></div>`:quantState.detailTab==='positions'?qPositionsHTML(r):qTradesHTML(r)}
 <div class="q-result-notes"><p>胜率只统计已经退出的交易；期末持仓以已有收盘估值，不假设期末全部可以成交。</p><p>判断策略时，同时看交易数、回撤、费用和未成交原因。单次短区间结果不足以证明策略有效。</p></div>`;
}
function qEquityChart(r){
 const points=r.curve;if(!points.length)return '';
 const w=960,h=285,left=53,right=935,top=21,bottom=245;
 const vals=points.flatMap(p=>[p.net_value,p.reference]).filter(Number.isFinite);let lo=Math.min(1,...vals),hi=Math.max(1,...vals);const pad=Math.max((hi-lo)*.14,.025);lo-=pad;hi+=pad;
 const x=i=>left+(right-left)*i/Math.max(1,points.length-1),y=v=>bottom-(v-lo)/(hi-lo)*(bottom-top);
 const line=key=>points.map((p,i)=>p[key]==null?'':`${i===0||points[i-1][key]==null?'M':'L'}${x(i).toFixed(2)},${y(p[key]).toFixed(2)}`).join(' ');
 const area=`${line('net_value')} L${x(points.length-1)},${bottom} L${left},${bottom} Z`;
 const gid=`q-fill-${r.id}`;
 return `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" class="q-equity-svg" viewBox="0 0 ${w} ${h}" role="img" aria-label="研究策略净值与同样本观察线，可指向日期查看资产与回撤"><defs><linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#408e76" stop-opacity=".2"/><stop offset="1" stop-color="#408e76" stop-opacity=".01"/></linearGradient></defs>${[0,1,2,3,4].map(i=>{const v=lo+(hi-lo)*i/4;return `<line x1="${left}" x2="${right}" y1="${y(v)}" y2="${y(v)}" stroke="#edf1ee"/><text x="3" y="${y(v)+4}" fill="#99ada2" font-size="11">${num(v,2)}</text>`;}).join('')}<line x1="${left}" x2="${right}" y1="${y(1)}" y2="${y(1)}" stroke="#d9e2dc" stroke-dasharray="4 5"/><path d="${area}" fill="url(#${gid})"/><path d="${line('reference')}" fill="none" stroke="#b6b9a7" stroke-width="1.5" stroke-dasharray="5 4"/><path d="${line('net_value')}" fill="none" stroke="#38886e" stroke-width="2.5"/>${points.map((p,i)=>`<rect data-q-point="${i}" x="${x(i)-(right-left)/Math.max(1,points.length-1)/2}" y="${top}" width="${Math.max(4,(right-left)/Math.max(1,points.length-1))}" height="${bottom-top}" fill="transparent" tabindex="0" aria-label="${esc(p.date)}净值${p.net_value}"><title>${esc(p.date)} 资产${num(p.equity,2)}元 回撤${num(-p.drawdown_pct,2)}%</title></rect>`).join('')}<text x="${left}" y="274" fill="#99ada2" font-size="11">${esc(points[0].date)}</text><text x="${right}" y="274" fill="#99ada2" text-anchor="end" font-size="11">${esc(points.at(-1).date)}</text></svg>`;
}
function qTradesHTML(r){
 if(!r.trades.length)return `<div class="q-zero"><b>区间内没有已退出的模拟交易。</b><p>查看未成交原因、期末持仓和规则条件，胜率保持缺失。</p></div>`;
 return `<div class="table-wrap q-trade-table"><table><thead><tr><th>股票</th><th>信号 / 模拟进入</th><th>模拟退出</th><th>股数</th><th>费用后损益</th><th>退出原因</th></tr></thead><tbody>${r.trades.slice().reverse().map(t=>`<tr><td><b>${esc(t.name)}</b><small>${esc(t.code)}</small></td><td>${esc(t.signal_date)}<small>进入 ${esc(t.entry_date)} · ${num(t.entry_price,2)}</small></td><td>${esc(t.exit_date)}<small>价格 ${num(t.exit_price,2)}</small></td><td>${t.shares}</td><td class="${color(t.pnl)}"><b>${num(t.pnl,2)}</b><small>${pct(t.return_pct)}</small></td><td class="q-reason">${esc(t.reason)}</td></tr>`).join('')}</tbody></table></div>`;
}
function qPositionsHTML(r){
 return r.open_positions.length?`<div class="table-wrap"><table><thead><tr><th>股票</th><th>进入日期</th><th>股数</th><th>进入价</th><th>估值收盘</th></tr></thead><tbody>${r.open_positions.map(p=>`<tr><td>${esc(p.name)}<small>${esc(p.code)}</small></td><td>${esc(p.entry_date)}</td><td>${p.shares}</td><td>${num(p.entry_price,2)}</td><td>${num(p.mark_price,2)}</td></tr>`).join('')}</tbody></table></div>`:'<div class="q-zero">期末没有未平仓的模拟持仓。</div>';
}
function qRulesHTML(){
 const r=quantState.result,c=r?.execution_config||r?.config||r?.request.config||quantState.config;
 return `<div class="q-rules"><h3>${r?'本次运行使用的规则':'当前设计的规则'}</h3><p>所有启用的入场条件同时满足才产生信号；排序只决定观察与模拟进入的先后。</p><ol>${(r?.rules||qRuleLines(c)).map(line=>`<li>${esc(line)}</li>`).join('')}</ol><h3>怎样模拟执行</h3><div class="q-rule-cards">${[['时间顺序','用T日收盘及之前数据形成信号，T+1开盘才模拟进入。'],['持有与退出',`最多${c.max_positions}只，持有${c.holding_days}个交易日；新仓位不在当日退出。`],['盘中止盈止损','同日同时触及止盈和止损时采用止损，跳空越过阈值按开盘价模拟。'],['不能成交的情况','已知涨停价不假设能买到，已知跌停价不假设能卖出；无报价或未知单价日跳过。']].map(([title,text])=>`<section><b>${title}</b><p>${esc(text)}</p></section>`).join('')}</div><h3>本次数据边界</h3>${(r?.warnings||[quantState.catalog.data.sample_warning,'历史板块、逐日连板、历史ST状态与公告信息不完整；相关条件不能直接外推到历史。']).map(w=>`<p class="q-boundary">${esc(w)}</p>`).join('')}<details><summary>参数、排序说明与复现记录</summary><p>趋势排序：均线位置30%、5日动量25%、均量倍数20%、收盘位置15%、20日动量10%；其他模板按对应动量或回踩位置排序。各项截断至0—1后加权，完整公式随引擎版本保留。</p>${r?`<p>运行编号 ${esc(r.id)}<br>引擎 ${esc(r.engine_version)}<br>数据摘要 ${esc(r.input_digest||'尚未冻结')}</p><button class="button small" data-q-replay="${esc(r.id)}" ${qBusy()?'disabled':''}>用冻结输入重新运行</button>`:''}<pre>${esc(JSON.stringify(c,null,2))}</pre></details><div class="q-rule-sources"><span>规则参考</span><a href="https://www.sse.com.cn/lawandrules/sselawsrules2025/trade/universal/c/c_20260424_10816492.shtml" target="_blank" rel="noopener">上交所交易规则（2026） ↗</a><a href="https://www.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20230827_5725662.shtml" target="_blank" rel="noopener">印花税调整通知 ↗</a></div></div>`;
}
function qRuleLines(c){
 const lines=[`成交额≥${c.min_amount/1e8}亿元，换手率${c.min_turnover}—${c.max_turnover}%`,`20日均量倍数${c.min_volume_ratio}—${c.max_volume_ratio}，5日涨幅≥${c.min_return5}%`,`距MA20≤${c.max_extension20}%，收盘位置≥${num(c.close_position_min*100,0)}%`];
 if(c.ma_alignment)lines.push('收盘>MA5>MA10>MA20');if(c.ma20_rising)lines.push('MA20高于5根之前');if(c.breakout_days)lines.push(`突破前${c.breakout_days}根高点，不含当日`);if(c.pullback_ma)lines.push(`距MA${c.pullback_ma}在±${c.pullback_distance_pct}%内`);if(c.min_boards)lines.push(`已核验连板≥${c.min_boards}板，仅当前快照`);if(c.sector_min)lines.push(`板块强度≥${c.sector_min}，仅当前快照`);return lines;
}
function qHistoryHTML(){
 const rows=quantState.catalog.history.filter(h=>h.mode===quantState.catalog.data.mode);
 return `<div class="q-history-intro"><h3>保留每次运行，比较同一规则的表现。</h3><p>“复用参数”使用当前数据，“复现输入”使用当时冻结的数据和参数。比较时请核对区间与样本，演示和真实结果分开展示。</p></div>${rows.length?rows.map(h=>`<section class="q-history-item"><div><span class="tag ${h.mode==='demo'?'blue':'mint'}">${h.mode==='demo'?'合成演示':'真实样本'}</span><span class="q-muted">${esc(qLabels[h.type])} · ${esc(qLabels[h.status])}</span><h4>${esc(h.strategy_name)} ${h.strategy_version?`<small>v${h.strategy_version}</small>`:''}</h4><p>${esc(h.created_at.replace('T',' '))} · 数据日 ${esc(h.trade_date)}</p></div><div class="q-history-outcome">${h.summary?`<b>${h.summary.completed}/${h.summary.selected}<small>只完成独立回测</small></b>`:h.funnel?`<b>${h.funnel.passed}<small>只符合规则</small></b>`:h.metrics?`<b class="${color(h.metrics.total_return_pct)}">${pct(h.metrics.total_return_pct)}<small>样本区间收益</small></b>`:'<b class="q-muted">—<small>无完整指标</small></b>'}<span><button class="button small" data-q-open="${h.id}">查看</button><button class="q-text-button" data-q-reuse="${h.id}" ${qBusy()?'disabled':''}>复用参数</button><button class="q-text-button" data-q-replay="${h.id}" ${qBusy()?'disabled':''}>复现输入</button></span></div></section>`).join(''):'<div class="q-zero">运行后会自动保存第一条记录。</div>'}`;
}

function paintQuant(){if(state.view==='quant')$('#content').innerHTML=quantView();}
function ensureQuantLoaded(){if(!quantState.loading&&Date.now()-quantState.loadedAt>5000)loadQuant();}
async function loadQuant(){
 const q=quantState;if(q.loading)return;q.loading=true;const restoreInitial=!q.config;
 try{
  const data=await api(`/api/quant/status?mode=${encodeURIComponent(state.mode)}`);q.catalog=data;q.loadedAt=Date.now();q.error=null;
  if(!q.config){
   const latest=data.history.find(h=>h.mode===data.data.mode&&h.request?.config);
   const saved=data.strategies.find(s=>s.id===latest?.request.strategy_id);
   const version=saved?.versions.find(v=>v.version===latest.request.strategy_version);
   if(version){q.config={...version.config};q.name=version.name;q.strategyId=saved.id;q.version=version.version;q.savedConfig=JSON.stringify(version.config);q.savedName=version.name;if(latest.request.start)q.start=latest.request.start;if(latest.request.end)q.end=latest.request.end;}
   else if(latest?.request?.config){q.config={...latest.request.config};q.name=latest.strategy_name||'我的策略';if(latest.request.start)q.start=latest.request.start;if(latest.request.end)q.end=latest.request.end;}
   else{q.config={...data.presets.trend.config};q.name='趋势突破 · 我的策略';}
  }
  if(!q.start)q.start=data.data.default_start;if(!q.end)q.end=data.data.default_end;
  const latestInMode=data.history.find(h=>h.mode===data.data.mode);
  const resultId=data.job.result_id&&data.job.result_id!==q.seenJob?data.job.result_id:(!q.result||q.result.mode!==data.data.mode?latestInMode?.id:null);
  if(q.result&&q.result.mode!==data.data.mode&&!resultId)q.result=null;
  if(resultId)q.result=await api(`/api/quant/result?id=${encodeURIComponent(resultId)}`);
  if(restoreInitial&&q.result?.type==='stock_backtest')qRestoreStocks(q.result);
  else if(q.selected.length&&!qBusy())qScheduleDataCheck();
  q.seenJob=data.job.result_id;if(data.job.state==='running')pollQuant();
 }catch(error){q.error=error.message;toast(`策略数据读取失败：${error.message}`);}
 finally{q.loading=false;paintQuant();}
}
function pollQuant(){clearTimeout(quantState.timer);quantState.timer=setTimeout(async()=>{
 if(state.view!=='quant')return;
 try{
  const data=await api(`/api/quant/status?mode=${encodeURIComponent(state.mode)}`);quantState.catalog=data;quantState.loadedAt=Date.now();
  if(data.job.state==='running')pollQuant();
  else if(data.job.result_id&&data.job.result_id!==quantState.seenJob){quantState.result=await api(`/api/quant/result?id=${encodeURIComponent(data.job.result_id)}`);quantState.seenJob=data.job.result_id;quantState.tab='result';toast(data.job.message);}
  paintQuant();if(data.job.state!=='running'&&quantState.selected.length)qScheduleDataCheck();
 }catch(error){quantState.error=error.message;toast(error.message);}
 },1100);}
function qUpdatePreview(){
 const label=$('#q-version-label');if(label)label.textContent=qDirty()?'未保存参数':`已保存 v${quantState.version}`;
 const blocked=qHistoryBlocked(),note=$('#q-run-note'),button=$('#q-run-backtest');
 if(note)note.textContent=blocked?'连板或板块条件缺少逐日历史，目前可运行当日筛选。':'收盘后产生信号，下一交易日模拟执行。';
 if(button)button.disabled=qBusy()||blocked;
 const stocks=$('#q-run-stocks');if(stocks)stocks.disabled=qBusy()||blocked||!quantState.selected.length;
 const capital=$('#q-stock-capital');if(capital)capital.innerHTML=`${num(quantState.config.capital,0)}<small>元 / 只</small>`;
 const stockNote=$('#q-stock-run-note');if(stockNote)stockNote.textContent=blocked?'当前连板或板块条件缺少逐日历史，请更换模板或关闭相关条件。':`使用「${quantState.name}」${qDirty()?'的当前参数快照':`v${quantState.version}`}；每只股票都会保留一份结果。`;
}
async function qStart(kind,replayId=null){
 if(qBusy())return;
 const q=quantState;
 if(!replayId&&['backtest','stock_backtest'].includes(kind)&&qHistoryBlocked()){toast('缺少逐日连板或板块历史，请先运行当日筛选');return;}
 if(!replayId&&!$('#q-config-form').reportValidity())return;
 if(!replayId&&kind==='stock_backtest'){
  if(!q.selected.length){toast('请先添加需要回测的股票');return;}
  if(!$('#q-stock-start').reportValidity()||!$('#q-stock-end').reportValidity())return;
 }
 q.pending=true;paintQuant();
 try{
  const clean=!qDirty();const body={type:kind,config:q.config,mode:state.mode,start:q.start,end:q.end,
   strategy_name:q.name,strategy_id:clean?q.strategyId:null,strategy_version:clean?q.version:null,
   codes:q.selected.map(s=>s.code),auto_history:q.autoHistory};
  const response=await api(replayId?'/api/quant/replay':'/api/quant/run',replayId?{id:replayId}:body);
  q.catalog.job=response.job;q.tab='result';await loadQuant();if(q.catalog.job.state==='running')pollQuant();
  $('#q-output')?.scrollIntoView({behavior:'smooth',block:'start'});
 }catch(error){toast(error.message);}
 finally{q.pending=false;paintQuant();qScheduleDataCheck();}
}
async function qSave(){
 const q=quantState;if(!$('#q-config-form').reportValidity())return;
 if(!qDirty()){toast(`当前参数已保存为v${q.version}`);return;}
 const saved=await api('/api/quant/strategy',{id:q.strategyId,name:q.name,config:q.config});
 const v=saved.versions.at(-1);q.config={...v.config};q.name=saved.name;q.strategyId=saved.id;q.version=v.version;q.savedConfig=JSON.stringify(v.config);q.savedName=saved.name;
 await loadQuant();toast(`策略已保存为v${v.version}`);
}
function exportQuant(){if(!quantState.result){toast('请先运行策略');return;}const link=document.createElement('a');link.href=qExport('json');link.download=`策略运行-${quantState.result.id}.json`;link.click();}
document.addEventListener('input',event=>{
 const el=event.target,key=el.dataset?.qKey;
 if(key){quantState.config[key]=el.type==='checkbox'?el.checked:typeof quantState.config[key]==='number'?Number(el.value)*Number(el.dataset.qUnit||1):el.value;qUpdatePreview();qScheduleDataCheck();}
 if(el.id==='q-name'){quantState.name=el.value;qUpdatePreview();}
 if(['q-start','q-stock-start'].includes(el.id)){quantState.start=el.value;const other=$('#'+(el.id==='q-start'?'q-stock-start':'q-start'));if(other)other.value=el.value;}
 if(['q-end','q-stock-end'].includes(el.id)){quantState.end=el.value;const other=$('#'+(el.id==='q-end'?'q-stock-end':'q-end'));if(other)other.value=el.value;}
 if(['q-start','q-end','q-stock-start','q-stock-end'].includes(el.id)){const text=$('#q-window');if(text)text.textContent=`回放区间 ${quantState.start} 至 ${quantState.end}`;qScheduleDataCheck();}
 if(el.id==='q-stock-search'){quantState.stockQuery=el.value;quantState.stockSearchSequence++;clearTimeout(quantState.stockTimer);quantState.stockTimer=setTimeout(qSearchStocks,300);}
 if(el.id==='q-stock-bulk')quantState.bulkText=el.value;
 if(el.id==='q-auto-history'){quantState.autoHistory=el.checked;qPaintReadiness();const button=$('#q-run-stocks');if(button)button.innerHTML=icon('chart',16)+(quantState.autoHistory?'运行所选股票回测 · 自动准备数据':'仅用本机缓存回测');}
});
document.addEventListener('submit',event=>{if(event.target.id==='q-config-form')event.preventDefault();});
document.addEventListener('change',async event=>{
 const el=event.target,q=quantState;
 try{
  if(el.id==='q-mode'){if(qBusy())return;state.mode=el.value;q.start='';q.end='';q.selected=[];q.autoHistory=true;q.stockOptions=null;q.stockQuery='';q.stockSearchSequence++;q.stockSearchLoading=false;q.loadedAt=0;qScheduleDataCheck();await load(state.analysis.config);await loadQuant();}
  if(el.id==='q-strategy'||el.id==='q-version'){
   const strategy=q.catalog.strategies.find(s=>s.id===(el.id==='q-strategy'?el.value:q.strategyId));if(!strategy)return;
   const v=el.id==='q-version'?strategy.versions.find(v=>v.version===Number(el.value)):strategy.versions.at(-1);
   q.config={...v.config};q.name=v.name;q.strategyId=strategy.id;q.version=v.version;q.savedConfig=JSON.stringify(v.config);q.savedName=v.name;qScheduleDataCheck();paintQuant();
  }
 }catch(error){toast(error.message);}
});
document.addEventListener('click',async event=>{
 const b=event.target.closest('[data-q-action],[data-q-template],[data-q-tab],[data-q-detail],[data-q-open],[data-q-reuse],[data-q-replay],[data-q-add],[data-q-remove],[data-q-stock-result]');if(!b||b.disabled)return;
 const q=quantState;
 try{
  if(b.dataset.qTemplate){const p=q.catalog.presets[b.dataset.qTemplate];q.config={...p.config};q.name=p.name+' · 我的策略';q.strategyId=null;q.version=null;q.savedConfig=null;q.savedName=null;qScheduleDataCheck();paintQuant();return;}
  if(b.dataset.qTab){q.tab=b.dataset.qTab;paintQuant();return;}if(b.dataset.qDetail){q.detailTab=b.dataset.qDetail;paintQuant();return;}
  if(b.dataset.qStockResult){q.activeStock=b.dataset.qStockResult;q.detailTab='trades';paintQuant();return;}
  if(b.dataset.qAdd){if(qBusy())return;const s=q.stockOptions?.find(s=>s.code===b.dataset.qAdd);if(s)qAddStocks([s]);return;}
  if(b.dataset.qRemove){if(qBusy())return;q.selected=q.selected.filter(s=>s.code!==b.dataset.qRemove);qScheduleDataCheck();paintQuant();return;}
  if(b.dataset.qReplay){await qStart(null,b.dataset.qReplay);return;}
  if(b.dataset.qOpen||b.dataset.qReuse){const r=await api(`/api/quant/result?id=${encodeURIComponent(b.dataset.qOpen||b.dataset.qReuse)}`);
   if(b.dataset.qOpen){q.result=r;q.activeStock=null;q.tab='result';}
   else{if(qBusy())return;qReuseRun(r);toast('已复用策略与所选股票，新运行默认自动准备数据');}
   paintQuant();return;
  }
  switch(b.dataset.qAction){case 'refresh':await loadQuant();break;case 'save':await qSave();break;
   case 'scan':await qStart('scan');break;case 'backtest':await qStart('backtest');break;
   case 'stock-backtest':await qStart('stock_backtest');break;
   case 'check-data':await qCheckStocks();break;
   case 'repair-history':if(!qBusy()&&q.result?.type==='stock_backtest'){qReuseRun(q.result);paintQuant();await qStart('stock_backtest');}break;
   case 'search-stocks':await qSearchStocks();break;
   case 'clear-stocks':if(!qBusy()){q.selected=[];qScheduleDataCheck();paintQuant();}break;
   case 'add-bulk':{if(qBusy())break;const mode=state.mode;const response=await api('/api/quant/stocks/resolve',{mode,codes:q.bulkText});if(mode===state.mode){if(qAddStocks(response.stocks)){q.bulkText='';paintQuant();}}break;}
   case 'cancel':await api('/api/quant/cancel',{});await loadQuant();break;
   case 'export-config':download(`${q.name||'策略'}-参数.json`,JSON.stringify({name:q.name,config:q.config},null,2),'application/json');break;}
 }catch(error){toast(error.message);}
});
function qPointReadout(event){const el=event.target.closest?.('[data-q-point]');if(!el)return;const p=qSelectedResult()?.curve?.[Number(el.dataset.qPoint)];const node=$('#q-chart-readout');if(p&&node)node.textContent=`${p.date} · 资产${num(p.equity,2)}元 · 回撤${num(-p.drawdown_pct,2)}% · 持仓${p.positions}只`;}
document.addEventListener('mousemove',qPointReadout);document.addEventListener('focusin',qPointReadout);
