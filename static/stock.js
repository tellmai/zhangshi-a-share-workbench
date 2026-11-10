'use strict';
// Request tokens protect the drawer when switching stocks or closing it mid-load.
let stockRequest=0,stockPollTimer=null,activeStock=null;
function cancelStockRequest(){stockRequest++;activeStock=null;clearTimeout(stockPollTimer);stockPollTimer=null;currentDetail=null;}
function isCurrentStock(token,code){return token===stockRequest&&activeStock?.code===code&&!$('#stock-drawer').hidden;}
function stockChartStatus(kind,message=''){
 const node=$('#stock-chart-status');if(!node)return;
 node.hidden=kind==='ready';node.className=`stock-chart-status ${kind}`;
 node.innerHTML=kind==='ready'?'':`<div>${kind==='loading'||kind==='reading'?'<span class="spinner"></span>':icon('flag',22)}<b>${kind==='loading'?'正在补齐历史行情':kind==='reading'?'正在读取日K线':'历史行情暂未齐备'}</b><p>${esc(message)}</p>${kind==='error'?'<button class="button small" data-action="retry-stock-history">重新加载历史</button>':''}</div>`;
}
function paintStockDetail(detail){
 currentDetail=detail;
 const caption=$('#stock-chart-caption');
 if(caption)caption.textContent=`${detail.adjustment==='qfq'?'前复权':'不复权'}日K · 当前显示${detail.bars.length}根 · 历史${detail.technical.sessions||0}根 · ${detail.adjustment_note}`;
 const canvas=$('#stock-chart');
 if(canvas)canvas.setAttribute('aria-label',`${detail.name} ${detail.adjustment==='qfq'?'前复权':'不复权'}日K线，${detail.bars.length}根，含成交量${detail.adjustment==='qfq'?'和可用均线':''}`);
 requestAnimationFrame(()=>{if(currentDetail===detail&&!$('#stock-drawer').hidden)drawCandles(detail);});
}
function repaintStockDrawer(detail){
 const row=state.analysis.stocks.find(s=>s.code===detail.code);if(!row)return;
 const top=$('#stock-drawer').scrollTop;
 renderStockDrawer(row,detail);$('#stock-drawer').scrollTop=top;paintStockDetail(detail);
}
async function openStock(code){
 const row=state.analysis.stocks.find(s=>s.code===code);if(!row)return;
 cancelStockRequest();const token=stockRequest;
 activeStock={code,mode:state.mode,date:state.analysis.meta.trade_date};
 lastFocus=document.activeElement;currentDetail=null;
 $('#drawer-overlay').hidden=false;$('#stock-drawer').hidden=false;document.body.style.overflow='hidden';
 renderStockDrawer(row);$('#stock-drawer').scrollTop=0;
 $('#stock-drawer [data-action="close-drawer"]').focus();
 stockChartStatus('reading','正在读取本地收盘快照…');
 try{
  const detail=await api(`/api/stock?code=${encodeURIComponent(code)}&mode=${encodeURIComponent(activeStock.mode)}`);
  if(!isCurrentStock(token,code))return;
  if(detail.trade_date!==activeStock.date)throw new Error('行情日期已切换，请读取最新快照后重新打开股票');
  repaintStockDrawer(detail);
  if(detail.needs_history){await startStockHistory(token,code);}
  else if(detail.snapshot_revision!==(state.analysis.meta.history_updated_at||state.analysis.meta.fetched_at))await finishStockHistory(token,code,detail);
  else stockChartStatus('ready');
 }catch(error){if(isCurrentStock(token,code))stockChartStatus('error',error.message);}
}
async function finishStockHistory(token,code,detail){
 if(!isCurrentStock(token,code))return;
 // Refresh scoring and missing-data reasons with the same current filter config.
 const updated=await load(state.analysis.config);
 if(!isCurrentStock(token,code))return;
 if(state.analysis.meta.trade_date!==detail.trade_date){stockChartStatus('error','行情日期已切换，请重新打开股票');return;}
 repaintStockDrawer(detail);stockChartStatus('ready');
 if(!updated)toast('日K线已补齐，评分刷新失败，可稍后读取最新快照');
}
function pollStockHistory(token,code){
 clearTimeout(stockPollTimer);
 stockPollTimer=setTimeout(async()=>{
  if(!isCurrentStock(token,code))return;
  try{
   const context=activeStock;
   const result=await api(`/api/stock/history?code=${encodeURIComponent(code)}&mode=${encodeURIComponent(context.mode)}&date=${encodeURIComponent(context.date)}`);
   if(!isCurrentStock(token,code))return;
   if(result.job.state==='done')await finishStockHistory(token,code,result.detail);
   else if(result.job.state==='error')stockChartStatus('error',result.job.message);
   else{stockChartStatus('loading',result.job.message||'正在等待历史行情接口…');pollStockHistory(token,code);}
  }catch(error){if(isCurrentStock(token,code))stockChartStatus('error',error.message);}
 },1200);
}
async function startStockHistory(token=stockRequest,code=activeStock?.code){
 if(!code||!isCurrentStock(token,code))return;
 stockChartStatus('loading','首次打开需获取历史日线，完成后会缓存在本机。');
 try{
  const context=activeStock;
  const result=await api('/api/stock/history',{code,mode:context.mode,date:context.date});
  if(!isCurrentStock(token,code))return;
  if(result.job.state==='done'&&result.detail)await finishStockHistory(token,code,result.detail);
  else{stockChartStatus('loading',result.job.message);pollStockHistory(token,code);}
 }catch(error){if(isCurrentStock(token,code))stockChartStatus('error',error.message);}
}
document.addEventListener('click',event=>{if(event.target.closest('[data-action="retry-stock-history"]'))startStockHistory();});

