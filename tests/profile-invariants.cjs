'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const engine = require('../assets/profile-engine.js');
const source = fs.readFileSync(path.join(__dirname,'../assets/demo.js'),'utf8');
const definitions=source.slice(0,source.indexOf('/* ------------------------------------------------------------------ 初始化 */'));
const adapter=source.slice(source.indexOf('function profileUIData()'),source.indexOf('for(const k of Object.keys(PERSONAS))if'));
function sandbox(storage=new Map()) {
  const nodes=new Map();
  function node(key){if(!nodes.has(key))nodes.set(key,{value:'',checked:false,innerHTML:'',textContent:'',style:{},dataset:{},classList:{add(){},remove(){}},insertAdjacentHTML(_,html){this.innerHTML+=html},remove(){},scrollTo(){},querySelector:node,querySelectorAll:()=>[]});return nodes.get(key)}
  const context=vm.createContext({console,crypto:require('node:crypto').webcrypto,TroposProfiles:engine,
    localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v)},
    window:{TroposProfileUI:{render(){}},scrollTo(){}},
    document:{querySelector:node,querySelectorAll:()=>[],body:{dataset:{}},addEventListener(){},removeEventListener(){}},
    setTimeout:fn=>{fn();return 0},clearTimeout(){}});
  vm.runInContext(definitions+'\n'+adapter+'\nrenderAll=()=>{};renderConsole=()=>{};renderBills=()=>{};toast=()=>{}; globalThis.api={state,P,S,cashPath,profileSnapshot,profileModes,recordProfileUpdate,runChecks,confirmTransfer,manageReply,profileAdapter,restoreWorkspace,persistWorkspace,ACTIONS};',context);
  return {api:context.api,nodes,node,storage};
}
let count=0;
function check(name,fn){fn();count++;console.log('PASS',name)}
(async()=>{
  const h=sandbox(),a=h.api,p=a.P();
  check('画像读取同意不能默认授予',()=>assert.equal(engine.validate({...a.S().profile,readConsent:false}).ok,false));
  check('负现金预留被拒绝',()=>assert.equal(engine.validate({...a.S().profile,readConsent:true,reserve:-1}).ok,false));
  check('同一画像的策略可随现金缺口变化，目标仍保留',()=>{const profile={...a.S().profile,goalType:'grow',incomeStability:'stable'};const before=engine.policy({profile,free:20000,income:10000,debt:0,shocked:false});const after=engine.policy({profile,free:-1000,income:10000,debt:0,shocked:true});assert.notEqual(before[0].key,after[0].key);assert.equal(after[0].key,'protect');assert.equal(profile.goalType,'grow');assert.equal(after.reduce((n,r)=>n+r.value,0),100)});
  check('续费进入未来现金路径，取消后移除',()=>{let before=a.cashPath(p,a.S());const sub=a.S().subs[0];assert.ok(before.events.some(f=>f.type==='subscription'&&f.label.includes(sub.name)));sub.status='cancelled';const after=a.cashPath(p,a.S());assert.ok(after.min>=before.min);assert.ok(!after.events.some(f=>f.type==='subscription'&&f.label.includes(sub.name)))});
  check('用户采纳建议不能单方更改账单日期',()=>{const st=a.S();st.shocked=true;st.replan=true;const item=p.replan.moves[0];assert.equal(a.cashPath(p,st).events.find(f=>f.label===p.bills.find(b=>b.id===item.id).label).day,p.bills.find(b=>b.id===item.id).day);st.confirmedDeferrals=[item.id];assert.equal(a.cashPath(p,st).events.find(f=>f.label===p.bills.find(b=>b.id===item.id).label).day,item.to);st.shocked=false;st.replan=false});
  a.S().completed=true;a.S().profile.readConsent=true;
  h.node('#tfTo').value='目标储蓄';h.node('#tfAmt').value='50000';h.node('#tfWhen').value='now';h.node('#tfMemo').value='目标储蓄';
  check('本人目标账户也不能透支或绕开现金底线',()=>{const result=a.runChecks(false);assert.equal(result.find(x=>x.key==='reserve').status,'fail');assert.equal(a.S().balance,20000)});
  a.state.persona='C';a.S().completed=true;a.S().profile.readConsent=true;
  h.node('#tfTo').value='妈妈';h.node('#tfAmt').value='100';h.node('#tfWhen').value='now';h.node('#tfMemo').value='测试正常转账';
  check('合法小额转账可以进入确认',()=>{a.runChecks(false);assert.equal(a.state.tf.allOk,true)});
  const original=a.S().balance;h.node('#tfAmt').value='50000';
  await a.confirmTransfer({disabled:false,remove(){}});
  check('确认后改金额使旧批准失效',()=>{assert.equal(a.S().balance,original);assert.equal(a.state.tf,null)});
  h.node('#tfAmt').value='100';a.runChecks(false);await a.confirmTransfer({disabled:false,remove(){}});
  check('确认转账只记一笔，余额与流水同时更新',()=>{assert.equal(a.S().balance,original-100);assert.equal(a.S().transferTx.length,1);assert.equal(a.S().transferTx[0].amount,-100);assert.ok(a.S().profileUpdates.length>0)});
  await a.confirmTransfer({disabled:false,remove(){}});
  check('同一凭证重放不重复扣款',()=>{assert.equal(a.S().balance,original-100);assert.equal(a.S().transferTx.length,1)});
  const st=a.S(),goal=st.profile.goalName,reserve=st.profile.reserve,guard=st.guard;
  check('新增账单更新现金状态，不改目标/底线/授权',()=>{const free=a.cashPath(a.P(),st).free;assert.equal(a.profileAdapter.sendEvent('bill_added').ok,true);assert.ok(a.cashPath(a.P(),st).free<free);assert.equal(st.profile.goalName,goal);assert.equal(st.profile.reserve,reserve);assert.equal(st.guard,guard);assert.equal(a.profileAdapter.sendEvent('bill_added').ok,false)});
  const initial=st.balance, income=a.P().income;
  check('工资到账只加一次，并移除同笔预期收入',()=>{assert.equal(a.profileAdapter.sendEvent('salary_received').ok,true);assert.equal(st.balance,initial+income);assert.equal(a.cashPath(a.P(),st).events.filter(f=>f.type==='salary').length,0);assert.equal(a.profileAdapter.sendEvent('salary_received').ok,false);assert.equal(st.balance,initial+income)});
  check('画像、账本、幂等凭证可恢复',()=>{a.persistWorkspace();const restored=sandbox(h.storage).api;assert.equal(restored.state.persona,'C');assert.equal(restored.S().balance,st.balance);assert.equal(restored.S().salaryReceived,true);assert.equal(restored.S().usedKeys.size,st.usedKeys.size);assert.equal(restored.S().profile.goalName,goal)});

  check('目标改名保留原有已存资金',()=>{const funded=a.P().goal.saved+st.goalAdded, balance=st.balance;const result=a.profileAdapter.saveProfile({...st.profile,goalName:'我的新目标名称',readConsent:true});assert.equal(result.ok,true);assert.equal(a.P().goal.saved+st.goalAdded,funded);assert.equal(st.balance,balance)});
  check('目标类型变化不虚构另一位用户的资产和计划',()=>{const b=sandbox().api;b.S().profile.goalType='grow';const html=b.manageReply('打理资金').html;assert.ok(!html.includes('另有独立应急金'));assert.ok(!html.includes('家庭旅行'));assert.ok(!html.includes('已排队'))});
  check('可编辑目标名称作为文字显示，不成为HTML',()=>{const b=sandbox().api;b.S().profile.goalName='<img src=x onerror=alert(1)>';const html=b.manageReply('打理资金').html;assert.ok(!html.includes('<img'));assert.ok(html.includes('&lt;img'))});
  console.log(`${count} invariant checks passed. These are business-logic checks, not browser or competition benchmark results.`);
})().catch(error=>{console.error(error);process.exitCode=1});
