/* Same-origin sandbox client. No provider key ever enters the browser. */
(function () {
  'use strict';
  class SandboxClient {
    constructor(){this.token=null;this.persona=null;this.health=null;this.sessions={};this.selectionSequence=0;this.accessToken=null;
      try{
        const stored=sessionStorage.getItem('tropos-lan-access');
        const query=typeof window?.location?.search==='string'?new URLSearchParams(window.location.search).get('access'):null;
        const candidate=query||stored;
        if(typeof candidate==='string'&&/^[A-Za-z0-9_-]{24,128}$/.test(candidate)){this.accessToken=candidate;sessionStorage.setItem('tropos-lan-access',candidate);}
        if(query&&window.history?.replaceState){const clean=new URL(window.location.href);clean.searchParams.delete('access');window.history.replaceState(null,'',clean.pathname+clean.search+clean.hash);}
      }catch(_){}
      try{const stored=JSON.parse(sessionStorage.getItem('tropos-api-sessions')||'{}');if(stored&&typeof stored==='object'&&!Array.isArray(stored))this.sessions=stored;}catch(_){}
    }
    async request(path,{method='GET',body,authenticated=true,token=this.token}={}){
      const headers={'Content-Type':'application/json'};
      if(this.accessToken)headers['X-Tropos-Access']=this.accessToken;
      if(authenticated){if(!token)throw new Error('请先进入模拟账户');headers.Authorization='Bearer '+token;}
      let response;
      try{response=await fetch(path,{method,headers,body:body===undefined?undefined:JSON.stringify(body),cache:'no-store',signal:AbortSignal.timeout(65000)});}
      catch(_){throw new Error('服务连接中断。请先查询原交易状态，避免重复提交。');}
      let data;try{data=await response.json()}catch(_){throw new Error('服务返回格式异常，未确认任何交易结果。')}
      if(!response.ok){const error=new Error(data.error?.message||'操作未完成');error.code=data.error?.code;error.details=data.error?.details;error.status=response.status;throw error;}
      return data;
    }
    async connect(persona){
      this.health=await this.request('/api/health',{authenticated:false});
      return this.select(persona);
    }
    async select(persona){
      // Candidate credentials never become the active identity until validation succeeds.
      // A failed switch therefore leaves every subsequent operation on the old account.
      const sequence=++this.selectionSequence;
      let candidateToken=this.sessions[persona]||null,snapshot;
      if(candidateToken){
        try{
          snapshot=await this.request('/api/state',{token:candidateToken});
          if(snapshot?.user?.persona!==persona){candidateToken=null;snapshot=null;}
        }catch(error){if(error.status!==401)throw error;candidateToken=null;}
      }
      if(!snapshot){
        const result=await this.request('/api/sessions',{method:'POST',body:{persona},authenticated:false});
        candidateToken=result.token;snapshot=result.snapshot;
      }
      if(typeof candidateToken!=='string'||!candidateToken||snapshot?.user?.persona!==persona){
        throw new Error('服务端账户身份与所选账户不匹配，当前账户未切换。');
      }
      if(sequence!==this.selectionSequence){
        const error=new Error('账户选择已更新，已忽略先前的连接结果。');
        error.code='SESSION_SELECTION_SUPERSEDED';throw error;
      }
      this.token=candidateToken;this.persona=persona;this.sessions[persona]=candidateToken;
      try{sessionStorage.setItem('tropos-api-sessions',JSON.stringify(this.sessions))}catch(_){}
      return snapshot;
    }
    profile(profile){return this.request('/api/profile',{method:'PUT',body:profile})}
    proposals(){return this.request('/api/proposals')}
    agent(message){return this.request('/api/agent',{method:'POST',body:{message}})}
    mission(message){return this.request('/api/missions',{method:'POST',body:{message}})}
    confirmMission(mission,approve,phrases){return this.request('/api/missions/'+encodeURIComponent(mission.id)+'/confirm',{method:'POST',body:{confirmation:true,token:mission.token,approve,phrases}})}
    portfolio(){return this.request('/api/portfolio')}
    redteam(){return this.request('/api/evaluation/redteam')}
    propose(body){return this.request('/api/transfers/propose',{method:'POST',body})}
    confirm(proposal,simulateTimeout=false){return this.request('/api/proposals/'+encodeURIComponent(proposal.id)+'/confirm',{method:'POST',body:{confirmation:true,confirmationToken:proposal.confirmationToken,simulate_timeout:simulateTimeout}})}
    revoke(id){return this.request('/api/proposals/'+encodeURIComponent(id)+'/revoke',{method:'POST',body:{confirmation:true}})}
    transaction(key){return this.request('/api/transactions/by-key/'+encodeURIComponent(key))}
    cancelSubscription(id){return this.request('/api/subscriptions/'+encodeURIComponent(id)+'/cancel',{method:'POST',body:{confirmation:true}})}
    event(type){return this.request('/api/events',{method:'POST',body:{type,event_key:crypto.randomUUID()}})}
    reset(){return this.request('/api/reset',{method:'POST',body:{confirmation:true}})}
    evaluation(){return this.request('/api/evaluation/latest')}
    intentEvaluation(){return this.request('/api/evaluation/intents')}
    life(){return this.request('/api/life')}
    insights(){return this.request('/api/insights')}
    shoppingProviders(){return this.request('/api/shopping/providers')}
    shoppingSearch(body){return this.request('/api/shopping/search',{method:'POST',body})}
    shoppingCheckout(body){return this.request('/api/shopping/checkout',{method:'POST',body})}
    shoppingConfirm(checkout,requestKey){return this.request('/api/shopping/checkouts/'+encodeURIComponent(checkout.id)+'/confirm',{method:'POST',body:{confirmation:true,confirmationToken:checkout.confirmationToken,requestKey}})}
    shoppingOrders(){return this.request('/api/shopping/orders')}
    shoppingOrder(id){return this.request('/api/shopping/orders/'+encodeURIComponent(id))}
    shoppingAdvance(id){return this.request('/api/shopping/orders/'+encodeURIComponent(id)+'/advance',{method:'POST',body:{confirmation:true}})}
    paotuiStatus(){return this.request('/api/shopping/paotui')}
    paotuiAuthStart(){return this.request('/api/shopping/paotui/auth/start',{method:'POST',body:{}})}
    paotuiAuthComplete(){return this.request('/api/shopping/paotui/auth/complete',{method:'POST',body:{}})}
    paotuiLogout(){return this.request('/api/shopping/paotui/logout',{method:'POST',body:{confirmation:true}})}
    paotuiAddresses(){return this.request('/api/shopping/paotui/addresses')}
    paotuiPreview(body){return this.request('/api/shopping/paotui/preview',{method:'POST',body})}
    paotuiConfirm(preview,requestKey,highAmountConfirmation=false){return this.request('/api/shopping/paotui/previews/'+encodeURIComponent(preview.id)+'/confirm',{method:'POST',body:{confirmation:true,confirmationToken:preview.confirmationToken,requestKey,highAmountConfirmation}})}
    paotuiOrders(){return this.request('/api/shopping/paotui/orders')}
    paotuiPayment(id){return this.request('/api/shopping/paotui/orders/'+encodeURIComponent(id)+'/payment',{method:'POST',body:{}})}
    paotuiOrderStatus(id){return this.request('/api/shopping/paotui/orders/'+encodeURIComponent(id)+'/status',{method:'POST',body:{}})}
    lifeSettings(body){return this.request('/api/life/settings',{method:'POST',body})}
    lifeScene(body){return this.request('/api/life/scenes',{method:'POST',body})}
    lifeCancel(id,body){return this.request('/api/life/scenes/'+encodeURIComponent(id)+'/cancel',{method:'POST',body})}
    lifeCommit(id,body){return this.request('/api/life/scenes/'+encodeURIComponent(id)+'/commit',{method:'POST',body})}
    lifeAnalyze(){return this.request('/api/life/analyze',{method:'POST',body:{}})}
    lifeAdopt(body){return this.request('/api/life/commit',{method:'POST',body})}
    lifeGoal(body){return this.request('/api/life/goal',{method:'POST',body})}
  }
  window.TroposSandboxClient=SandboxClient;
})();
