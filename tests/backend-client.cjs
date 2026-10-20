'use strict';
/* Actual HTTP client + workbench adapter, with DOM replaced by inert nodes. No browser automation. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {spawn}=require('node:child_process'),os=require('node:os');
const root=path.resolve(__dirname,'..'),tmp=fs.mkdtempSync(path.join(os.tmpdir(),'tropos-client-'));
const env={...process.env};delete env.DEEPSEEK_API_KEY;delete env.TROPOS_LLM_API_KEY;
const python=fs.existsSync(path.join(root,'.venv/bin/python'))?path.join(root,'.venv/bin/python'):'python3';
const child=spawn(python,['-m','backend.server','--port','0','--db',path.join(tmp,'bank.sqlite')],{cwd:root,env,stdio:['ignore','pipe','pipe']});
let errors='';child.stderr.on('data',data=>errors+=data.toString());
const ready=new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('Server did not start: '+errors)),10000);let out='';child.stdout.on('data',data=>{out+=data;const match=out.match(/http:\/\/127\.0\.0\.1:\d+/);if(match){clearTimeout(timer);resolve(match[0])}});child.on('exit',code=>{clearTimeout(timer);reject(new Error('Server exited '+code+': '+errors))})});
(async()=>{
 const base=await ready,nodes=new Map(),store=new Map();
 function node(key){if(!nodes.has(key))nodes.set(key,{value:'',checked:false,innerHTML:'',textContent:'',style:{},dataset:{},classList:{add(){},remove(){}},querySelector:node,querySelectorAll:()=>[],insertAdjacentHTML(_,html){this.innerHTML+=html},remove(){},scrollTo(){}});return nodes.get(key)}
 const context=vm.createContext({console,crypto:require('node:crypto').webcrypto,AbortSignal,structuredClone,
 TroposProfiles:require('../assets/profile-engine.js'),sessionStorage:{getItem:k=>store.get(k)||null,setItem:(k,v)=>store.set(k,v)},localStorage:{getItem:()=>null,setItem(){}},
 fetch:(p,o={})=>fetch(base+p,{...o,headers:{...o.headers,Origin:base}}),window:{TroposProfileUI:{render(){}},scrollTo(){}},
 document:{querySelector:node,querySelectorAll:()=>[],body:{dataset:{}},addEventListener(){},removeEventListener(){}},setTimeout:fn=>{fn();return 0},clearTimeout(){}});
 vm.runInContext(fs.readFileSync(path.join(root,'assets/backend-client.js'),'utf8'),context);
 const source=fs.readFileSync(path.join(root,'assets/demo.js'),'utf8');
 const definitions=source.slice(0,source.indexOf('/* ------------------------------------------------------------------ 初始化 */'));
 const adapter=source.slice(source.indexOf('function profileUIData()'),source.indexOf('async function startWorkbench()'));
 vm.runInContext(definitions+'\n'+adapter+'\nrenderAll=()=>{};renderConsole=()=>{};renderBills=()=>{};renderCool=()=>{};toast=()=>{};globalThis.api={state,P,S,bankAPI,applyServerSnapshot,profileAdapter,serverTransferCheck,executeServerProposal,replayServerProposal,switchPersona,money,setGuard,serverAgentReply,cancelServerSubscription,ACTIONS};',context);
 const a=context.api;let count=0;const pass=(name,fn)=>{fn();count++;console.log('PASS',name)};
 a.state.serverMode=true;a.applyServerSnapshot(await a.bankAPI.connect('C'));
 pass('HTTP session and persisted account snapshot reach the workbench',()=>{assert.equal(a.P().name,'陈工');assert.equal(a.S().balance,20000);assert.equal(a.S().completed,false)});
 assert.equal((await a.profileAdapter.saveProfile({...a.S().profile,readConsent:true})).ok,true);
 pass('Profile confirmation is stored by the server',()=>assert.equal(a.S().completed,true));
 node('#tfTo').value='目标储蓄';node('#tfAmt').value='100.49';node('#tfWhen').value='now';node('#tfMemo').value='HTTP integration';
 await a.serverTransferCheck();const proposal=a.state.tf.proposal;const balance=a.S().balance;
 pass('Confirmation displays cents exactly before any debit',()=>{assert.ok(node('#tfConfirm').innerHTML.includes('¥100.49'));assert.equal(a.money(-0.01),'−¥0.01')});
 await a.executeServerProposal(proposal.id,null,true);
 pass('Timeout confirmation queries the committed original transaction',()=>{assert.equal(a.S().balance,balance-100.49);assert.equal(a.P().tx.filter(t=>t.proposalId===proposal.id).length,1);assert.ok(node('#tfConfirm').innerHTML.includes('沙箱已记账'));assert.ok(node('#tfConfirm').innerHTML.includes('¥100.49'))});
 await a.replayServerProposal();
 pass('UI replay returns original receipt with no second debit',()=>assert.equal(a.S().balance,balance-100.49));
 node('#tfAmt').value='50';await a.serverTransferCheck();const stale=a.state.tf.proposal;
 assert.equal((await a.profileAdapter.saveProfile({...a.S().profile,reserve:a.S().profile.reserve+100})).ok,true);
 const before=a.S().balance;await a.executeServerProposal(stale.id,null,false);
 pass('Profile edit invalidates an old server proposal even if UI keeps its id',()=>assert.equal(a.S().balance,before));
 const sub=a.S().subs[0];const result=await a.bankAPI.cancelSubscription(sub.id);a.applyServerSnapshot(result.snapshot);
 pass('Merchant receipt and cash prediction update from the same response',()=>{assert.equal(a.S().subs[0].status,'cancelled');assert.equal(typeof a.S().subs[0].receipt,'string');assert.ok(!a.S().serverSnapshot.cash.events.some(e=>e.label===sub.name+'续费'))});
 await a.switchPersona('A',true);
 pass('Account switching changes server session and does not reuse C balance',()=>{assert.equal(a.P().name,'阿林');assert.equal(a.S().balance,20000);assert.equal(a.S().completed,false)});
 await a.switchPersona('C',true);
 pass('Switching back restores C ledger and profile from SQLite',()=>{assert.equal(a.S().balance,balance-100.49);assert.equal(a.S().completed,true)});
 const revision=a.S().revision,updates=JSON.stringify(a.S().profileUpdates);
 a.setGuard(true);a.ACTIONS.shockOff();
 pass('Local shopping preference cannot fabricate a server profile revision',()=>{assert.equal(a.S().revision,revision);assert.equal(JSON.stringify(a.S().profileUpdates),updates)});
 const reply=await a.serverAgentReply('帮我安排这2万元');
 pass('Plan response contains server-calculated steps and the actual personal goal',()=>{assert.ok(reply.html.includes(a.S().profile.goalName));assert.ok(reply.html.includes('METIS deterministic rules'));assert.ok(reply.html.includes('计划不会自动执行'))});
 const originalEvent=a.bankAPI.event.bind(a.bankAPI);let releaseEvent;
 a.bankAPI.event=async type=>{await new Promise(resolve=>releaseEvent=resolve);return originalEvent(type)};
 const pending=a.profileAdapter.sendEvent('income_delayed');await a.switchPersona('A',true);
 pass('An in-flight account event locks account switching',()=>{assert.equal(a.state.operationBusy,true);assert.equal(a.state.persona,'C');assert.equal(a.bankAPI.persona,'C')});
 releaseEvent();assert.equal((await pending).ok,true);a.bankAPI.event=originalEvent;
 pass('Event response keeps matching UI and client account identities',()=>{assert.equal(a.state.operationBusy,false);assert.equal(a.state.persona,a.bankAPI.persona);assert.equal(a.S().shocked,true)});
 let releaseModal;context.modalWait=new Promise(resolve=>releaseModal=resolve);vm.runInContext('modal=()=>globalThis.modalWait;',context);
 const activeSub=a.S().subs.find(s=>s.status==='active');const cancellation=a.cancelServerSubscription(activeSub.id);await a.switchPersona('A',true);
 pass('Merchant confirmation holds the origin account throughout the dialog',()=>{assert.equal(a.state.persona,'C');assert.equal(a.state.operationBusy,true)});
 releaseModal(true);await cancellation;
 pass('Confirmed merchant operation updates only the bound account',()=>{assert.equal(a.S().subs.find(s=>s.id===activeSub.id).status,'cancelled');assert.equal(a.state.operationBusy,false)});
 const report=await a.bankAPI.evaluation();
 pass('Evaluation screen reads saved measured results',()=>assert.equal(report.scenario_count,48));
 console.log(`${count} HTTP/workbench integration checks passed; DOM is stubbed, no browser visual verification.`);
})().catch(error=>{console.error(error);process.exitCode=1}).finally(()=>{child.kill();fs.rmSync(tmp,{recursive:true,force:true})});
