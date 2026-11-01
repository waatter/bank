/* Same-origin sandbox client. No provider key ever enters the browser. */
(function () {
  'use strict';
  class SandboxClient {
    constructor(){this.token=null;this.persona=null;this.userId=null;this.accountType=null;this.username=null;this.email=null;this.emailVerified=false;this.mfaEnabled=false;this.health=null;this.sessions={};this.selectionSequence=0;this.accessToken=null;this.memberSession=null;this.lastMailDelivery=null;
      this.lastPersona=null;try{const last=sessionStorage.getItem('tropos-api-active-persona');if(['A','B','C'].includes(last))this.lastPersona=last;}catch(_){}
      try{
        const stored=sessionStorage.getItem('tropos-lan-access');
        const query=typeof window?.location?.search==='string'?new URLSearchParams(window.location.search).get('access'):null;
        const candidate=query||stored;
        if(typeof candidate==='string'&&/^[A-Za-z0-9_-]{24,128}$/.test(candidate)){this.accessToken=candidate;sessionStorage.setItem('tropos-lan-access',candidate);}
        if(query&&window.history?.replaceState){const clean=new URL(window.location.href);clean.searchParams.delete('access');window.history.replaceState(null,'',clean.pathname+clean.search+clean.hash);}
      }catch(_){}
      try{const stored=JSON.parse(sessionStorage.getItem('tropos-api-sessions')||'{}');if(stored&&typeof stored==='object'&&!Array.isArray(stored))this.sessions=stored;}catch(_){}
      try{const stored=JSON.parse(sessionStorage.getItem('tropos-member-session')||'null');if(stored&&typeof stored.token==='string'&&stored.token.length>=32)this.memberSession=stored;}catch(_){}
      this.conversations={};this.pendingTurns={};this.conversationEpochs={};
      try{const stored=JSON.parse(sessionStorage.getItem('tropos-api-conversations')||'{}');
        for(const key of Object.keys(stored?.conversations||{}).concat(Object.keys(stored?.pendingTurns||{})).filter((v,i,a)=>a.indexOf(v)===i)){
          if(!/^[A-Za-z0-9_-]{1,128}$/.test(key))continue;
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
      catch(cause){
        notice('unavailable');
        const readOnly=method==='GET'||path==='/api/agent'||/^\/api\/model-center\/(?:personal\/)?providers\/[^/]+\/test$/.test(path);
        const message=readOnly
          ?'只读服务连接中断或响应超时。本次请求不会执行支付、转账、下单或账户修改，请稍后重试。'
          :'服务连接中断。请先查询原交易状态，避免重复提交。';
        const error=new Error(message);error.code=cause?.name==='TimeoutError'?'REQUEST_TIMEOUT':'NETWORK_ERROR';throw error;
      }
      let data;try{data=await response.json()}catch(_){notice('unavailable');throw new Error('服务返回格式异常，未确认任何交易结果。')}
      if(!response.ok){if(response.status>=500)notice('unavailable');const error=new Error(data.error?.message||'操作未完成');error.code=data.error?.code;error.details=data.error?.details;error.status=response.status;throw error;}
      notice('online');
      return data;
    }
    async connect(persona){
      this.health=await this.request('/api/health',{authenticated:false});
      if(this.memberSession){
        try{
          const snapshot=await this.request('/api/state',{token:this.memberSession.token});
          if(snapshot?.user?.accountType==='member'){this.installIdentity(this.memberSession.token,snapshot);return snapshot;}
        }catch(error){if(error.status!==401)throw error;}
        this.memberSession=null;try{sessionStorage.removeItem('tropos-member-session')}catch(_){}
      }
      return this.select(persona);
    }
    installIdentity(token,snapshot){
      if(typeof token!=='string'||!snapshot?.user?.id||!['A','B','C'].includes(snapshot.user.persona))throw new Error('服务端返回的账户身份无效。');
      this.token=token;this.persona=snapshot.user.persona;this.userId=snapshot.user.id;
      this.accountType=snapshot.user.accountType||'demo';this.username=snapshot.user.username||null;
      this.email=snapshot.user.email||null;this.emailVerified=snapshot.user.emailVerified===true;this.mfaEnabled=snapshot.user.mfaEnabled===true;
      this.lastPersona=this.persona;try{sessionStorage.setItem('tropos-api-active-persona',this.persona)}catch(_){}
      return snapshot;
    }
    async register(details){
      if(!this.health)this.health=await this.request('/api/health',{authenticated:false});
      const result=await this.request('/api/auth/register',{method:'POST',body:details,authenticated:false});
      this.lastMailDelivery=result.mailDelivery||null;
      this.memberSession={token:result.token};try{sessionStorage.setItem('tropos-member-session',JSON.stringify(this.memberSession))}catch(_){}
      this.selectionSequence++;
      return this.installIdentity(result.token,result.snapshot);
    }
    async login(username,password){
      if(!this.health)this.health=await this.request('/api/health',{authenticated:false});
      const result=await this.request('/api/auth/login',{method:'POST',body:{username,password},authenticated:false});
      if(result.mfaRequired)return result;
      this.memberSession={token:result.token};try{sessionStorage.setItem('tropos-member-session',JSON.stringify(this.memberSession))}catch(_){}
      this.selectionSequence++;
      return this.installIdentity(result.token,result.snapshot);
    }
    async completeMfa(challengeToken,code){
      const result=await this.request('/api/auth/mfa/complete',{method:'POST',body:{challengeToken,code},authenticated:false});
      this.memberSession={token:result.token};try{sessionStorage.setItem('tropos-member-session',JSON.stringify(this.memberSession))}catch(_){}
      this.selectionSequence++;return this.installIdentity(result.token,result.snapshot);
    }
    verifyEmail(token){return this.request('/api/auth/email/verify',{method:'POST',body:{token},authenticated:false})}
    resendEmail(){return this.request('/api/auth/email/resend',{method:'POST',body:{}})}
    bindEmail(email,password,code=''){return this.request('/api/auth/email/bind',{method:'POST',body:{email,password,code,confirmation:true}})}
    forgotPassword(identifier){return this.request('/api/auth/password/forgot',{method:'POST',body:{identifier},authenticated:false})}
    resetPassword(token,password){return this.request('/api/auth/password/reset',{method:'POST',body:{token,password},authenticated:false})}
    setupMfa(){return this.request('/api/auth/mfa/setup',{method:'POST',body:{}})}
    enableMfa(code){return this.request('/api/auth/mfa/enable',{method:'POST',body:{code}})}
    disableMfa(password,code){return this.request('/api/auth/mfa/disable',{method:'POST',body:{password,code,confirmation:true}})}
    async logout(){
      const token=this.token;
      if(token&&this.accountType==='member'){try{await this.request('/api/auth/logout',{method:'POST',body:{},token})}catch(_){}finally{this.memberSession=null;try{sessionStorage.removeItem('tropos-member-session')}catch(_){}}}
      this.token=null;this.userId=null;this.accountType=null;this.username=null;this.email=null;this.emailVerified=false;this.mfaEnabled=false;this.persona=null;this.selectionSequence++;
    }
    scopeKey(){return this.accountType==='member'&&this.userId?this.userId:this.persona;}
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
      this.installIdentity(candidateToken,snapshot);this.sessions[persona]=candidateToken;
      this.lastPersona=persona;try{sessionStorage.setItem('tropos-api-active-persona',persona)}catch(_){}
      try{sessionStorage.setItem('tropos-api-sessions',JSON.stringify(this.sessions))}catch(_){}
      return snapshot;
    }
    profile(profile){return this.request('/api/profile',{method:'PUT',body:profile})}
    proposals(){return this.request('/api/proposals')}
    saveConversations(){try{sessionStorage.setItem('tropos-api-conversations',JSON.stringify({conversations:this.conversations,pendingTurns:this.pendingTurns}))}catch(_){}}
    async agent(message){
      const persona=this.persona,userId=this.userId,key=this.scopeKey(),token=this.token,existing=this.pendingTurns[key];
      const selection=this.selectionSequence,epoch=this.conversationEpochs[key]||0;
      if(existing&&existing.message!==message){const error=new Error('上一条消息结果尚未核对，请先重试原消息或恢复办理记录。');error.code='TURN_UNRESOLVED';throw error;}
      const current=this.conversations[key];
      const body=existing||{message,requestKey:crypto.randomUUID(),...(current?{conversationId:current.id,conversationVersion:current.version}:{})};
      this.pendingTurns[key]=body;this.saveConversations();
      let result;
      try{result=await this.request('/api/agent',{method:'POST',body,token});}
      catch(error){if(error.status&&error.status<500&&this.pendingTurns[key]?.requestKey===body.requestKey&&(this.conversationEpochs[key]||0)===epoch){delete this.pendingTurns[key];this.saveConversations();}throw error;}
      if(this.persona!==persona||this.userId!==userId||this.token!==token||this.selectionSequence!==selection||(this.conversationEpochs[key]||0)!==epoch||result.snapshot?.user?.id!==userId)throw new Error('对话响应不属于当前账户或已更新的办理记录，请恢复原记录。');
      if(!result.conversation||!Number.isInteger(result.conversation.version)||typeof result.conversation.id!=='string')throw new Error('办理记录格式异常，请按原消息恢复，勿新建重复事项。');
      const latest=this.conversations[key],pending=this.pendingTurns[key];
      if((pending&&pending.requestKey!==body.requestKey)||(latest&&(latest.id!==result.conversation.id||latest.version>result.conversation.version)))throw new Error('已有更新的办理步骤，已忽略迟到的旧消息结果。');
      this.conversations[key]=result.conversation;
      if(this.pendingTurns[key]?.requestKey===body.requestKey)delete this.pendingTurns[key];this.saveConversations();
      return result;
    }
    async recoverConversation(){
      const persona=this.persona,userId=this.userId,key=this.scopeKey(),token=this.token,current=this.conversations[key];
      const selection=this.selectionSequence,epoch=this.conversationEpochs[key]||0;
      if(!current)return {conversation:null,messages:[],currentResult:null};
      const result=await this.request('/api/conversations/'+encodeURIComponent(current.id),{token});
      if(persona!==this.persona||userId!==this.userId||token!==this.token||selection!==this.selectionSequence||epoch!==(this.conversationEpochs[key]||0)||result.currentResult?.snapshot?.user?.id!==userId)throw new Error('账户已变化或办理记录已更新，未恢复旧的对话。');
      const latest=this.conversations[key];
      if(!latest||latest.id!==result.conversation.id||latest.version>result.conversation.version)throw new Error('已有更新的办理步骤，已忽略迟到的恢复结果。');
      this.conversations[key]=result.conversation;
      // A successful history read does not prove that an unresolved POST failed.
      // Keep its original request key until an explicit retry resolves it.
      this.saveConversations();return result;
    }
    forgetConversation(key=this.scopeKey()){this.conversationEpochs[key]=(this.conversationEpochs[key]||0)+1;delete this.conversations[key];delete this.pendingTurns[key];this.saveConversations();}
    subscriptionProposal(id){return this.request('/api/subscription-proposals/'+encodeURIComponent(id))}
    confirmSubscription(proposal,simulateTimeout=false){return this.request('/api/subscription-proposals/'+encodeURIComponent(proposal.id)+'/confirm',{method:'POST',body:{confirmation:true,confirmationToken:proposal.confirmationToken,simulate_timeout:simulateTimeout}})}
    revokeSubscription(id){return this.request('/api/subscription-proposals/'+encodeURIComponent(id)+'/revoke',{method:'POST',body:{confirmation:true}})}
    mission(message){return this.request('/api/missions',{method:'POST',body:{message}})}
    confirmMission(mission,approve,phrases){return this.request('/api/missions/'+encodeURIComponent(mission.id)+'/confirm',{method:'POST',body:{confirmation:true,token:mission.token,approve,phrases}})}
    portfolio(){return this.request('/api/portfolio')}
    household(){return this.request('/api/household')}
    familySimulate(id,decision){return this.request('/api/family/asks/'+encodeURIComponent(id)+'/simulate',{method:'POST',body:{decision}})}
    redteam(){return this.request('/api/evaluation/redteam')}
    redteamLive(){return this.request('/api/evaluation/redteam-live')}
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
    glossary(){return this.request('/api/financial-glossary')}
    modelCenter(){return this.request('/api/model-center')}
    selectModel(provider){return this.request('/api/model-center/selection',{method:'PUT',body:{provider}})}
    configureModel(provider,config){return this.request('/api/model-center/providers/'+encodeURIComponent(provider),{method:'PUT',body:{...config,confirmation:true}})}
    testModel(provider){return this.request('/api/model-center/providers/'+encodeURIComponent(provider)+'/test',{method:'POST',body:{confirmation:true}})}
    configurePersonalModel(provider,config){return this.request('/api/model-center/personal/providers/'+encodeURIComponent(provider),{method:'PUT',body:{...config,confirmation:true}})}
    testPersonalModel(provider){return this.request('/api/model-center/personal/providers/'+encodeURIComponent(provider)+'/test',{method:'POST',body:{confirmation:true}})}
    removePersonalModel(provider){return this.request('/api/model-center/personal/providers/'+encodeURIComponent(provider),{method:'DELETE',body:{confirmation:true}})}
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
    paotuiConfirm(preview,requestKey,highAmountConfirmation=false){return this.request('/api/shopping/paotui/previews/'+encodeURIComponent(preview.id)+'/confirm',{method:'POST',body:{confirmation:true,confirmationToken:preview.confirmationToken,requestKey,highAmountConfirmation,...(preview.manualAddressToken?{manualAddressToken:preview.manualAddressToken}:{})}})}
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
