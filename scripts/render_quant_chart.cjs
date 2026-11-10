// Offline visual QA for the actual SVG chart function; no browser or URL access.
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const sharp=require('sharp');
async function main(){
 const [input,output,stockCode]=process.argv.slice(2);
 if(!input||!output)throw new Error('Usage: render_quant_chart.cjs result.json chart.png');
 const inert={addEventListener(){},classList:{add(){},remove(){},toggle(){}}};
 let result=JSON.parse(fs.readFileSync(input,'utf8'));
 if(result.type==='stock_backtest'){
  const child=stockCode?result.results.find(r=>r.code===stockCode):result.results.find(r=>r.curve?.length);
  if(!child?.curve?.length)throw new Error('所选股票没有可渲染的资金曲线');
  result={...child,id:result.id+'-'+child.code.replace('.','-')};
 }
 const ctx=vm.createContext({document:{querySelector:()=>inert,querySelectorAll:()=>[],addEventListener(){}},
  window:{addEventListener(){}},location:{hash:'#quant'},localStorage:{getItem:()=>null},URL,
  setTimeout:()=>0,clearTimeout(){},requestAnimationFrame(){},result});
 const root=path.resolve(__dirname,'..');
 vm.runInContext(fs.readFileSync(path.join(root,'static/quant.js'),'utf8'),ctx);
 vm.runInContext(fs.readFileSync(path.join(root,'static/app.js'),'utf8').replace(/\nboot\(\);\s*$/,'\n'),ctx);
 const svg=vm.runInContext('qEquityChart(result)',ctx);
 fs.writeFileSync(output.replace(/\.png$/,'.svg'),svg);
 await sharp(Buffer.from(svg)).flatten({background:'#ffffff'}).png().toFile(output);
 console.log(JSON.stringify({output:path.resolve(output),curve_points:ctx.result.curve.length}));
}
main().catch(error=>{console.error(error);process.exitCode=1;});
