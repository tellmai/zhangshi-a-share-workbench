// State and concurrency tests use a DOM stub; actual browser QA is recorded separately.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const nodes=new Map(),events={};
const node=k=>{if(!nodes.has(k))nodes.set(k,{innerHTML:'',value:'',setAttribute(){},scrollIntoView(){}});return nodes.get(k);};
const h=vm.createContext({window:{addEventListener(){},v2SharedReady:false},document:{addEventListener:(k,f)=>events[k]=f},
  localStorage:{getItem(){return null;},setItem(){}},state:{view:'workbench',mode:'active'},location:{hash:'#workbench'},
  URLSearchParams,Date,setTimeout:()=>0,clearTimeout(){},$ :node,
  esc:x=>String(x??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;'),
  num:x=>String(x??'—'),pct:x=>String(x),toast(){},console});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/workbench.js'),'utf8'),h);
const run=s=>vm.runInContext(s,h);
async function main(){
  assert.match(run('workbenchView()'),/读取研究数据/);
  run("v2State.error='<img src=x>'; ");
  assert.doesNotMatch(run('v2Error()'),/<img/);
  console.log('PASS: honest empty state and escaped structured errors');
  run('v2State.pending=true'); assert.match(run("v2Button('scan','冻结研究')"),/disabled/);
  run('v2State.pending=false;v2State.loading=true'); assert.match(run("v2Button('scan','冻结研究')"),/disabled/);
  console.log('PASS: loading and in-flight submissions disable duplicate actions');
  const resolvers=[];
  h.queue=()=>new Promise(resolve=>resolvers.push(resolve));
  run("v2API=async function(path){if(path==='/health')return queue();return []};state.view='tasks';v2State.view='';");
  const old=run('v2Load()'),newer=run('v2Load()');
  const health=tag=>({version:tag,engine_version:'2',data_directory:'isolated',scheduler:{enabled:false},config:{},provider_capabilities:{}});
  resolvers[1](health('new'));await newer;resolvers[0](health('old'));await old;
  assert.equal(run('v2State.health.version'),'new');
  console.log('PASS: late data responses cannot overwrite a newer page load');
  run("v2State.jobs=[{id:'1',kind:'backup',state:'failed',trade_date:'2026-11-10',attempt:1,error:'<bad>',progress:20,updated_at:null}]");
  const job=run('v2JobHTML()');assert.match(job,/从检查点重试/);assert.doesNotMatch(job,/<bad>/);
  console.log('PASS: failed durable jobs retain an escaped reason and recovery action');
  run("v2State.run={id:'x',status:'incomplete',metrics:null,curve:[],warnings:['缺口'],orders:[],skips:{},manifest:{}};");
  assert.match(run('v2RunHTML()'),/完整区间指标未计算/);
  assert.doesNotMatch(run('v2RunHTML()'),/最大回撤/);
  console.log('PASS: incomplete research never displays complete performance metrics');
  run("v2State.account='demo';v2State.edit=null;state.view='portfolio';v2State.portfolio={confirmed:false,cash:20000,equity:20000,positions:[],closed_pnl:0,issues:[],trade_count:0};v2State.trades=[];");
  const html=run('portfolioV2View()');assert.match(html,/演示账本.*selected|selected.*演示账本/);
  assert.match(html,/人工失效参照价/);assert.match(html,/系统外成交/);
  console.log('PASS: manual facts, demo account separation and explicit exit reference are visible');
  run("v2State.csv='before';v2State.preview={can_commit:true};");
  h.changed={target:{id:'v2-csv',value:'after'}};await events.input(h.changed);
  assert.equal(run('v2State.preview'),null);
  console.log('PASS: edited CSV invalidates its previous commit preview');
  const calls=[];h.capture=(p,b)=>{calls.push([p,b]);return Promise.resolve({});};
  run("v2API=capture;v2SharedRevisions['real:2026-11-10']=2");
  await run("v2SyncWatch('real:600001.SH',true)");await run("v2SyncPlan('real:2026-11-10',{codes:['600001.SH'],note:'n'})");
  assert.equal(calls[0][1].add,'600001.SH');assert.equal(calls[1][1].expected_revision,2);
  console.log('PASS: shared watch changes are incremental and plan edits use revision checks');
  run("v2State.review={reference_account:'shadow',comparable_groups:0,account_incomplete:true,note:'<unsafe>',rows:[{code:'600001.SH',side:'buy',status:'DIFFERENT_EXECUTION_DATE',actual:{qty:100,vwap:10,fees:5},shadow:{qty:100,vwap:9,fees:5},adverse_price_bps:null}],strategy_realized:[]}");
  const review=run('v2ExecutionHTML()');assert.match(review,/执行日期不同/);assert.match(review,/待核对/);assert.doesNotMatch(review,/<unsafe>/);
  assert.match(run('portfolioV2View()'),/执行偏差与盈亏归属/);
  console.log('PASS: execution comparison shows date mismatches and escapes review evidence');
  run("v2State.edit={code:'600001.SH',side:'buy',qty:200,price:10.005,fees:5,executed_at:'2026-10-21T02:00:00+00:00'}");
  assert.match(run('portfolioV2View()'),/name="price"[^>]*step="any"[^>]*value="10.005"/);
  console.log('PASS: an imported multi-decimal average price remains editable without rounding');
}
main().catch(e=>{console.error(e);process.exitCode=1});
