// Offline QA: run the application's actual canvas renderer with a saved detail.
// Optional @napi-rs/canvas dependency, resolved from NODE_PATH; no browser/network.
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {createCanvas}=require('@napi-rs/canvas');
const [input,output]=process.argv.slice(2);
if(!input||!output)throw new Error('Usage: render_stock_chart.cjs detail.json chart.png');
const canvas=createCanvas(580,270),getContext=canvas.getContext.bind(canvas);
canvas.getBoundingClientRect=()=>({width:580,height:270});
canvas.getContext=kind=>{const ctx=getContext(kind);ctx.fillStyle='#ffffff';ctx.fillRect(0,0,580,270);return ctx;};
const inert={addEventListener(){},classList:{add(){},remove(){},toggle(){}}};
const context=vm.createContext({
 document:{querySelector:s=>s==='#stock-chart'?canvas:inert,querySelectorAll:()=>[],addEventListener(){}},
 window:{devicePixelRatio:1,addEventListener(){}},location:{hash:'#ladder'},
 localStorage:{getItem:()=>null},setTimeout:()=>0,clearTimeout(){},requestAnimationFrame(){},URL,
 detail:JSON.parse(fs.readFileSync(input,'utf8')),
});
const root=path.resolve(__dirname,'..');
vm.runInContext(fs.readFileSync(path.join(root,'static/stock.js'),'utf8'),context);
vm.runInContext(fs.readFileSync(path.join(root,'static/app.js'),'utf8').replace(/\nboot\(\);\s*$/,'\n'),context);
vm.runInContext('drawCandles(detail)',context);
fs.writeFileSync(output,canvas.toBuffer('image/png'));
console.log(JSON.stringify({output:path.resolve(output),width:canvas.width,height:canvas.height,bars:context.detail.bars.length}));
