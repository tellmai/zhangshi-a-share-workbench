// Drawer and canvas contracts in a VM; no browser or localhost requests.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

function harness(){
 const nodes=new Map(),timers=new Map(),draws=[];let timerId=0;
 const ctx={scale(){},beginPath(){},stroke(){},closePath(){},fill(){},
  moveTo(x,y){assert(Number.isFinite(x)&&Number.isFinite(y));},
  lineTo(x,y){assert(Number.isFinite(x)&&Number.isFinite(y));},
  fillRect(...args){assert(args.every(Number.isFinite));draws.push(args);},fillText(){}};
 const node=selector=>{
  if(!nodes.has(selector))nodes.set(selector,{innerHTML:'',textContent:'',hidden:true,scrollTop:0,attrs:{},
   classList:{add(){},remove(){},toggle(){}},addEventListener(){},focus(){},
   setAttribute(k,v){this.attrs[k]=v;},getBoundingClientRect:()=>({width:580,height:270}),getContext:()=>ctx});
  return nodes.get(selector);
 };
 const c=vm.createContext({document:{querySelector:node,querySelectorAll:()=>[],addEventListener(){},body:{style:{}},activeElement:{focus(){}}},
  window:{addEventListener(){},devicePixelRatio:2},location:{hash:'#ladder'},localStorage:{getItem:()=>null},
  setTimeout(fn){const id=++timerId;timers.set(id,fn);return id;},clearTimeout(id){timers.delete(id);},requestAnimationFrame:fn=>fn(),URL});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/stock.js'),'utf8'),c);
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8').replace(/\nboot\(\);\s*$/,'\n'),c);
 c.fixture={meta:{trade_date:'2026-10-23',fetched_at:'rev1',mode:'import'},config:{style:'balanced'},stocks:[row('600838.SH'),row('600839.SH')]};
 vm.runInContext("state.analysis=fixture;load=async function(){return true;};",c);
 return {c,node,draws,timers,run:code=>vm.runInContext(code,c),poll:()=>timers.get(vm.runInContext('stockPollTimer',c))()};
}
function row(code){return {code,name:'合成测试股'+code.slice(-5),board:'主板',primary_sector:'测试板块',pct_chg:10,
 current:{close:11,high:11,low:9,amount_yuan:1e9,turnover_rate:5},technical:{sessions:1,volume_ratio:null},
 consecutive:3,components:[],penalties:[],exclusions:['历史不足'],risk_flags:[],verification:{status:'matched'},selected:false};}
function detail(code='600838.SH',ready=false){
 const bars=Array.from({length:ready?60:1},(_,i)=>({date:i===59?'2026-10-23':'2026-08-03',open:10,high:11,low:9,close:11,volume:1000+i,
  ma5:ready?10:null,ma10:ready?9.9:null,ma20:ready?9.8:null}));
 return {code,name:'合成测试股',trade_date:'2026-10-23',snapshot_revision:'rev1',adjustment:ready?'qfq':'raw',needs_history:!ready,
  adjustment_note:ready?'前复权序列':'不复权，历史待补',bars,
  technical:{sessions:ready?150:1,volume_ratio:ready?1.2:null,return5:null,return20:null,extension20:null,breakout20:null,macd:null}};
}
async function main(){
 {
  const h=harness(),calls=[];h.c.respond=async(url,body)=>{calls.push({url,body});return body?{job:{state:'running',message:'读取合成历史'}}:detail();};h.run('api=respond;');
  await h.run('openStock("600838.SH")');
  assert.equal(calls.length,2);assert.equal(calls[1].url,'/api/stock/history');
  assert.equal(calls[1].body.code,'600838.SH');assert.equal(calls[1].body.date,'2026-10-23');
  assert.match(h.node('#stock-chart-status').innerHTML,/读取合成历史/);
  assert.match(h.node('#stock-chart-caption').textContent,/不复权日K · 当前显示1根/);
  assert(h.draws.length>=2);
  h.c.ready=detail('600838.SH',true);h.c.respond=async()=>({job:{state:'done'},detail:h.c.ready});
  h.run('api=respond;load=async function(){state.analysis.stocks[0].exclusions=[];return true;};');
  await h.poll();
  assert.equal(h.node('#stock-chart-status').hidden,true);
  assert.match(h.node('#stock-chart-caption').textContent,/前复权日K · 当前显示60根 · 历史150根/);
  assert.doesNotMatch(h.node('#stock-drawer').innerHTML,/历史不足/);
  assert(h.draws.length>=122);assert.equal(h.node('#stock-chart').width,1160);
  console.log('PASS: opening a missing-history stock starts hydration; completion paints 60 bars and refreshes exclusions');
 }
 {
  const h=harness();h.c.respond=async(url,body)=>body?{job:{state:'running'}}:detail();h.run('api=respond;');
  await h.run('openStock("600838.SH")');
  h.c.respond=async()=>({job:{state:'error',message:'合成接口暂时不可用'},detail:detail()});
  h.run('api=respond;');
  await h.poll();
  assert.match(h.node('#stock-chart-status').innerHTML,/合成接口暂时不可用/);
  assert.match(h.node('#stock-chart-status').innerHTML,/重新加载历史/);
  assert.match(h.node('#stock-chart-caption').textContent,/不复权/);
  assert.equal(h.run('currentDetail.bars.length'),1);
  console.log('PASS: failed hydration retains the dated quote, names the failure and offers retry');
 }
 {
  const h=harness();let resolve;const pending=new Promise(done=>{resolve=done;});
  h.c.respond=()=>pending;h.run('api=respond;');
  const opening=h.run('openStock("600838.SH")');h.run('closeDrawer()');resolve(detail());await opening;
  assert.equal(h.run('currentDetail'),null);assert.equal(h.node('#stock-drawer').hidden,true);
  assert.equal(h.run('activeStock'),null);
  console.log('PASS: closing the drawer invalidates a late response');
 }
 {
  const h=harness();let resolve;const pending=new Promise(done=>{resolve=done;});
  h.c.respond=url=>url.includes('600838')?pending:Promise.resolve(detail('600839.SH',true));h.run('api=respond;');
  const first=h.run('openStock("600838.SH")');await h.run('openStock("600839.SH")');resolve(detail('600838.SH',true));await first;
  assert.equal(h.run('currentDetail.code'),'600839.SH');
  assert.equal(h.run('activeStock.code'),'600839.SH');
  console.log('PASS: switching stocks prevents a late previous-stock chart from overwriting the current one');
 }
 {
  const h=harness(),calls=[];h.c.respond=async(url,body)=>{calls.push(url);return {...detail('600838.SH',true),needs_history:false};};
  h.run('api=respond;state.mode="demo";');await h.run('openStock("600838.SH")');assert.equal(calls.length,1);
  const l=h.run('candleLayout([{high:11,low:11,ma5:null,ma10:null,ma20:null}],580,270)');
  assert(Number.isFinite(l.y(11)));assert(l.max>l.min);assert(l.step<20);assert(l.x(0)>500);
  console.log('PASS: demo needs no real-data job; a single flat candle has bounded width and a finite scale');
 }
 {
  const h=harness();h.c.respond=async()=>({...detail('600838.SH',true),trade_date:'2026-10-24'});h.run('api=respond;');
  await h.run('openStock("600838.SH")');assert.match(h.node('#stock-chart-status').innerHTML,/行情日期已切换/);
  assert.equal(h.run('currentDetail'),null);
  console.log('PASS: a different-date response cannot be shown under the previous trading date');
 }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
