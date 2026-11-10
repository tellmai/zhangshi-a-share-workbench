// Offline contracts for the ranking the user sees; no origin/browser access.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const nodes=new Map(),listeners=[];
const node=key=>{if(!nodes.has(key))nodes.set(key,{innerHTML:'',textContent:'',hidden:false,addEventListener(){},classList:{add(){},remove(){},toggle(){}}});return nodes.get(key);};
const c=vm.createContext({document:{querySelector:node,querySelectorAll:()=>[],addEventListener(type,fn){if(type==='click')listeners.push(fn);}},window:{addEventListener(){}},location:{hash:'#overview'},localStorage:{getItem:()=>null},URL,setTimeout(){},clearTimeout(){},requestAnimationFrame(){}});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8').replace(/\nboot\(\);\s*$/,'\n'),c);
const run=code=>vm.runInContext(code,c);
const sector=(id,pct,score)=>({id,name:id,type:'industry',classification:'合成分类',avg_pct:pct,daily_pct:pct,score,complete:true,breadth:90,limit_up:1,members:[],available_count:10,member_count:10,coverage:100,expanded:true});
const slow=sector('低涨幅高评分',1,99),fast=sector('最高涨幅',6,60),down=sector('下跌不凑榜',-2,40);
c.fixture={config:{style:'trend',sector_type:'industry'},meta:{trade_date:'2026-10-23',mode:'live'},sectors:[slow,fast,down],stocks:[],candidates:[],sector_leaders:{rows:[fast,slow],basis:'同日成分等权涨幅',scope:'仅已展开板块；未覆盖全市场',asof_date:'2026-10-23',complete_universe:false,available_count:3,expanded_count:3}};
run('state.analysis=fixture;');
let html=run('heatmap()');
assert.equal(run('state.sectorOrder'),'daily');
assert(html.indexOf('最高涨幅')<html.indexOf('低涨幅高评分'));
assert(!html.includes('下跌不凑榜'));assert.match(html,/当日领涨/);assert.match(html,/行情日 2026-10-23/);assert.match(html,/未覆盖全市场/);assert.match(html,/data-count="2"/);
assert(!/undefined|NaN/.test(html));
console.log('PASS: overview defaults to dated daily leaders, with no negative fillers or full-market claim');

(async()=>{
 c.draws=0;run('render=function(){draws++;};');
 await listeners[0]({target:{closest:()=>({dataset:{sectorOrder:'strength'},classList:{contains:()=>false}})}});
 assert.equal(run('state.sectorOrder'),'strength');assert.equal(c.draws,1);
 html=run('heatmap()');assert(html.indexOf('低涨幅高评分')<html.indexOf('最高涨幅'));
 assert(run('sectorsView()').indexOf('低涨幅高评分')<run('sectorsView()').indexOf('最高涨幅'));
 run('state.sectorOrder="daily";');html=run('sectorsView()');assert(html.indexOf('最高涨幅')<html.indexOf('低涨幅高评分'));
 console.log('PASS: ranking switch changes overview and sector cards together without altering stock style');

 c.fixture.sector_leaders.rows=[{id:'unknown',name:'<img>未展开领涨',daily_pct:8,type:'industry',classification:'同日指数',expanded:false,members:[]}];
 c.fixture.sector_leaders.complete_universe=true;c.fixture.sector_leaders.available_count=20;
 html=run('heatmap()');assert.match(html,/&lt;img&gt;/);assert.match(html,/data-sector-unexpanded=/);assert.match(html,/同一供应商完整分类/);
 assert.match(run('sectorsView()'),/成分尚未展开/);assert(!/undefined|NaN/.test(run('sectorsView()')));
 console.log('PASS: full-list leaders can precede expanded samples while missing constituents stay explicit');

 c.fixture.sector_leaders.rows=[];c.fixture.sector_leaders.available_count=3;
 assert.match(run('heatmap()'),/暂无上涨板块/);assert.doesNotMatch(run('heatmap()'),/class="heatmap"/);
 c.fixture.meta.mode='demo';assert.match(run('heatmap()'),/合成演示 · 非真实当日行情/);
 run('state.status={active_meta:{mode:"live"}};');assert.match(run('heatmap()'),/读取真实收盘/);
 c.fixture.sector_leaders.available_count=0;assert.match(run('heatmap()'),/板块同日数据不足/);
 console.log('PASS: falling-market, missing-data and demo states remain explicit');
})().catch(error=>{console.error(error);process.exitCode=1;});
