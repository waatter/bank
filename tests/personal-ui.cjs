'use strict';
// The real assessor drives the real dashboard renderer; the DOM is controlled.
const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const engine=require('../assets/profile-engine.js');
const source=fs.readFileSync(path.join(__dirname,'../assets/personal-ui.js'),'utf8');
function setup(overrides={}) {
  const profile={goalType:'grow',goalName:'未来计划',target:10000,reserve:1000,incomeStability:'stable',style:'A',readConsent:true,
    assessment:{horizon:'long',liquidityNeed:'low',lossTolerance:'high',lossReaction:'hold',experience:'experienced',knowledge:'possible'}};
  const facts={profile,balance:30000,income:10000,debt:0,daily:100,min:15000,free:14000,shocked:false,salaryReceived:false,...overrides};
  const data={accountName:'用户',profile:facts.profile,facts:{balance:facts.balance},cash:{min:facts.min,free:facts.free},bills:[],goal:{saved:1000},salaryReceived:false,riskProfile:engine.assess(facts)};
  const calls=[],body={dataset:{}},handlers={};
  const root={innerHTML:'',addEventListener:(type,fn)=>handlers[type]=fn};
  const context={window:{},document:{body,getElementById:()=>root}};
  vm.runInNewContext(source,context);
  const ui=context.window.TroposPersonalUI;
  ui.init({getData:()=>data,openView:view=>calls.push(view),editProfile:()=>calls.push('edit')});
  return {root,body,data,facts,ui,calls,click:dataset=>handlers.click({target:{closest:selector=>selector==='[data-rp-edit]' ? (dataset.edit?{}:null) : dataset.rpView?{dataset}:null}})};
}
test('missing declarations show onboarding and no financial values',()=>{
  const h=setup({profile:{reserve:1000},balance:99999});
  assert.equal(h.body.dataset.financialRole,'review');
  assert.ok(h.root.innerHTML.includes('data-rp-edit'));
  assert.ok(!h.root.innerHTML.includes('99,999'));
  h.click({edit:true});assert.deepEqual(h.calls,['edit']);
});
test('the actual financial role changes the primary tasks and details',()=>{
  const growth=setup(),cash=setup({min:500,free:-500}),debt=setup({debt:7000}),goal=setup();
  goal.data.profile={...goal.data.profile,assessment:{...goal.data.profile.assessment,horizon:'short'}};
  goal.data.riskProfile=engine.assess({...goal.facts,profile:goal.data.profile});goal.ui.render();
  assert.equal(growth.body.dataset.financialRole,'growth');
  assert.equal(cash.body.dataset.financialRole,'cash');assert.ok(cash.root.innerHTML.includes('缺口'));
  assert.equal(debt.body.dataset.financialRole,'debt');assert.ok(debt.root.innerHTML.includes('先核对这些还款'));
  assert.equal(goal.body.dataset.financialRole,'goal');assert.ok(goal.root.innerHTML.includes('未来计划'));
  assert.notEqual(cash.root.innerHTML,growth.root.innerHTML);
});
test('a cash event updates the dashboard without changing declared preferences',()=>{
  const h=setup(),before=JSON.stringify(h.data.profile);
  h.data.cash={min:500,free:-500};h.data.riskProfile=engine.assess({...h.facts,...h.data.cash,shocked:true});h.ui.render();
  assert.equal(h.body.dataset.financialRole,'cash');assert.ok(h.root.innerHTML.includes('现金修复'));
  assert.equal(JSON.stringify(h.data.profile),before);
});
test('editable names are rendered as text, not executable markup',()=>{
  const h=setup();h.data.accountName='<img src=x onerror=alert(1)>';h.data.profile.goalName='<script>bad()</script>';h.ui.render();
  assert.ok(h.root.innerHTML.includes('&lt;img'));assert.ok(h.root.innerHTML.includes('&lt;script&gt;'));
  assert.ok(!h.root.innerHTML.includes('<img'));assert.ok(!h.root.innerHTML.includes('<script>'));
});
test('navigation only opens registered workbench views and does not perform financial operations',()=>{
  const h=setup();h.click({rpView:'transfer'});h.click({rpView:'javascript:bad()'});h.click({rpView:'actions'});
  assert.deepEqual(h.calls,['transfer','actions']);
});
test('missing monetary values are shown as unknown rather than invented zeros',()=>{
  const h=setup();h.data.facts.balance=undefined;h.ui.render();assert.ok(h.root.innerHTML.includes('待核对'));
});
