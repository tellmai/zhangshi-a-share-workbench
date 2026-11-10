// Exercise the actual app and short-term module together, with no browser or network.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const read=name=>fs.readFileSync(path.join(__dirname,'../static',name),'utf8');

function fixture(){
 const stock=(code,role,score,extension,breakout,sector,matched=true)=>({code,name:'合成'+code,role,score,selected:true,
  board:'主板',primary_sector:sector,sector_names:[sector],pct_chg:6,consecutive:role==='连板观察'?3:role==='首板观察'?1:0,
  current:{close:12,high:12.2,low:11,amount_yuan:5e8,turnover_rate:5},technical:{volume_ratio:1.8,extension20:extension,breakout20:breakout},
  verification:{status:matched?'matched':'single_source'},reasons:['板块扩散有同日证据','量价结构符合当前规则'],risk_flags:['公告待核验'],
  exclusions:[],confirmation:'次日观察承接与板块扩散',invalidation:'板块转弱或原有条件失效',components:[],penalties:[]});
 const stocks=[stock('A','首板观察',88,6,true,'I0'),stock('B','连板观察',92,35,true,'I1',false),stock('C','趋势强势',85,10,false,'I0'),stock('D','趋势突破',70,null,true,'I1',false),
  {...stock('X','首板观察',96,8,true,'I0'),selected:false,exclusions:['历史不足60根','所需复权因子缺失']}];
 const sector=(id,pct,members,expanded=true)=>({id,name:id,type:'industry',asof_date:'2026-10-23',daily_pct:pct,avg_pct:pct,score:70,classification:'合成分类',complete:true,breadth:80,limit_up:1,members,expanded});
 const sectors=[sector('I0',9,['A','C','X']),sector('I1',4,['B','D']),sector('I2',6,[])];
 return {meta:{trade_date:'2026-10-23',mode:'live',source:'合成数据源',universe:'合成测试范围',fetched_at:'2026-10-23T17:00:00+08:00'},
  config:{style:'balanced',sector_type:'industry',min_amount:3e8,min_turnover:2,max_turnover:30,min_score:60,min_sector_score:40},
  stocks,candidates:['A','B','C','D'],sectors,sector_leaders:{rows:[sectors[0],sector('U',8,[],false),sectors[2],sectors[1]],basis:'同日成分等权涨幅',scope:'已展开研究范围',complete_universe:false},
  quality:{conflicts:0,matched:3,actual_count:5},market:{active_count:5,advancing:4,declining:1,unchanged:0,breadth:80,limit_up:null,limit_up_confirmed:2,limit_down:null,limit_down_confirmed:0,
   broken_rate:null,confirmed_height:3,height:null,height_complete:false,limit_status_coverage:40,phase:'分歧轮动',promotion_rate:null,ladder:[{height:3,count:1,stocks:[{name:'合成B',code:'B'}]}]}};
}
function harness(){
 const nodes=new Map(),storage=new Map(),listeners={};let failStorage=false;
 const classes=()=>({values:new Set(),add(x){this.values.add(x);},remove(x){this.values.delete(x);},toggle(x,v){if(v)this.values.add(x);else this.values.delete(x);},contains(x){return this.values.has(x);}});
 let dynamic=new Set();
 const node=selector=>{
  if(!nodes.has(selector)){
   let html='';const n={textContent:'',hidden:false,disabled:false,attrs:{},dataset:{compact:'false'},classList:classes(),
    addEventListener(){},setAttribute(k,v){this.attrs[k]=v;},scrollIntoView(){},hasAttribute(k){return k in this.attrs;}};
   Object.defineProperty(n,'innerHTML',{get(){return html;},set(value){html=value;if(selector==='#content')dynamic=new Set([...value.matchAll(/id="([^"]+)"/g)].map(m=>'#'+m[1]));else for(const m of value.matchAll(/id="([^"]+)"/g))dynamic.add('#'+m[1]);}});
   nodes.set(selector,n);
  }
  return nodes.get(selector);
 };
 const query=selector=>{
  if(['#stock-rows','#short-stock-results','#short-plan-host','#short-result-count','#short-more','#table-count','#stock-empty','#short-note-state'].includes(selector)&&!dynamic.has(selector))return null;
  return node(selector);
 };
 const roleButtons=['all','首板观察','连板观察','趋势'].map(role=>({...node('role:'+role),dataset:{role}}));
 const c=vm.createContext({document:{querySelector:query,querySelectorAll:s=>s==='[data-role]'?roleButtons:[],addEventListener(type,fn){(listeners[type]??=[]).push(fn);}},
  window:{addEventListener(){}},location:{hash:'#stocks'},localStorage:{getItem:k=>storage.get(k)||null,setItem(k,v){if(failStorage)throw new Error('storage full');storage.set(k,v);}},
  URL,setTimeout(){return 0;},clearTimeout(){},requestAnimationFrame(){}});
 vm.runInContext(read('shortline.js'),c);
 vm.runInContext(read('app.js').replace(/\nboot\(\);\s*$/,'\n'),c);
 const run=code=>vm.runInContext(code,c);
 c.fixture=fixture();run('state.analysis=fixture;state.status={today:"2026-10-24",job:{state:"idle"}};');
 const emit=async(type,target)=>{for(const fn of listeners[type]||[])await fn({target:{...target,closest:()=>target}});};
 return {c,node,run,storage,roleButtons,emit,fail(){failStorage=true;}};
}
async function main(){
 {
  const h=harness();h.c.location.hash='#shortline';h.run('render()');
  const html=h.node('#content').innerHTML;
  assert.match(h.node('#navigation').innerHTML,/href="#shortline".*短线复盘/);
  assert.match(h.node('#navigation').innerHTML,/强势股精选/);
  assert.match(html,/收盘情绪速读/);assert.match(html,/上涨占优/);assert.match(html,/已确认样本 · 全量状态未齐/);
  assert.match(html,/已确认连板高度 · 覆盖不全/);assert.match(html,/触板状态不完整/);
  assert.match(html,/当日主线/);assert.match(html,/强势候选速览/);
  assert(html.indexOf('data-sector="I0"')<html.indexOf('data-sector-unexpanded="U"'));
  assert.doesNotMatch(h.run('shortSectorHTML()'),/data-sector="I1"/);
  assert.match(h.node('#short-plan-host').innerHTML,/次日观察清单/);
  assert.doesNotMatch(html,/undefined|NaN/);
  console.log('PASS: short-term review renders actual scope, honest partial limit counts, daily leaders, ladder and observation plan');
 }
 {
  const h=harness();h.run('render()');
  let html=h.node('#short-stock-results').innerHTML;
  assert(html.indexOf('合成B')<html.indexOf('合成A'));
  assert.doesNotMatch(html,/合成X/);assert.match(html,/强在哪里/);assert.match(html,/次日确认与失效条件/);
  assert.match(html,/公告待核验/);assert.match(html,/data-short-plan="A"/);
  h.run('shortState.mainline=true;');assert.deepEqual(Array.from(h.run('filteredStocks().map(s=>s.code)')),['A','C']);
  h.run('shortState.mainline=false;shortState.breakout=true;');assert.deepEqual(Array.from(h.run('filteredStocks().map(s=>s.code)')),['B','A','D']);
  h.run('shortState.breakout=false;shortState.avoidExtended=true;');assert.deepEqual(Array.from(h.run('filteredStocks().map(s=>s.code)')),['A','C']);
  h.run('shortState.avoidExtended=false;shortState.matched=true;');assert.deepEqual(Array.from(h.run('filteredStocks().map(s=>s.code)')),['A','C']);
  h.run('shortState.mainline=true;shortState.breakout=true;shortState.avoidExtended=true;');assert.deepEqual(Array.from(h.run('filteredStocks().map(s=>s.code)')),['A']);
  h.run('state.showExcluded=true;render();');html=h.node('#short-stock-results').innerHTML;
  assert.match(html,/合成X/);assert.match(html,/历史不足60根/);assert.match(html,/复权因子缺失/);
  assert.match(html,/data-short-plan="X"[^>]*disabled/);
  console.log('PASS: card selection combines mainline, breakout, extension and verified-close filters while preserving excluded-stock reasons');
 }
 {
  const h=harness();h.run('render()');
  await h.emit('change',{dataset:{shortFilter:'breakout'},checked:true});
  assert.equal(h.run('shortState.breakout'),true);
  assert.doesNotMatch(h.node('#short-stock-results').innerHTML,/合成C/);
  await h.emit('click',{dataset:{shortLayout:'table'},hasAttribute:()=>false,classList:{contains:()=>false}});
  assert.equal(h.run('shortState.layout'),'table');assert.match(h.node('#stock-rows').innerHTML,/合成B/);
  assert.doesNotMatch(h.node('#stock-rows').innerHTML,/合成C/);
  h.run('state.role="连板观察";renderTable();');
  assert.match(h.node('#stock-rows').innerHTML,/合成B/);assert.doesNotMatch(h.node('#stock-rows').innerHTML,/合成A/);
  assert.equal(h.roleButtons[2].attrs['aria-pressed'],'true');
  h.run('state.role="all";state.search="合成A";renderTable();');assert.match(h.node('#table-count').textContent,/1 \/ 1/);
  console.log('PASS: user filter changes and card/table switching update actual results, role selection and search counts');
 }
 {
  const h=harness();h.run('render();toggleShortPlan("A");toggleShortPlan("B");');
  assert.deepEqual(Array.from(h.run('shortPlan().codes')),['A','B']);
  assert.match(h.node('#short-stock-results').innerHTML,/已加入观察/);
  assert.match(h.node('#short-plan-host').innerHTML,/合成A/);
  await h.emit('input',{id:'short-plan-note',dataset:{},value:'等待主线扩散；承接转弱则放弃'});
  assert.equal(h.run('shortPlan().note'),'等待主线扩散；承接转弱则放弃');
  assert.equal(JSON.parse(h.storage.get('after-close-short-plans'))['real:2026-10-23'].codes.length,2);
  h.c.exports=[];h.run('download=function(...args){exports.push(args);};exportShortPlan();');
  assert.match(h.c.exports[0][0],/2026-10-23/);assert.match(h.c.exports[0][1],/等待主线扩散/);
  assert.match(h.c.exports[0][1],/确认：次日观察承接/);assert.match(h.c.exports[0][1],/失效：板块转弱/);
  h.run('state.analysis.meta.mode="demo";');assert.equal(h.run('shortPlan().codes.length'),0);
  h.run('state.analysis.meta.mode="live";state.analysis.meta.trade_date="2026-10-22";');assert.equal(h.run('shortPlan().codes.length'),0);
  h.run('state.analysis.meta.trade_date="2026-10-23";toggleShortPlan("A");toggleShortPlan("X");');
  assert.deepEqual(Array.from(h.run('shortPlan().codes')),['B']);
  console.log('PASS: dated observation plans persist with notes, export confirmation/failure conditions, separate demo data and reject excluded additions');
 }
 {
  const h=harness();h.run('render();');h.fail();h.run('toggleShortPlan("A");');
  assert.equal(h.run('shortState.storageError'),true);
  assert.match(h.node('#short-plan-host').innerHTML,/暂存当前页面/);
  await h.emit('input',{id:'short-plan-note',dataset:{},value:'存储失败时仍保留可导出的笔记'});
  h.c.exports=[];h.run('download=function(...args){exports.push(args);};exportShortPlan();');
  assert.match(h.c.exports[0][1],/存储失败时仍保留可导出的笔记/);
  assert.match(h.c.exports[0][1],/合成A/);
  console.log('PASS: unavailable local storage retains the current plan and note in memory for export');
 }
 {
  const h=harness();h.c.fixture.stocks[0].name='<img src=x onerror=evil>';
  h.run('render();');assert.match(h.node('#short-stock-results').innerHTML,/&lt;img/);
  assert.doesNotMatch(h.node('#short-stock-results').innerHTML,/<img src=x/);
  h.run('toggleShortPlan("A");');
  await h.emit('input',{id:'short-plan-note',dataset:{},value:'</textarea><script>evil()</script>'});
  assert.match(h.run('shortPlanHTML()'),/&lt;\/textarea&gt;/);
  h.run('state.analysis.sector_leaders.rows[0].asof_date="2026-10-22";');
  assert.doesNotMatch(h.run('shortSectorHTML()'),/data-sector="I0"/);
  h.run('state.analysis.market.active_count=0;');assert.match(h.run('shortMood().label'),/数据待齐/);
  assert.match(h.run('shortNextFocus(state.analysis.market)'),/补齐同日有效行情/);
  assert.match(h.run('shortlineView()'),/暂无有效收盘样本/);
  console.log('PASS: names and notes are escaped, stale sector rows are omitted, and empty data does not imply a market direction');
 }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
