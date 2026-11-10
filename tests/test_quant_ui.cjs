// Real template/result shapes from synthetic Python data, rendered without a browser.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {execFileSync}=require('node:child_process');
const root=path.resolve(__dirname,'..');
const fixtures=JSON.parse(execFileSync(process.env.QSA_PYTHON||'python',['-c',[
 'import json',
 'from qsa.data import make_demo',
 'from qsa.quant import preset_catalog,dataset_info,scan,replay',
 'from qsa.quant_stocks import replay_stocks',
 'd=make_demo();p=preset_catalog();c=p["trend"]["config"];s=scan(d,c);r=replay(d,c,d["meta"]["calendar"][-20],d["meta"]["trade_date"])',
 'base=dict(id="a"*20,strategy_name="合成策略",strategy_version=None,mode="demo",trade_date=d["meta"]["trade_date"],created_at="2026-10-22T19:30:00+08:00",engine_version="1.0.0",input_digest="b"*64,request=dict(config=c,type="backtest",start=r["start"],end=r["end"]))',
 'd["stocks"][1]["bars"]=d["stocks"][1]["bars"][-1:];b=replay_stocks(d,c,["DEMO001","DEMO002"],r["start"],r["end"]);b={**base,**b,"request":{**base["request"],"type":"stock_backtest","codes":["DEMO001","DEMO002"],"auto_history":False}}',
 'print(json.dumps(dict(presets=p,data=dataset_info(d),scan={**base,**s},replay={**base,**r},batch=b),ensure_ascii=False))',
].join(';')],{cwd:root,encoding:'utf8',env:{...process.env,PYTHONIOENCODING:'utf-8'}}));

