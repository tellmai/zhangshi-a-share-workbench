'use strict';
const sourcesState={data:null,loading:false,pending:false,error:null,timer:null,loadedAt:0,dirty:false};
const sourceStates={success:'采样通过',error:'本次失败',needs_setup:'待配置',research_only:'独立研究可用',login_only:'仅登录已验证'};
async function readSourceReferenceCSV(file){
 if(file.size>12*1024*1024)throw new Error('参考CSV请控制在12MB以内');
 const bytes=await file.arrayBuffer();let text=new TextDecoder('utf-8').decode(bytes);
 if(text.includes('\uFFFD'))text=new TextDecoder('gb18030').decode(bytes);
 return parseCSV(text).map(row=>{let code=row.code.replace(/^'/,'').trim().toUpperCase();if(/^(SH|SZ)\d{6}$/.test(code))code=code.slice(2)+'.'+code.slice(0,2);
  if(/^\d{6}$/.test(code)){const known=state.analysis.stocks.filter(s=>s.code.slice(0,6)===code);if(known.length===1)code=known[0].code;}
  let day=row.date;if(day&&/^\d{8}$/.test(day))day=day.slice(0,4)+'-'+day.slice(4,6)+'-'+day.slice(6);
  return {...row,code,...(day?{date:day}:{}),close:row.close.replace(/,/g,'')};});
}
function sourcesBusy(){return sourcesState.pending||sourcesState.data?.job.state==='running';}
function sourceHubHTML(){
 const s=sourcesState,d=s.data;
 if(!d)return `<section class="panel source-hub"><div class="panel-heading"><h2>数据源设置</h2></div><p class="note-block">${s.error?esc(s.error):'正在读取数据源配置…'}<button class="text-button" data-source-action="reload">重新读取</button></p></section>`;
 const busy=sourcesBusy(),c=d.settings;
 return `<section class="panel source-hub"><div class="panel-heading"><div><div class="eyebrow">DATA CONNECTIONS</div><h2>数据源设置与连接检测</h2><p>免费通道优先；看清实际日期、上游和可用范围。</p></div><button class="button primary small" data-source-action="all" ${busy?'disabled':''}>${icon('refresh',14)}检测全部免费通道</button></div>
 <div class="source-intro">${d.notes.map(n=>`<span>${esc(n)}</span>`).join('')}</div>
 ${s.error?`<div class="source-warning">${esc(s.error)}</div>`:''}
 <form id="source-settings-form"><fieldset ${busy?'disabled':''}><div class="source-routing"><label>收盘行情通道<select name="market_provider">${[['auto','自动：东方财富 → 新浪 / 腾讯'],['eastmoney','优先东方财富'],['sina','优先新浪 / 腾讯']].map(([id,label])=>`<option value="${id}" ${c.market_provider===id?'selected':''}>${label}</option>`).join('')}</select></label><label>历史优先来源<select name="history_primary">${['tencent','eastmoney','baostock','akshare'].map(id=>`<option value="${id}" ${c.history_order[0]===id?'selected':''}>${esc(d.catalog.find(x=>x.id===id).name)}</option>`).join('')}</select></label><label>库调用超时（秒）<input name="library_timeout" type="number" min="10" max="60" value="${c.library_timeout}" required></label></div>
 <div class="source-history-options"><span>启用历史来源</span>${['tencent','eastmoney','baostock','akshare'].map(id=>`<label><input type="checkbox" name="history_source" value="${id}" ${c.history_order.includes(id)?'checked':''}>${esc(d.catalog.find(x=>x.id===id).name)}</label>`).join('')}</div>
 <div class="source-save"><label><input name="market_fallback" type="checkbox" ${c.market_fallback?'checked':''}>行情通道失败后自动切换备用</label><button class="button small" type="submit">保存来源设置</button><span id="source-dirty">${s.dirty?'有未保存修改':'已读取本机设置'} · 下次更新和历史补齐使用</span></div></fieldset></form>
 <div class="source-test-controls"><label>核对日期<input id="source-test-date" type="date" value="${esc(s.test?.date||d.snapshot_date)}" ${busy?'disabled':''}></label><label>采样股票<input id="source-test-code" value="${esc(s.test?.code||'600838.SH')}" placeholder="600838.SH" ${busy?'disabled':''}></label><label>同花顺行业名称<input id="source-test-symbol" value="${esc(s.test?.symbol||'半导体')}" maxlength="30" ${busy?'disabled':''}></label><span>只读采样，保留现有行情。</span></div>
 ${d.job.state==='running'?`<div class="source-job" role="status"><span class="spinner"></span><b>${esc(d.job.message)}</b><span>${num(d.job.progress,0)}%</span></div>`:''}
 <div class="source-card-grid">${d.catalog.map(p=>{const h=d.health[p.id],phase=h?.state;return `<article class="source-card"><div><h3>${esc(p.name)}</h3><span class="tag ${phase==='success'||phase==='research_only'?'mint':phase==='error'?'warm':''}">${esc(sourceStates[phase]||'尚未检测')}</span></div><p>${esc(p.role)}</p><small>${esc(p.access)} · 上游 ${esc(p.family)}${p.module?' · '+(p.installed?'库已安装':'依赖未安装'):''}</small>${h?`<p class="source-health">${esc(h.message)}</p><small>检测 ${esc(h.checked_at)}${h.data_date?' · 数据 '+esc(h.data_date):''}<br>${esc(h.scope||'')}</small>${h.sample?`<details><summary>查看采样和证据</summary>${h.evidence?`<p>本机证据 ${esc(h.evidence)}</p>`:''}<pre>${esc(JSON.stringify(h.sample,null,2))}</pre></details>`:''}`:'<p class="source-health source-muted">安装了库不代表网络或数据权限已通过。</p>'}<div class="source-card-actions">${p.id==='ifind'?`<button class="button small" data-source-action="ifind" ${busy||!p.installed?'disabled':''}>官方SDK登录检测</button>`:`<button class="button small" data-source-provider="${p.id}" ${busy||!p.installed?'disabled':''}>${p.id==='ths'?'测试行业指数':'测试连接'}</button>`}<a class="text-button" href="${safeURL(p.docs)}" target="_blank" rel="noopener noreferrer">官方/项目说明 ↗</a></div>${p.id==='ifind'?'<p class="source-account-note">普通同花顺账号不等于数据接口账号。需先安装官方SDK并开通接口权限；本页不读取客户端Cookie，也不保存账号密码。</p>':''}</article>`;}).join('')}</div>
 <details class="source-ifind" id="source-ifind"><summary>同花顺：账号与其他接入方式</summary><p>公开板块无需个人登录，但可能受页面变化和访问限制影响。普通客户端可导出CSV作为参考收盘，不能据此获得官方API权限。iFinD登录成功也不等于所有行情字段已开通。</p><button class="button small" data-source-action="csv">导入客户端CSV参考收盘</button><small>只核对代码与收盘，不导入完整日线或板块。</small><form id="source-ifind-form"><label>iFinD数据接口账号<input name="username" autocomplete="off" maxlength="256" ${d.catalog.find(p=>p.id==='ifind').installed&&!busy?'':'disabled'} required></label><label>接口密码<input name="password" type="password" autocomplete="off" maxlength="256" ${d.catalog.find(p=>p.id==='ifind').installed&&!busy?'':'disabled'} required></label><button class="button small" type="submit" ${d.catalog.find(p=>p.id==='ifind').installed&&!busy?'':'disabled'}>检测登录并结束本次会话</button></form><p>仅本次请求使用，不写入设置或证据。官方说明同一账号跨设备登录可能互斥；检测成功只确认身份，尚未接入主行情。</p><a class="text-button" href="https://quantapi.51ifind.com/" target="_blank" rel="noopener noreferrer">官方接口、SDK与试用说明 ↗</a></details></section>`;
}
function ensureSourcesLoaded(){if(!sourcesState.loading&&Date.now()-sourcesState.loadedAt>10000&&!sourcesState.dirty)loadSources();}
async function loadSources(){const s=sourcesState;if(s.loading)return;s.loading=true;try{s.data=await api('/api/sources');s.loadedAt=Date.now();s.error=null;if(s.data.job.state==='running')pollSources();}catch(e){s.error=e.message;s.loadedAt=Date.now();}finally{s.loading=false;if(state.view==='data')render();}}
function pollSources(){clearTimeout(sourcesState.timer);sourcesState.timer=setTimeout(async()=>{if(state.view==='data')await loadSources();},1400);}
async function startSourceTest(provider,credentials={}){
 if(sourcesBusy())return;if(sourcesState.dirty){toast('请先保存来源设置，再测试连接');return;}
 const body={provider,date:$('#source-test-date').value,code:$('#source-test-code').value.trim().toUpperCase(),symbol:$('#source-test-symbol').value.trim(),...credentials};
 sourcesState.test={date:body.date,code:body.code,symbol:body.symbol};
 sourcesState.pending=true;try{await api('/api/sources/test',body);if(provider==='ifind')$('#source-ifind-form').reset();await loadSources();pollSources();}catch(e){toast(e.message);}finally{sourcesState.pending=false;if(state.view==='data')render();}
}
document.addEventListener('click',async event=>{const b=event.target.closest('[data-source-action],[data-source-provider]');if(!b)return;
 if(b.dataset.sourceProvider){await startSourceTest(b.dataset.sourceProvider);return;}
 switch(b.dataset.sourceAction){case 'reload':await loadSources();break;case 'all':await startSourceTest('all');break;
 case 'ifind':$('#source-ifind').open=true;$('#source-ifind').scrollIntoView({behavior:'smooth'});break;
 case 'csv':$('#reference-form').elements.source.value='同花顺客户端CSV导出（用户提供）';$('#reference-form').elements.trade_date.value=state.analysis.meta.trade_date;$('#reference-dialog').showModal();break;}
});
document.addEventListener('input',event=>{if(event.target.closest('#source-settings-form')){sourcesState.dirty=true;$('#source-dirty').textContent='有未保存修改；保存后生效';}});
document.addEventListener('submit',async event=>{
 if(event.target.id==='source-ifind-form'){event.preventDefault();const f=new FormData(event.target);await startSourceTest('ifind',{username:f.get('username'),password:f.get('password')});return;}
 if(event.target.id!=='source-settings-form')return;event.preventDefault();if(sourcesBusy())return;
 const f=new FormData(event.target),primary=f.get('history_primary'),enabled=f.getAll('history_source');
 if(!enabled.includes(primary)){toast('请勾选历史优先来源，或更换优先来源');return;}
 const history_order=[primary,...enabled.filter(id=>id!==primary)];
 try{await api('/api/sources/settings',{market_provider:f.get('market_provider'),market_fallback:f.has('market_fallback'),history_order,library_timeout:Number(f.get('library_timeout'))});sourcesState.dirty=false;await loadSources();toast('来源设置已保存，下次更新与补齐使用');}catch(e){toast(e.message);}
});
