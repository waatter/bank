/* Same-origin sandbox client. No provider key ever enters the browser. */
(function () {
  'use strict';
  class SandboxClient {
    constructor(){this.token=null;this.persona=null;this.health=null;this.sessions={};this.selectionSequence=0;this.accessToken=null;
      this.lastPersona=null;try{const last=sessionStorage.getItem('tropos-api-active-persona');if(['A','B','C'].includes(last))this.lastPersona=last;}catch(_){}
      try{
        const stored=sessionStorage.getItem('tropos-lan-access');
        const query=typeof window?.location?.search==='string'?new URLSearchParams(window.location.search).get('access'):null;
        const candidate=query||stored;
        if(typeof candidate==='string'&&/^[A-Za-z0-9_-]{24,128}$/.test(candidate)){this.accessToken=candidate;sessionStorage.setItem('tropos-lan-access',candidate);}
        if(query&&window.history?.replaceState){const clean=new URL(window.location.href);clean.searchParams.delete('access');window.history.replaceState(null,'',clean.pathname+clean.search+clean.hash);}
      }catch(_){}
      try{const stored=JSON.parse(sessionStorage.getItem('tropos-api-sessions')||'{}');if(stored&&typeof stored==='object'&&!Array.isArray(stored))this.sessions=stored;}catch(_){}
      this.conversations={};this.pendingTurns={};this.conversationEpochs={};
      try{const stored=JSON.parse(sessionStorage.getItem('tropos-api-conversations')||'{}');
        for(const key of ['A','B','C']){
          const value=stored?.conversations?.[key];
          if(value&&typeof value.id==='string'&&/^[A-Za-z0-9_-]{1,128}$/.test(value.id)&&Number.isInteger(value.version)&&value.version>=0)this.conversations[key]=value;
          const turn=stored?.pendingTurns?.[key];
          if(turn&&typeof turn.message==='string'&&typeof turn.requestKey==='string'&&/^[A-Za-z0-9_-]{1,128}$/.test(turn.requestKey))this.pendingTurns[key]=turn;
        }
      }catch(_){}
    }
    async request(path,{method='GET',body,authenticated=true,token=this.token}={}){
      const notice=state=>{if(typeof window.dispatchEvent==='function'&&typeof CustomEvent==='function')window.dispatchEvent(new CustomEvent('tropos:connection',{detail:{state}}));};
      if(typeof navigator!=='undefined'&&navigator.onLine===false){notice('unavailable');const error=new Error('当前没有网络连接。本次请求尚未发送；此前结果不明的事项仍需按原凭证查询。');error.code='NETWORK_OFFLINE';error.definitelyUnsent=true;throw error;}
      const headers={'Content-Type':'application/json'};
      if(this.accessToken)headers['X-Tropos-Access']=this.accessToken;
      if(authenticated){if(!token)throw new Error('请先进入模拟账户');headers.Authorization='Bearer '+token;}
      let response;
      try{response=await fetch(path,{method,headers,body:body===undefined?undefined:JSON.stringify(body),cache:'no-store',signal:AbortSignal.timeout(65000)});}
      catch(_){notice('unavailable');throw new Error('服务连接中断。请先查询原交易状态，避免重复提交。');}
      let data;try{data=await response.json()}catch(_){notice('unavailable');throw new Error('服务返回格式异常，未确认任何交易结果。')}
      if(!response.ok){if(response.status>=500)notice('unavailable');const error=new Error(data.error?.message||'操作未完成');error.code=data.error?.code;error.details=data.error?.details;error.status=response.status;throw error;}
      notice('online');
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
      this.lastPersona=persona;try{sessionStorage.setItem('tropos-api-active-persona',persona)}catch(_){}
      try{sessionStorage.setItem('tropos-api-sessions',JSON.stringify(this.sessions))}catch(_){}
      return snapshot;
    }
    profile(profile){return this.request('/api/profile',{method:'PUT',body:profile})}
    proposals(){return this.request('/api/proposals')}
    saveConversations(){try{sessionStorage.setItem('tropos-api-conversations',JSON.stringify({conversations:this.conversations,pendingTurns:this.pendingTurns}))}catch(_){}}
    async agent(message){
      const persona=this.persona,token=this.token,existing=this.pendingTurns[persona];
      const selection=this.selectionSequence,epoch=this.conversationEpochs[persona]||0;
      if(existing&&existing.message!==message){const error=new Error('上一条消息结果尚未核对，请先重试原消息或恢复办理记录。');error.code='TURN_UNRESOLVED';throw error;}
      const current=this.conversations[persona];
      const body=existing||{message,requestKey:crypto.randomUUID(),...(current?{conversationId:current.id,conversationVersion:current.version}:{})};
      this.pendingTurns[persona]=body;this.saveConversations();
      let result;
      try{result=await this.request('/api/agent',{method:'POST',body,token});}
      catch(error){if(error.status&&error.status<500&&this.pendingTurns[persona]?.requestKey===body.requestKey&&(this.conversationEpochs[persona]||0)===epoch){delete this.pendingTurns[persona];this.saveConversations();}throw error;}
      if(this.persona!==persona||this.token!==token||this.selectionSequence!==selection||(this.conversationEpochs[persona]||0)!==epoch||result.snapshot?.user?.persona!==persona)throw new Error('对话响应不属于当前账户或已更新的办理记录，请恢复原记录。');
      if(!result.conversation||!Number.isInteger(result.conversation.version)||typeof result.conversation.id!=='string')throw new Error('办理记录格式异常，请按原消息恢复，勿新建重复事项。');
      const latest=this.conversations[persona],pending=this.pendingTurns[persona];
      if((pending&&pending.requestKey!==body.requestKey)||(latest&&(latest.id!==result.conversation.id||latest.version>result.conversation.version)))throw new Error('已有更新的办理步骤，已忽略迟到的旧消息结果。');
      this.conversations[persona]=result.conversation;
      if(this.pendingTurns[persona]?.requestKey===body.requestKey)delete this.pendingTurns[persona];this.saveConversations();
      return result;
    }
    async recoverConversation(){
      const persona=this.persona,token=this.token,current=this.conversations[persona];
      const selection=this.selectionSequence,epoch=this.conversationEpochs[persona]||0;
      if(!current)return {conversation:null,messages:[],currentResult:null};
      const result=await this.request('/api/conversations/'+encodeURIComponent(current.id),{token});
      if(persona!==this.persona||token!==this.token||selection!==this.selectionSequence||epoch!==(this.conversationEpochs[persona]||0)||result.currentResult?.snapshot?.user?.persona!==persona)throw new Error('账户已变化或办理记录已更新，未恢复旧的对话。');
      const latest=this.conversations[persona];
      if(!latest||latest.id!==result.conversation.id||latest.version>result.conversation.version)throw new Error('已有更新的办理步骤，已忽略迟到的恢复结果。');
      this.conversations[persona]=result.conversation;
      // A successful history read does not prove that an unresolved POST failed.
      // Keep its original request key until an explicit retry resolves it.
      this.saveConversations();return result;
    }
    forgetConversation(persona=this.persona){this.conversationEpochs[persona]=(this.conversationEpochs[persona]||0)+1;delete this.conversations[persona];delete this.pendingTurns[persona];this.saveConversations();}
    subscriptionProposal(id){return this.request('/api/subscription-proposals/'+encodeURIComponent(id))}
    confirmSubscription(proposal,simulateTimeout=false){return this.request('/api/subscription-proposals/'+encodeURIComponent(proposal.id)+'/confirm',{method:'POST',body:{confirmation:true,confirmationToken:proposal.confirmationToken,simulate_timeout:simulateTimeout}})}
    revokeSubscription(id){return this.request('/api/subscription-proposals/'+encodeURIComponent(id)+'/revoke',{method:'POST',body:{confirmation:true}})}
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
