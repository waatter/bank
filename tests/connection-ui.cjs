'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../assets/connection-ui.js'),'utf8');
function setup(online=true){
  const listeners=new Map(),calls=[],nodes=new Map(),queue=[];let root,recoveries=0,mounts=0;
  const node=key=>{if(!nodes.has(key))nodes.set(key,{textContent:'',disabled:false});return nodes.get(key)};
  const context={navigator:{onLine:online},AbortSignal:{timeout:()=>({})},
    fetch:async(url,options)=>{calls.push({url,options});const next=queue.shift();if(next instanceof Error)throw next;return next||{ok:true,json:async()=>({sandbox:true,version:'3.0.0'})}},
    window:{addEventListener:(name,fn)=>listeners.set(name,fn)},
    document:{body:{prepend:value=>{root=value;mounts++;}},createElement:()=>({hidden:false,dataset:{},setAttribute(){},querySelector:node,addEventListener:(name,fn)=>listeners.set('root:'+name,fn)}),querySelector:()=>({click:()=>recoveries++})}};
  vm.runInNewContext(source,context);
  return {context,listeners,calls,queue,node,get root(){return root},get mounts(){return mounts},get recoveries(){return recoveries},ui:context.window.TroposConnectionUI};
}
test('healthy startup adds one hidden notice without a network request',()=>{
  const h=setup();h.ui.init();assert.equal(h.mounts,1);assert.equal(h.root.hidden,true);assert.equal(h.calls.length,0);
});
test('offline state shows stale-state wording and sends no financial or health request',()=>{
  const h=setup(false);assert.equal(h.root.hidden,false);assert.equal(h.root.dataset.state,'offline');assert.match(h.node('[data-connection-message]').textContent,/上次读取/);assert.equal(h.calls.length,0);
});
test('recovery probe uses only uncached GET health and does not retry a POST',async()=>{
  const h=setup(false);h.context.navigator.onLine=true;await h.ui.check();assert.equal(h.calls.length,1);assert.equal(h.calls[0].url,'/api/health');assert.equal(h.calls[0].options.method,'GET');assert.equal(h.calls[0].options.cache,'no-store');assert.equal(h.root.dataset.state,'recovered');assert.match(h.node('[data-connection-message]').textContent,/仍需/);
});
test('unavailable or wrong-version health never claims recovery',async()=>{
  const h=setup();h.queue.push(new Error('offline'));await h.ui.check();assert.equal(h.root.dataset.state,'unavailable');
  h.queue.push({ok:true,json:async()=>({sandbox:true,version:'2.0.0'})});await h.ui.check();assert.equal(h.root.dataset.state,'unavailable');assert.equal(h.calls.length,2);
});
test('repeated probes are coalesced and the recovery button delegates to the read-only history control',async()=>{
  const h=setup();let release;h.queue.push({ok:true,json:()=>new Promise(resolve=>release=resolve)});const first=h.ui.check();await Promise.resolve();await h.ui.check();assert.equal(h.calls.length,1);release({sandbox:true,version:'3.0.0'});await first;
  h.listeners.get('root:click')({target:{closest:selector=>selector==='[data-connection-recover]'?{}:null}});assert.equal(h.recoveries,1);assert.equal(h.calls.length,1);
});
test('a successful request after uncertainty still tells the user to check the original result',()=>{
  const h=setup();h.listeners.get('tropos:connection')({detail:{state:'unavailable'}});h.listeners.get('tropos:connection')({detail:{state:'online'}});assert.equal(h.root.hidden,false);assert.equal(h.root.dataset.state,'recovered');
});
test('the client refuses an offline confirmation before sending and preserves unresolved message identity',async()=>{
  const sourceClient=fs.readFileSync(path.join(__dirname,'../assets/backend-client.js'),'utf8');const calls=[],store=new Map();
  const context={window:{},navigator:{onLine:false},crypto:{randomUUID:()=> 'original-turn'},sessionStorage:{getItem:k=>store.get(k)||null,setItem:(k,v)=>store.set(k,v)},fetch:async(...args)=>calls.push(args),AbortSignal:{timeout:()=>({})}};
  vm.runInNewContext(sourceClient,context);const client=new context.window.TroposSandboxClient();client.persona='A';client.token='synthetic-token';
  await assert.rejects(client.confirmSubscription({id:'proposal',confirmationToken:'scoped-token'}),error=>error.code==='NETWORK_OFFLINE'&&error.definitelyUnsent===true);
  client.pendingTurns.A={message:'查原方案',requestKey:'unresolved-original'};
  await assert.rejects(client.agent('查原方案'),/没有网络连接/);assert.equal(client.pendingTurns.A.requestKey,'unresolved-original');assert.equal(calls.length,0);
});