function candleLayout(bars,w,h){
 const left=45,right=w-12,top=23,bottom=h*.71,volumeTop=h*.81,volumeBottom=h-23;
 const step=(right-left)/Math.max(30,bars.length);
 const points=bars.flatMap(b=>[b.high,b.low,b.ma5,b.ma10,b.ma20]).filter(v=>Number.isFinite(v));
 const high=Math.max(...points),low=Math.min(...points),pad=Math.max((high-low)*.08,high*.005,.01);
 const max=high+pad,min=low-pad;
 return {left,right,top,bottom,volumeTop,volumeBottom,step,min,max,
  x:i=>right-step*(bars.length-i-.5),y:v=>bottom-(v-min)/(max-min)*(bottom-top)};
}
function drawCandles(detail){
 const canvas=$('#stock-chart');if(!canvas)return;
 const {ctx,w,h}=canvasContext(canvas),bars=detail.bars;
 if(!bars.length){ctx.fillStyle='#8a9f9c';ctx.fillText('暂无有效日线，可重新加载历史行情',30,95);return;}
 const {left,right,top,bottom,volumeTop,volumeBottom,step,min,max,x,y}=candleLayout(bars,w,h);
 for(let i=0;i<=4;i++){
  const price=min+(max-min)*i/4;
  ctx.strokeStyle='#edf1f1';ctx.beginPath();ctx.moveTo(left,y(price));ctx.lineTo(right,y(price));ctx.stroke();
  ctx.fillStyle='#9baca8';ctx.fillText(num(price,2),0,y(price)+3);
 }
 const maxVolume=Math.max(...bars.map(b=>b.volume))||1;
 const bodyWidth=Math.max(1,Math.min(8,step*.58));
 bars.forEach((bar,i)=>{
  const xx=x(i),up=bar.close>=bar.open,cl=up?'#cf7070':'#5c9c81';
  ctx.strokeStyle=cl;ctx.fillStyle=cl;ctx.beginPath();ctx.moveTo(xx,y(bar.high));ctx.lineTo(xx,y(bar.low));ctx.stroke();
  ctx.fillRect(xx-bodyWidth/2,Math.min(y(bar.open),y(bar.close)),bodyWidth,Math.max(1,Math.abs(y(bar.open)-y(bar.close))));
  const vh=bar.volume/maxVolume*(volumeBottom-volumeTop);ctx.fillStyle=up?'#e9b5b2':'#b0d1bd';
  ctx.fillRect(xx-bodyWidth/2,volumeBottom-vh,bodyWidth,vh);
 });
 const lines=[['ma5','#d1a65b'],['ma10','#80a6c3'],['ma20','#ad91b6']].filter(([key])=>bars.some(b=>b[key]!=null));
 lines.forEach(([key,c])=>{
  ctx.beginPath();let active=false;
  bars.forEach((bar,i)=>{if(bar[key]==null){active=false;return;}active?ctx.lineTo(x(i),y(bar[key])):ctx.moveTo(x(i),y(bar[key]));active=true;});
  ctx.strokeStyle=c;ctx.lineWidth=1.2;ctx.stroke();ctx.lineWidth=1;
 });
 ctx.fillStyle='#99aaa5';
 if(bars.length>1)ctx.fillText(bars[0].date.slice(5),Math.max(left,x(0)-12),h-5);
 ctx.fillText(bars.at(-1).date.slice(5),right-32,h-5);
 ctx.fillText('成交量',0,volumeTop+9);
 ctx.fillText(`${num(maxVolume/1e4,0)}万股`,left,volumeTop-3);
 lines.forEach(([key,c],i)=>{ctx.fillStyle=c;ctx.fillText(key.toUpperCase(),left+i*50,11);});
 if(detail.adjustment==='raw'){ctx.fillStyle='#a38e64';ctx.fillText('不复权 · 历史待补齐',left,11);}
}
