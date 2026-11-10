// Synthetic source-level UI checks. Does not launch a browser or access localhost.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const listeners={};
const context = vm.createContext({
 document:{addEventListener(type,fn){listeners[type]=fn;}}, setTimeout:()=>0, clearTimeout(){}, URL,
 state:{view:'review',analysis:{meta:{trade_date:'2026-10-22'}},status:{today:'2026-10-23'}},
 styles:[['balanced','综合共振'],['relay','连板接力'],['trend','趋势短线']],
 icon:()=>'', num:n=>String(n), safeURL:u=>u,
 esc:value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
 toast:()=>{}, $:()=>({innerHTML:''}),
});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/review.js'),'utf8'),context);
const run=code=>vm.runInContext(code,context);

async function main(){
 let html=run('reviewView()');
 assert.match(html,/生成收盘复盘/);
 assert.match(html,/手动生成/);
 assert.match(html,/从收盘数据，到清晰表达/);
 console.log('PASS: first entry has an explicit action and honest empty state');

 context.report={id:'2026-10-22-000000000001',trade_date:'2026-10-22',status:'draft',title:'<img src=x>',
  body:'<script>malicious</script>\n以上内容仅为个人对市场行情的复盘与观点分享，不构成任何投资建议。',
  missing:['公告待核验'],checks:['正文没有股票代码'],sectors:[],char_count:99,
  cutoff_at:'2026-10-22T19:30:00+08:00',source_policy:'测试',sources:[],cover:{status:'not_generated',prompt:'生成横版封面'}};
 run('reviewState.report=report;');
 html=run('reviewReportHTML(reviewState.report)');
 assert.match(html,/待复核草稿/);
 assert.match(html,/&lt;script&gt;/);
 assert.doesNotMatch(html,/<script>/);
 assert.match(html,/复制标题与正文/);
 assert.match(run('reviewCoverHTML(reviewState.report)'),/本次未生成封面/);
 console.log('PASS: draft status, safe escaping, copy/export and missing-cover fallback');

 run('reviewState.tab="evidence";');
 assert.doesNotMatch(run('reviewReportHTML(reviewState.report)'),/review-body/);
 run('reviewState.tab="checks";');
 assert.match(run('reviewReportHTML(reviewState.report)'),/公告待核验/);
 context.failed={status:'verification_failed',failure:'没有同日数据',trade_date:'2026-10-22',body:'',missing:[]};
 assert.match(run('reviewReportHTML(failed)'),/本次未生成行情文稿/);
 assert.doesNotMatch(run('reviewReportHTML(failed)'),/复制标题与正文/);
 console.log('PASS: evidence/checks are separate; failed verification has no copyable market report');

 const settings={enabled:false,time:'19:30',extra_urls:[],closed_dates:[],refresh_quotes:true};
 context.data={settings,job:{state:'running',message:'核对同日收盘'},history:[],schedule_note:'电脑须保持唤醒'};
 run('reviewState.data=data;');
 assert.match(run('reviewView()'),/data-review-action="generate" disabled/);
 console.log('PASS: generation visibly disables duplicate submissions');

 run('reviewState.report=null;');
 context.data={...context.data,job:{state:'done',report_id:context.report.id},history:[context.report]};
 context.response=async url=>url==='/api/reviews'?context.data:context.report;
 run('api=response;paintReview=function(){};');
 await run('loadReviews()');
 assert.equal(run('reviewState.report.id'),context.report.id);
 assert.equal(run('reviewState.seenJob'),context.report.id);
 console.log('PASS: reopening a completed job selects its saved result');

 context.report.cover.status='template_ready';context.report.title_design={angle:'指数与题材',scope:'仅样本',support:'已核验依据',selected_index:0,
  options:[{title:'指数涨4%，强势能延续吗？'},{title:'<img>备选标题'},{title:'另一个事实型标题'}]};
 html=run('reviewCoverHTML(report)');assert.match(html,/当天标题方案/);assert.match(html,/&lt;img&gt;备选标题/);
 assert.match(html,/data-review-action="choose-title"/);assert.match(html,/同步正文标题、封面与导出文件/);
 assert.doesNotMatch(html,/<img>备选标题/);
 console.log('PASS: factual title alternatives are visible, escaped and explain version preservation');

 context.seen=[];context.reply=async(url,body)=>{context.seen.push({url,body});return {...context.report,id:'new-title-version'};};
 run('api=reply;loadReviews=async function(id){seen.push({selected:id});};reviewState.pending=false;');
 await listeners.click({target:{closest:()=>({dataset:{reviewAction:'choose-title',titleIndex:'1'}})}});
 assert.equal(context.seen[0].url,'/api/reviews/title');assert.equal(context.seen[0].body.index,1);
 assert.equal(run('reviewState.report.id'),'new-title-version');assert.equal(run('reviewState.pending'),false);
 console.log('PASS: selecting an alternative updates the archived report and releases pending state');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
