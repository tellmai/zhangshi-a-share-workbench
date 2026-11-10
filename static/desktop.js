'use strict';
window.__ZHANGSHI_DESKTOP__=true;
window.desktopReady=new Promise(resolve=>{
 if(window.pywebview?.api)resolve();else window.addEventListener('pywebviewready',resolve,{once:true});
});
window.desktopReady.then(()=>{
 const footer=document.querySelector('footer');if(!footer)return;
 const button=document.createElement('button');button.className='text-button';button.textContent='打开数据目录';
 button.addEventListener('click',()=>window.pywebview.api.open_data_folder());footer.append(button);
});
window.desktopSaveBlob=async(name,value,type)=>{
 await window.desktopReady;
 const blob=value instanceof Blob?value:new Blob([value],{type:type||'application/octet-stream'});
 const encoded=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=reject;reader.readAsDataURL(blob);});
 return window.pywebview.api.save_file(name,encoded);
};
document.addEventListener('click',async event=>{
 const link=event.target.closest('a');if(!link)return;
 const raw=link.getAttribute('href')||'';
 if(!raw.startsWith('/api/'))return;
 event.preventDefault();event.stopImmediatePropagation();
 try{await window.desktopReady;await window.pywebview.api.save_artifact(raw,link.getAttribute('download')||'研究资料.json');}
 catch(error){if(typeof toast==='function')toast('导出未完成：'+error.message);}
},true);
