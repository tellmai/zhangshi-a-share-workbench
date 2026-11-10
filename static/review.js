'use strict';
const reviewState={data:null,report:null,tab:'copy',loading:false,loadedAt:0,pending:false,request:0,date:null,timer:null,seenJob:null};
const reviewArtifact=(name,id=reviewState.report?.id)=>`/api/reviews/artifact?id=${encodeURIComponent(id||'')}&name=${encodeURIComponent(name)}`;
const reviewCoverURL=()=>typeof window!=='undefined'&&window.__ZHANGSHI_DESKTOP__&&reviewState.report?.desktop_cover?reviewState.report.desktop_cover:reviewArtifact('cover.svg');
const reviewLabels={draft:'待复核草稿',verification_failed:'核验未通过',skipped:'休市跳过'};

function reviewView(){
 const data=reviewState.data, report=reviewState.report, settings=data?.settings;
 const running=reviewState.pending||data?.job.state==='running';
 const date=reviewState.date||state.analysis?.meta.trade_date||state.status?.today||'';
 return `<div class="review-workspace">
  <section class="review-intro"><div><div class="eyebrow">DAILY CLOSING EDITOR</div><h2>今天的市场，整理成一篇有依据的复盘。</h2><p>三种短线视角 · 最多三个方向 · 独立证据页 · 横版封面</p></div><span class="review-time">${icon('clock',19)}<b>${esc(settings?.time||'19:30')}</b><small>北京时间 · ${settings?.enabled?'自动运行已开启':'手动生成'}</small></span></section>
  <div class="review-toolbar"><label>复盘交易日<input id="review-date" type="date" value="${esc(date)}" max="${esc(state.status?.today||'')}" ${running?'disabled':''}></label><label class="checkbox"><input id="review-refresh" type="checkbox" ${running?'disabled':''}> 生成前更新行情</label><div class="review-toolbar-actions"><button class="button" data-review-action="reload" ${reviewState.loading?'disabled':''}>${icon('refresh',15)}读取记录</button><button class="button primary" data-review-action="generate" ${running?'disabled':''}>${running?'<span class="spinner"></span>':icon('spark',16)}${running?'正在核验与生成':'生成收盘复盘'}</button></div></div>
  <div class="review-job ${data?.job.state==='error'?'error':''}" role="status" aria-live="polite">${running?'<span class="spinner"></span>':icon('shield',16)}<span>${esc(reviewState.loading&&!data?'正在读取复盘记录…':data?.job.message||'使用真实盘后快照。日期不符、关键来源冲突时会暂停相应结论。')}</span></div>
  <div class="review-layout"><div class="review-main">
   ${report?reviewReportHTML(report):`<section class="panel review-welcome"><span class="review-welcome-icon">${icon('spark',30)}</span><h2>从收盘数据，到清晰表达。</h2><p>首次生成后，这里会展示完整标题、可复制正文和当天封面。<br>文稿使用免费数据，缺失字段会明确列出，发布前请复核。</p><div class="review-steps"><span><b>01</b>核验来源</span><span><b>02</b>梳理强势方向</span><span><b>03</b>生成文稿与封面</span></div></section>`}
   ${report?.title?reviewCoverHTML(report):''}
  </div><aside class="review-side">
   <section class="panel"><div class="panel-heading"><h2>复盘存档</h2><span class="tag">${data?.history.length||0}次</span></div><div class="review-history">${data?.history.length?data.history.slice(0,30).map(h=>`<button class="review-history-item ${h.id===report?.id?'active':''}" data-review-id="${esc(h.id)}"><span>${esc(h.trade_date)}<small>${esc(reviewLabels[h.status]||h.status)}</small></span><b>${esc(h.title||'本次仅保存核验结果')}</b><small>${esc((h.created_at||'').slice(11,19))} · ${h.char_count?`${h.char_count}字`:'无文稿'}</small></button>`).join(''):'<p class="review-side-note">还没有存档。每次生成保留独立版本，方便比较与回看。</p>'}</div></section>
   <section class="panel"><div class="panel-heading"><h2>本机运行设置</h2>${icon('clock',16)}</div>${settings?`<form id="review-settings-form" class="review-settings"><label class="review-toggle"><span>每天自动生成<small>休市静默跳过</small></span><input name="enabled" type="checkbox" ${settings.enabled?'checked':''}></label><label>北京时间<input name="time" type="time" value="${esc(settings.time)}" min="15:00" required></label><label class="checkbox"><input name="refresh_quotes" type="checkbox" ${settings.refresh_quotes?'checked':''}> 自动生成前更新行情</label><details><summary>来源与休市补充</summary><label>补充文章链接<small>每行一个，最多8个；只引用实际读取的同日收盘正文。</small><textarea name="extra_urls" rows="4" placeholder="https://www.cls.cn/detail/…">${esc(settings.extra_urls.join('\n'))}</textarea></label><label>临时休市日期<small>每行一个 YYYY-MM-DD，优先于年度日历。</small><textarea name="closed_dates" rows="2">${esc(settings.closed_dates.join('\n'))}</textarea></label></details><button class="button wide" type="submit">保存运行设置</button><p class="review-side-note">${esc(data.schedule_note)}<br>默认关闭；原任务的后台启用状态未核实，开启前请自行避免重复运行。</p></form>`:'<div class="review-side-note">设置读取中。</div>'}</section>
   <section class="review-provenance"><b>已迁入任务描述</b><p>“A股抖音复盘收盘”的最新格式、单段明日判断和指定免责声明已用于此工作流。生成结果仅在本机保存。</p></section>
  </aside></div></div>`;
}

