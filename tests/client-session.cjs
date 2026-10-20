'use strict';
/* Client identity regressions with controlled transport failures; no browser required. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {test}=require('node:test');
const source=fs.readFileSync(path.join(__dirname,'../assets/backend-client.js'),'utf8');
const snapshot=persona=>({user:{id:'demo_'+persona,persona},balance:20000});
const response=(body,status=200)=>({ok:status>=200&&status<300,status,json:async()=>body});
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}}
function setup(fetch,stored={}){
  const saved=new Map([['tropos-api-sessions',JSON.stringify(stored)]]);
  const context=vm.createContext({window:{},AbortSignal,fetch,sessionStorage:{getItem:key=>saved.get(key)||null,setItem:(key,value)=>saved.set(key,value)}});
  vm.runInContext(source,context);
  const client=new context.window.TroposSandboxClient();
  client.token='A-live';client.persona='A';client.sessions.A='A-live';
  return {client,saved};
}

test('failed cached-account switch retains active token for later financial requests',async()=>{
  const calls=[];
  const {client}=setup(async(url,opts)=>{
    calls.push({url,token:opts.headers.Authorization});
    if(url==='/api/state')throw new Error('network disconnected');
    return response({ok:true});
  },{B:'B-cached'});
  await assert.rejects(client.select('B'),/服务连接中断/);
  assert.equal(client.persona,'A');assert.equal(client.token,'A-live');
  await client.cancelSubscription('s1');
  assert.equal(calls[0].token,'Bearer B-cached');
  assert.equal(calls[1].token,'Bearer A-live');
});

test('in-flight switch leaves active account unchanged until the validated snapshot arrives',async()=>{
  const pending=deferred(),calls=[];
  const {client}=setup(async(url,opts)=>{calls.push(opts.headers.Authorization);return url==='/api/state'?pending.promise:response({ok:true})},{B:'B-cached'});
  const selection=client.select('B');
  assert.equal(client.persona,'A');assert.equal(client.token,'A-live');
  await client.profile({goalName:'A preference'});
  assert.equal(calls[1],'Bearer A-live');
  pending.resolve(response(snapshot('B')));
  const state=await selection;
  assert.equal(state.user.persona,'B');assert.equal(client.persona,'B');assert.equal(client.token,'B-cached');
});

test('expired cached session renews only the intended account, then atomically installs identity',async()=>{
  const calls=[];
  const {client,saved}=setup(async(url,opts)=>{
    calls.push({url,opts});
    if(url==='/api/state')return response({error:{code:'UNAUTHORIZED',message:'expired'}},401);
    assert.equal(client.persona,'A');assert.equal(client.token,'A-live');
    return response({token:'B-new',snapshot:snapshot('B')});
  },{B:'B-expired'});
  const state=await client.select('B');
  assert.equal(state.user.persona,'B');assert.equal(client.token,'B-new');assert.equal(client.persona,'B');
  assert.equal(JSON.parse(calls[1].opts.body).persona,'B');
  assert.equal(calls[1].opts.headers.Authorization,undefined);
  assert.equal(JSON.parse(saved.get('tropos-api-sessions')).B,'B-new');
});

test('renewal network failure preserves previous identity and stored credentials',async()=>{
  const {client,saved}=setup(async url=>{
    if(url==='/api/state')return response({error:{message:'expired'}},401);
    throw new Error('session endpoint offline');
  },{B:'B-expired'});
  await assert.rejects(client.select('B'),/服务连接中断/);
  assert.equal(client.persona,'A');assert.equal(client.token,'A-live');assert.equal(client.sessions.B,'B-expired');
  assert.equal(JSON.parse(saved.get('tropos-api-sessions')).B,'B-expired');
});

test('server errors do not silently replace a still potentially valid cached session',async()=>{
  let count=0;
  const {client}=setup(async()=>{count++;return response({error:{code:'UNAVAILABLE',message:'暂时不可用'}},503)},{B:'B-cached'});
  await assert.rejects(client.select('B'),/暂时不可用/);
  assert.equal(count,1);assert.equal(client.persona,'A');assert.equal(client.token,'A-live');
});

test('mismatched new-session identity is refused without installing its token',async()=>{
  const {client}=setup(async()=>response({token:'wrong-token',snapshot:snapshot('C')}));
  await assert.rejects(client.select('B'),/身份与所选账户不匹配/);
  assert.equal(client.persona,'A');assert.equal(client.token,'A-live');assert.equal(client.sessions.B,undefined);
});

test('incorrect cached identity is renewed rather than reused for another account',async()=>{
  const {client}=setup(async url=>url==='/api/state'?response(snapshot('C')):response({token:'B-new',snapshot:snapshot('B')}),{B:'C-token'});
  const state=await client.select('B');
  assert.equal(state.user.persona,'B');assert.equal(client.token,'B-new');assert.equal(client.persona,'B');
});

test('an older concurrent account response cannot overwrite the latest chosen identity',async()=>{
  const pending=deferred();
  const {client}=setup(async(_,opts)=>opts.headers.Authorization==='Bearer B-cached'?pending.promise:response(snapshot('C')),{B:'B-cached',C:'C-cached'});
  const old=client.select('B');
  await client.select('C');
  pending.resolve(response(snapshot('B')));
  await assert.rejects(old,error=>error.code==='SESSION_SELECTION_SUPERSEDED');
  assert.equal(client.persona,'C');assert.equal(client.token,'C-cached');
});

test('explicit credential override does not mutate the active credential',async()=>{
  let header;
  const {client}=setup(async(_,opts)=>{header=opts.headers.Authorization;return response(snapshot('B'))});
  await client.request('/api/state',{token:'B-inspect'});
  assert.equal(header,'Bearer B-inspect');assert.equal(client.token,'A-live');assert.equal(client.persona,'A');
});
