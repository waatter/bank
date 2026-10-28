/* Connection recovery never submits, confirms, or retries a financial request. */
(function () {
  'use strict';
  let root, checking=false, uncertain=false;
  function render(state){
    if(!root)return;
    root.hidden=state==='online'&&!uncertain;
    if(root.hidden)return;
    const messages={offline:'网络已断开。以下可能是上次读取的状态；恢复后请先核对原办理记录。',unavailable:'暂时连接不到服务。请先查询原事项的结果，避免重复办理。',recovered:'连接已恢复。此前未确认的办理结果，仍需按原凭证核对。'};
    root.dataset.state=state;
    root.querySelector('[data-connection-message]').textContent=messages[state]||messages.recovered;
    const button=root.querySelector('[data-connection-check]');
    button.disabled=checking;button.textContent=checking?'正在检查…':'检查连接';
  }
  async function check(){
    if(checking)return;
    checking=true;render(typeof navigator!=='undefined'&&navigator.onLine===false?'offline':'unavailable');
    try{
      const response=await fetch('/api/health',{method:'GET',cache:'no-store',signal:AbortSignal.timeout(8000)});
      const health=await response.json();
      if(!response.ok||health.sandbox!==true||!String(health.version||'').startsWith('3.'))throw new Error('health_unavailable');
      checking=false;render('recovered');
    }catch(_){checking=false;render(typeof navigator!=='undefined'&&navigator.onLine===false?'offline':'unavailable');}
  }
  function init(){
    if(root)return;
    root=document.createElement('aside');root.id='connectionNotice';root.className='connection-notice';root.hidden=true;
    root.setAttribute('role','status');root.setAttribute('aria-live','polite');
    root.innerHTML='<p data-connection-message></p><div><button type="button" data-connection-check>检查连接</button><button type="button" data-connection-recover>恢复办理记录</button></div>';
    document.body.prepend(root);
    root.addEventListener('click',event=>{
      if(event.target.closest('[data-connection-check]'))check();
      if(event.target.closest('[data-connection-recover]')){
        const button=document.querySelector('[data-act="recoverChat"]');
        if(button){button.click();}else check();
      }
    });
    window.addEventListener('offline',()=>{uncertain=true;render('offline');});
    window.addEventListener('online',()=>{uncertain=true;check();});
    window.addEventListener('tropos:connection',event=>{
      const state=event.detail?.state;
      if(state==='unavailable'){uncertain=true;render(typeof navigator!=='undefined'&&navigator.onLine===false?'offline':'unavailable');}
      else if(state==='online'&&uncertain)render('recovered');
    });
    if(typeof navigator!=='undefined'&&navigator.onLine===false){uncertain=true;render('offline');}
  }
  window.TroposConnectionUI={init,check};
  init();
})();