function reviewBodyMarkup(report){
 const sectors=new Set(report.sectors.map(s=>s.name));
 return report.body.split(/\n\n+/).map(text=>{
  if(/^[一二三四五]、/.test(text))return `<h3>${esc(text)}</h3>`;
  if(sectors.has(text))return `<h4>${esc(text)}</h4>`;
  const cls=text.startsWith('以上内容仅为个人')?'review-disclaimer':text.startsWith('#')?'review-hashtags':'';
  return `<p class="${cls}">${esc(text)}</p>`;
 }).join('');
}
function reviewReportHTML(report){
 if(!report.body)return `<section class="panel review-failure"><span class="tag danger">${esc(reviewLabels[report.status]||'核验结果')}</span><h2>本次未生成行情文稿</h2><p>${esc(report.failure||'关键证据不足。')}</p><p>交易日期：${esc(report.trade_date)} · 截止：${esc(report.cutoff_at)}</p>${report.missing?.length?`<ul>${report.missing.map(m=>`<li>${esc(m)}</li>`).join('')}</ul>`:''}<button class="button" data-review-action="checks">导出核验记录</button></section>`;
 const tabs=[['copy','复盘文稿'],['evidence','来源证据'],['checks',`待核验 ${report.missing.length}`]];
 return `<section class="panel review-editor"><div class="review-editor-head"><div><span class="tag warm">待复核草稿</span><span class="review-meta">${esc(report.trade_date)} · ${report.char_count}字 · ${report.sectors.length}个方向</span></div><button class="button small" data-review-action="bundle">${icon('download',14)}完整资料</button></div><div class="review-tabs" role="tablist" aria-label="复盘内容">${tabs.map(([key,label])=>`<button role="tab" aria-selected="${reviewState.tab===key}" data-review-tab="${key}" class="${reviewState.tab===key?'active':''}">${esc(label)}</button>`).join('')}</div>
 ${reviewState.tab==='copy'?`<div class="review-copy-tools"><span>正文不含代码、来源表与交易指令</span><button class="button small" data-review-action="copy">复制标题与正文</button><a class="button small" href="${reviewArtifact('douyin.txt')}" download="抖音复盘-${esc(report.trade_date)}.txt">下载TXT</a></div><article class="review-copy"><div class="eyebrow">A SHARE / AFTER CLOSE</div><h2>${esc(report.title)}</h2><div class="review-body">${reviewBodyMarkup(report)}</div></article>`:reviewState.tab==='evidence'?reviewEvidenceHTML(report):reviewChecksHTML(report)}
 </section>`;
}

function reviewEvidenceHTML(report){
 return `<div class="review-evidence"><p class="review-evidence-intro">截止 ${esc(report.cutoff_at.replace('T',' '))}。${esc(report.source_policy)}。正文不展示来源表，导出资料会保留本页。</p>${report.sources.map(s=>`<section class="review-source"><div><span class="tag ${s.kind==='news'?'blue':'mint'}">${esc(s.publisher||s.kind)}</span><span class="review-source-state">${esc(s.status)}</span></div><h3>${s.url?`<a href="${safeURL(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title)} ↗</a>`:esc(s.title)}</h3><p>行情日期 ${esc(s.market_date)} · 发布/更新 ${esc(s.published_at||'未提供')}<br>读取 ${esc(s.retrieved_at||'未提供')}</p><p>${esc(s.scope||'未说明')}</p><div class="review-source-fields">${s.verified_fields?.length?s.verified_fields.map(f=>`<span>${esc(f)}</span>`).join(''):'没有形成交叉核验字段，不用于具体市场数值'}</div></section>`).join('')}
 <a class="button" href="${reviewArtifact('sources.json')}" download="复盘来源-${esc(report.trade_date)}.json">下载来源记录</a></div>`;
}