function harness(){
 const nodes=new Map(),listeners=new Map(),timers=[];
 const node=selector=>{if(!nodes.has(selector))nodes.set(selector,{innerHTML:'',textContent:'',hidden:false,disabled:false,
  classList:{add(){},remove(){},toggle(){}},addEventListener(){},setAttribute(){},reportValidity:()=>true,scrollIntoView(){}});return nodes.get(selector);};
 const c=vm.createContext({document:{querySelector:node,querySelectorAll:()=>[],addEventListener(type,fn){if(!listeners.has(type))listeners.set(type,[]);listeners.get(type).push(fn);}},
  window:{addEventListener(){}},location:{hash:'#quant'},localStorage:{getItem:()=>null},URL,
  setTimeout:fn=>{timers.push(fn);return timers.length;},clearTimeout(){},requestAnimationFrame(){}});
 vm.runInContext(fs.readFileSync(path.join(root,'static/quant.js'),'utf8'),c);
 vm.runInContext(fs.readFileSync(path.join(root,'static/app.js'),'utf8').replace(/\nboot\(\);\s*$/,'\n'),c);
 c.catalog={presets:fixtures.presets,data:fixtures.data,strategies:[],history:[],engine_version:'1.0.0',job:{state:'idle',message:'准备运行',progress:0}};
 c.config={...fixtures.presets.trend.config};
 vm.runInContext("state.view='quant';state.mode='demo';state.analysis={config:{style:'balanced'}};quantState.catalog=catalog;quantState.config=config;quantState.name='测试策略';quantState.start=catalog.data.default_start;quantState.end=catalog.data.default_end;",c);
 return {c,node,nodes,listeners,run:code=>vm.runInContext(code,c)};
}
async function main(){
 {
  const h=harness(),html=h.run('quantView()');
  for(const name of ['趋势突破','放量上攻','强势回踩','连板观察','保存策略','运行当日筛选','运行历史回测'])assert(html.includes(name));
  assert.match(html,/合成演示/);assert.match(html,/历史结果受样本范围影响/);
  assert.match(h.run('qInput("收盘位置","close_position_min",{unit:.01})'),/value="55"/);
  assert.doesNotMatch(html,/55\.0000000/);
  console.log('PASS: novice workflow, four runnable templates and exact input units render');
 }
 {
  const h=harness();h.run('quantState.config={...catalog.presets.relay.config};');
  assert(h.run('qHistoryBlocked()'));assert.match(h.run('quantView()'),/id="q-run-backtest"[^>]*disabled/);
  h.c.calls=[];h.c.respond=async(...args)=>{h.c.calls.push(args);};h.run('api=respond;');
  await h.run('qStart("backtest")');assert.equal(h.c.calls.length,0);
  console.log('PASS: relay history gaps visibly block backtest without silently removing a rule');
 }
 {
  const h=harness();h.c.result={...fixtures.replay,strategy_name:'<script>bad</script>'};h.run('quantState.result=result;');
  const html=h.run('qResultHTML()');assert.match(html,/&lt;script&gt;/);assert.doesNotMatch(html,/<script>/);
  assert.match(html,/合成演示 · 非真实收益/);assert.match(html,/研究性样本回放/);assert.match(html,/非指数/);
  assert.match(html,/data-q-point=/);
  assert.doesNotMatch(h.run('qEquityChart(quantState.result)'),/NaN|Infinity/);
  h.c.failed={...fixtures.replay,metrics:null,status:'incomplete',failure:'合成除权资料不足'};h.run('quantState.result=failed;');
  assert.match(h.run('qResultHTML()'),/合成除权资料不足/);assert.match(h.run('qReplayHTML(quantState.result)'),/未计算/);
  console.log('PASS: demo labeling, escaped names, inspectable curve and incomplete-result metric gating');
 }
 {
  const h=harness();h.run('quantState.savedConfig=JSON.stringify(quantState.config);quantState.savedName=quantState.name;quantState.strategyId="saved";quantState.version=1;');
  assert.equal(h.run('qDirty()'),false);
  const input=h.listeners.get('input')[0];input({target:{dataset:{qKey:'min_amount',qUnit:'100000000'},value:'3',type:'number'}});
  assert.equal(h.run('quantState.config.min_amount'),300000000);assert(h.run('qDirty()'));
  assert.equal(h.node('#q-version-label').textContent,'未保存参数');
  console.log('PASS: editing parameters marks a saved version dirty without losing unit conversions');
 }
 {
  const h=harness(),calls=[];
  h.run('quantState.strategyId="old-version";quantState.version=1;quantState.savedConfig="different";');
  h.c.respond=async(url,body)=>{calls.push({url,body});return {job:{state:'running',message:'运行中',progress:5}};};
  h.run('api=respond;loadQuant=async function(){};pollQuant=function(){};');
  await h.run('qStart("scan")');
  assert.equal(calls[0].url,'/api/quant/run');assert.equal(calls[0].body.strategy_id,null);assert.equal(calls[0].body.strategy_version,null);
  assert.equal(h.run('quantState.pending'),false);assert.equal(h.run('qBusy()'),true);
  await h.run('qStart("scan")');assert.equal(calls.length,1);
  assert.match(h.run('quantView()'),/fieldset disabled/);
  console.log('PASS: dirty runs store their parameter snapshot and duplicate running jobs are disabled');
 }
 {
  const h=harness(),seen=[];h.c.respond=async(url,body)=>{seen.push({url,body});return {job:{state:'done'}};};
  h.run('api=respond;loadQuant=async function(){};');await h.run('qStart(null,"aaaaaaaaaaaaaaaaaaaa")');
  assert.equal(seen[0].url,'/api/quant/replay');assert.equal(seen[0].body.id,'aaaaaaaaaaaaaaaaaaaa');
  h.c.result=fixtures.scan;h.run('quantState.result=result;');
  assert.match(h.run('qScanHTML(quantState.result)'),/排序分不是上涨概率/);
  const index=fs.readFileSync(path.join(root,'static/index.html'),'utf8');assert(index.indexOf('/quant.js')<index.indexOf('/app.js'));
  console.log('PASS: replay uses frozen-input endpoint; signal results and script ordering are explicit');
 }
 {
  const h=harness(),savedConfig={...fixtures.presets.trend.config,min_amount:300000000};
  const result={...fixtures.replay,strategy_id:'saved-example',strategy_version:2,
   request:{...fixtures.replay.request,strategy_id:'saved-example',strategy_version:2,config:savedConfig}};
  h.c.catalog.strategies=[{id:'saved-example',name:'保存的策略',versions:[{version:2,name:'保存的策略',saved_at:'2026-10-24T10:00:00+08:00',config:savedConfig}]}];
  h.c.catalog.history=[{...result,id:'f'.repeat(20),mode:'active',strategy_name:'另一数据模式'},result];
  const requests=[];
  h.c.respond=async url=>{requests.push(url);return url.includes('/status')?h.c.catalog:result;};
  h.run('api=respond;quantState.config=null;quantState.start="";quantState.end="";');
  await h.run('loadQuant()');
  assert.equal(h.run('quantState.strategyId'),'saved-example');assert.equal(h.run('quantState.version'),2);
  assert.equal(h.run('quantState.config.min_amount'),300000000);assert.equal(h.run('qDirty()'),false);
  assert.equal(h.run('quantState.start'),result.request.start);
  assert(requests.at(-1).includes(result.id));
  assert.doesNotMatch(h.run('qHistoryHTML()'),/另一数据模式/);
  console.log('PASS: opening restores the saved strategy version and keeps real/demo history separate');
 }
 {
  const h=harness();let html=h.run('qStockPickerHTML()');
  assert.match(html,/指定股票回测/);assert.match(html,/q-run-stocks[^>]*disabled/);assert.match(html,/元 \/ 只/);
  h.c.stocks=[{code:'DEMO001',name:'合成一',history_bars:150},{code:'DEMO002',name:'<img src=x>',history_bars:1}];
  h.run('qAddStocks(stocks);qAddStocks(stocks);');assert.equal(h.run('quantState.selected.length'),2);
  html=h.run('qStockPickerHTML()');assert.match(html,/&lt;img src=x&gt;/);assert.doesNotMatch(html,/q-run-stocks[^>]*disabled/);
  h.run('quantState.config={...catalog.presets.relay.config};');assert.match(h.run('qStockPickerHTML()'),/q-run-stocks[^>]*disabled/);
  console.log('PASS: stock selection escapes names, deduplicates and disables unsupported historical conditions');
 }
 {
  const h=harness(),calls=[];h.c.stocks=[{code:'DEMO001',name:'合成一'},{code:'DEMO002',name:'合成二'}];
  h.run('qAddStocks(stocks);quantState.autoHistory=false;');
  h.c.respond=async(url,body)=>{calls.push({url,body});return {job:{state:'running',message:'运行中',progress:1}};};
  h.run('api=respond;loadQuant=async function(){};pollQuant=function(){};');
  await h.run('qStart("stock_backtest")');
  assert.deepEqual(Array.from(calls[0].body.codes),['DEMO001','DEMO002']);assert.equal(calls[0].body.auto_history,false);
  assert.equal(calls[0].body.config.max_positions,5);assert.equal(calls[0].body.type,'stock_backtest');
  await h.run('qStart("stock_backtest")');assert.equal(calls.length,1);
  console.log('PASS: batch requests carry explicit stocks and preserve the saved strategy funding parameters');
 }
 {
  const h=harness();h.c.batch=fixtures.batch;h.run('quantState.result=batch;');
  const html=h.run('qResultHTML()');assert.match(html,/1\/2只完成/);assert.match(html,/历史不足/);
  assert.match(html,/同一区间/);assert.match(html,/独立初始资金/);assert.match(html,/data-q-stock-result="DEMO002"/);
  assert.match(html,/data-q-point=/);assert.doesNotMatch(html,/NaN|Infinity|undefined/);
  h.run('quantState.activeStock="DEMO002";');assert.doesNotMatch(h.run('qStockResultsHTML(batch)'),/q-equity-svg/);
  assert.match(h.run('qStockResultsHTML(batch)'),/没有完整区间指标/);
  h.run('quantState.activeStock="DEMO001";');
  h.c.ev={target:{closest:()=>({dataset:{qPoint:'1'}})}};h.run('qPointReadout(ev)');
  assert(h.node('#q-chart-readout').textContent.startsWith(fixtures.batch.results[0].curve[1].date));
  console.log('PASS: mixed outcomes stay visible; details and chart hover use the selected account');
 }
 {
  const h=harness();h.c.batch=fixtures.batch;h.run('qRestoreStocks(batch);');
  assert.equal(h.run('quantState.selected[1].code'),'DEMO002');assert.equal(h.run('quantState.autoHistory'),true);
  const input=h.listeners.get('input')[0];input({target:{id:'q-stock-start',dataset:{},value:'2026-08-03'}});
  assert.equal(h.run('quantState.start'),'2026-08-03');assert.equal(h.node('#q-start').value,'2026-08-03');
  h.c.catalog.history=[{...fixtures.batch,summary:{selected:2,completed:1}}];
  assert.match(h.run('qHistoryHTML()'),/只完成独立回测/);
  console.log('PASS: stock lists restore with automatic preparation enabled; dates synchronize and history has no combined return');
 }
 {
  const h=harness();let resolveFirst;h.c.respond=()=>new Promise(resolve=>{resolveFirst=resolve;});h.run('api=respond;quantState.stockQuery="旧搜索";');
  const old=h.run('qSearchStocks()');h.run('quantState.stockQuery="新搜索";quantState.stockSearchSequence++;');
  resolveFirst({stocks:[{code:'OLD',name:'旧结果'}]});await old;
  assert.equal(h.run('quantState.stockOptions'),null);
  console.log('PASS: delayed search responses cannot replace newer stock searches');
 }
 {
  const h=harness();h.c.batch={...fixtures.batch,request:{...fixtures.batch.request,config:{...fixtures.batch.request.config,holding_days:8}},strategy_name:'未保存的逐股规则'};
  h.c.catalog.history=[h.c.batch];h.c.respond=async url=>url.includes('/status')?h.c.catalog:h.c.batch;
  h.run('api=respond;quantState.config=null;quantState.start="";quantState.end="";');await h.run('loadQuant()');
  assert.equal(h.run('quantState.config.holding_days'),8);assert.equal(h.run('quantState.selected.length'),2);
  assert.equal(h.run('quantState.name'),'未保存的逐股规则');assert.equal(h.run('qDirty()'),true);
  console.log('PASS: reopening a partially completed batch restores its exact unsaved rules and stock list');
 }
 {
  const h=harness(),seen=[];
  h.c.stocks=fixtures.batch.results.map(s=>({code:s.code,name:s.name,history_bars:s.coverage.history_bars}));
  h.c.checked={ready:1,total:2,stocks:fixtures.batch.results.map(s=>({...s,status:s.status==='completed'?'ready':'needs_history',history_bars:s.coverage.history_bars,message:s.status==='completed'?'已就绪':'本机仅有当日报价'}))};
  h.c.respond=async(url,body)=>{seen.push({url,body});return h.c.checked;};h.run('api=respond;qAddStocks(stocks);');
  assert.equal(seen.length,0);await h.run('qCheckStocks()');
  assert.equal(seen.length,1);assert.equal(seen[0].url,'/api/quant/stocks/check');
  assert.deepEqual(Array.from(seen[0].body.codes),['DEMO001','DEMO002']);
  assert.match(h.run('qReadinessHTML()'),/数据准备 1\/2/);assert.match(h.run('qReadinessHTML()'),/运行时补齐/);
  h.run('quantState.autoHistory=false;');assert.match(h.run('qReadinessHTML()'),/只用本机数据/);
  assert.match(h.run('qStockPickerHTML()'),/仅用本机缓存回测/);
  console.log('PASS: selection checks local coverage without starting downloads; cache-only mode warns before running');
 }
 {
  const h=harness();let release;
  h.c.respond=()=>new Promise(resolve=>{release=resolve;});h.run('api=respond;quantState.selected=[{code:"DEMO001",name:"合成一"}];');
  const old=h.run('qCheckStocks()');
  h.listeners.get('input')[0]({target:{id:'q-stock-start',dataset:{},value:'2026-06-01'}});
  release({ready:1,total:1,stocks:[]});await old;
  assert.equal(h.run('quantState.readiness'),null);assert.equal(h.run('quantState.readinessPending'),true);
  console.log('PASS: a late readiness check cannot apply to a changed date range');
 }
 {
  const h=harness(),calls=[];h.c.batch=fixtures.batch;
  h.run('quantState.result=batch;quantState.config.holding_days=29;quantState.autoHistory=false;');
  assert.match(h.run('qStockResultsHTML(batch)'),/补齐历史并重新回测/);
  h.c.respond=async(url,body)=>{calls.push({url,body});return {job:{state:'running',message:'准备数据'}};};
  h.run('api=respond;loadQuant=async function(){};pollQuant=function(){};');
  await h.listeners.get('click')[0]({target:{closest:()=>({disabled:false,dataset:{qAction:'repair-history'}})}});
  assert.equal(calls[0].url,'/api/quant/run');assert.equal(calls[0].body.auto_history,true);
  assert.equal(calls[0].body.start,fixtures.batch.request.start);assert.equal(calls[0].body.end,fixtures.batch.request.end);
  assert.equal(calls[0].body.config.holding_days,fixtures.batch.request.config.holding_days);
  assert.deepEqual(Array.from(calls[0].body.codes),Array.from(fixtures.batch.request.codes));
  console.log('PASS: history repair runs current data with the archived strategy, dates and stocks while preserving the old result');
 }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