function reviewChecksHTML(report){
 return `<div class="review-checks"><div class="review-checks-note">免费数据版会生成待复核草稿。数据不足时缩减内容，保留空项和失败记录；规则判断尚未经过预测效果验证。</div><h3>本次通过</h3><ul class="review-passed">${report.checks.map(c=>`<li>${icon('shield',14)}${esc(c)}</li>`).join('')}</ul><h3>仍需核验</h3><ul>${report.missing.map(m=>`<li>${esc(m)}</li>`).join('')}</ul><details><summary>代表股与三种风格依据</summary>${report.sectors.map(s=>`<div class="review-representatives"><h4>${esc(s.name)}<small>仅本次已展开的同类板块</small></h4>${s.representatives.map(r=>`<p><b>${esc(r.name)}</b><span>${esc(styles.find(x=>x[0]===r.style)?.[1])} · ${esc(r.role)} · ${num(r.score,1)}分</span></p><small>${esc(r.risks.join('；'))}</small>`).join('')}</div>`).join('')}</details><a class="button" href="${reviewArtifact('checks.json')}" download="复盘核验-${esc(report.trade_date)}.json">下载核验记录</a></div>`;
}

function reviewCoverHTML(report){
 const ready=report.cover.status==='template_ready';
 return `<section class="panel review-cover"><div class="panel-heading"><h2>当天横版封面 <span class="sub">16:9 / 1920 × 1080</span></h2><span class="tag">${ready?'摄影底图 + 当日排版':'尚未生成'}</span></div>${ready?`<img src="${reviewCoverURL()}" alt="${esc(report.trade_date+' '+report.title+'，示意画面，非实时行情')}" width="1920" height="1080">${reviewTitleChoicesHTML(report)}<div class="review-cover-actions"><button class="button primary" data-review-action="png">${icon('download',15)}导出PNG封面</button><a class="button" href="${reviewArtifact('cover.svg')}" download="复盘封面-${esc(report.trade_date)}.svg">下载SVG</a><span>示意画面，非实时行情</span></div>`:'<p class="review-side-note">本次未生成封面。可复制以下提示词，在图像工具中生成。</p>'}<details><summary>封面提示词与制作记录</summary><p>${esc(report.cover.method||'未调用图像生成')}</p><p>${esc(report.cover.reference)}</p><pre>${esc(report.cover.prompt)}</pre><button class="button small" data-review-action="prompt">复制封面提示词</button></details></section>`;
}
function reviewTitleChoicesHTML(report){const d=report.title_design;if(!d)return '';return `<div class="review-title-choices"><div><b>当天标题方案</b><span>${esc(d.angle)}</span></div><p>${esc(d.support)}<br>${esc(d.scope)}</p>${d.options.map((v,i)=>`<button class="review-title-choice ${i===d.selected_index?'active':''}" data-review-action="choose-title" data-title-index="${i}" ${reviewState.pending?'disabled':''}><small>${i===d.selected_index?'当前使用':i===0?'推荐':'备选'}</small><span>${esc(v.title)}</span>${i===d.selected_index?icon('check',13):icon('arrow',13)}</button>`).join('')}<small>选择后另存一版，同步正文标题、封面与导出文件；保留原稿。</small></div>`;}

function paintReview(){if(state.view==='review')$('#content').innerHTML=reviewView();}
function ensureReviewLoaded(){if(!reviewState.loading&&Date.now()-reviewState.loadedAt>5000)loadReviews();}
async function loadReviews(selectId=null){
 if(reviewState.loading)return;
 reviewState.loading=true;
 try{
  const data=await api('/api/reviews');reviewState.data=data;reviewState.loadedAt=Date.now();
  const id=selectId||(data.job.report_id&&data.job.report_id!==reviewState.seenJob?data.job.report_id:(!reviewState.report?data.history[0]?.id:null));
  if(id)reviewState.report=await api(`/api/reviews/report?id=${encodeURIComponent(id)}`);
  reviewState.seenJob=data.job.report_id;
  if(data.job.state==='running')scheduleReviewPoll();
 }catch(error){toast(`复盘记录读取失败：${error.message}`);}
 finally{reviewState.loading=false;paintReview();}
}
function scheduleReviewPoll(){clearTimeout(reviewState.timer);reviewState.timer=setTimeout(async()=>{
 if(state.view!=='review')return;
 try{
  const data=await api('/api/reviews');reviewState.data=data;reviewState.loadedAt=Date.now();
  if(data.job.state==='running')scheduleReviewPoll();
  else if(data.job.report_id&&data.job.report_id!==reviewState.report?.id){reviewState.report=await api(`/api/reviews/report?id=${encodeURIComponent(data.job.report_id)}`);reviewState.seenJob=data.job.report_id;reviewState.tab='copy';toast(data.job.message);}
  paintReview();
 }catch(error){toast(`复盘状态读取失败：${error.message}`);}
 },1800);}
async function copyReviewText(value){
 try{await navigator.clipboard.writeText(value);toast('已复制');}
 catch{const field=document.createElement('textarea');field.value=value;field.style.position='fixed';field.style.opacity='0';document.body.append(field);field.select();const ok=document.execCommand('copy');field.remove();if(!ok)throw new Error('复制失败，请使用TXT下载');toast('已复制');}
}
function exportReviewBundle(){
 if(!reviewState.report?.id){toast('请先生成或选择一份复盘');return;}
 const a=document.createElement('a');a.href=reviewArtifact('bundle.zip');a.download=`复盘资料-${reviewState.report.trade_date}.zip`;a.click();
}
async function exportCoverPNG(){
 const r=reviewState.report;if(r?.cover.status!=='template_ready')throw new Error('本次封面尚未生成');
 const image=new Image();image.src=reviewCoverURL();await image.decode();
 if(image.naturalWidth!==1920||image.naturalHeight!==1080)throw new Error('封面尺寸检查失败');
 const canvas=document.createElement('canvas');canvas.width=1920;canvas.height=1080;canvas.getContext('2d').drawImage(image,0,0,1920,1080);
 const blob=await new Promise((resolve,reject)=>canvas.toBlob(b=>b?resolve(b):reject(new Error('PNG导出失败，可下载SVG封面')),'image/png'));
 download(`复盘封面-${r.trade_date}.png`,blob,'image/png');toast('已导出1920×1080 PNG封面');
}
document.addEventListener('click',async event=>{
 const b=event.target.closest('[data-review-action],[data-review-tab],[data-review-id]');if(!b)return;
 try{
  if(b.dataset.reviewTab){reviewState.tab=b.dataset.reviewTab;paintReview();return;}
  if(b.dataset.reviewId){const id=++reviewState.request;const r=await api(`/api/reviews/report?id=${encodeURIComponent(b.dataset.reviewId)}`);if(id===reviewState.request){reviewState.report=r;reviewState.tab='copy';paintReview();}return;}
  const r=reviewState.report;
  switch(b.dataset.reviewAction){
   case 'choose-title':{
    if(!r||reviewState.pending)return;
    reviewState.pending=true;paintReview();
    try{const updated=await api('/api/reviews/title',{id:r.id,index:Number(b.dataset.titleIndex)});reviewState.report=updated;await loadReviews(updated.id);toast('标题与封面已同步，原稿保留');}finally{reviewState.pending=false;paintReview();}break;
   }
   case 'reload':await loadReviews();break;
   case 'generate':{
    if(reviewState.pending||reviewState.data?.job.state==='running')return;
    const date=$('#review-date').value,refresh=$('#review-refresh').checked;reviewState.date=date;reviewState.pending=true;paintReview();
    try{const result=await api('/api/reviews/generate',{date,refresh});toast(result.message);await loadReviews();}finally{reviewState.pending=false;paintReview();}break;
   }
   case 'copy':if(r?.body)await copyReviewText(r.title+'\n\n'+r.body);break;
   case 'prompt':if(r?.cover.prompt)await copyReviewText(r.cover.prompt);break;
   case 'bundle':exportReviewBundle();break;
   case 'png':await exportCoverPNG();break;
   case 'checks':if(r)download(`核验记录-${r.trade_date}.json`,JSON.stringify(r,null,2),'application/json');break;
  }
 }catch(error){toast(error.message);}
});
document.addEventListener('submit',async event=>{
 if(event.target.id!=='review-settings-form')return;event.preventDefault();
 const button=event.target.querySelector('button[type="submit"]');button.disabled=true;
 try{
  const f=new FormData(event.target);const lines=name=>String(f.get(name)||'').split(/\r?\n/).map(x=>x.trim()).filter(Boolean);
  await api('/api/reviews/settings',{enabled:f.has('enabled'),time:f.get('time'),refresh_quotes:f.has('refresh_quotes'),extra_urls:lines('extra_urls'),closed_dates:lines('closed_dates')});
  await loadReviews();toast('本机运行设置已保存');
 }catch(error){button.disabled=false;toast(error.message);}
});
document.addEventListener('change',event=>{if(event.target.id==='review-date')reviewState.date=event.target.value;});

