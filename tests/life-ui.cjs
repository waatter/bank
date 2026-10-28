/* Behavior checks for the planning UI; controlled DOM only, no visual acceptance. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../assets/life-ui.js'), 'utf8');
const clone = value => JSON.parse(JSON.stringify(value));
const settle = async () => { await new Promise(setImmediate); await new Promise(setImmediate); };
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => { resolve=a; reject=b; }); return {promise,resolve,reject}; };
const decode = value => value.replace(/&quot;/g,'"').replace(/&#39;/g,"'").replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&amp;/g,'&');
function attrs(text) {
  const result = {};
  for (const m of text.matchAll(/([\w:-]+)(?:="([^"]*)")?/g)) result[m[1]] = decode(m[2] || '');
  return result;
}
class ControlledRoot {
  constructor() { this.listeners={}; this._html=''; this.attributes={}; this.status={textContent:'',hidden:true,classList:{toggle(){}}}; this.dirty={textContent:''}; }
  set innerHTML(value) { this._html=String(value); }
  get innerHTML() { return this._html; }
  addEventListener(type,fn) { (this.listeners[type] ||= []).push(fn); }
  setAttribute(key,value) { this.attributes[key]=value; }
  contains(el) { return el && el.owner===this; }
  buttons() {
    return Array.from(this._html.matchAll(/<button\b([^>]*)>([\s\S]*?)<\/button>/g),m=>{
      const a=attrs(m[1]), dataset={};
      for(const [k,v] of Object.entries(a)) if(k.startsWith('data-')) dataset[k.slice(5).replace(/-([a-z])/g,(_,c)=>c.toUpperCase())]=v;
      const button={owner:this,dataset,disabled:Object.hasOwn(a,'disabled'),textContent:m[2].replace(/<[^>]+>/g,''),getAttribute:key=>a[key] ?? null,focus:()=>{this.focused=button;}};
      button.closest=selector=>selector==='button'?button:null;
      return button;
    });
  }
  querySelectorAll(selector) { return selector==='button' || selector==='button,input,select' ? this.buttons() : []; }
  querySelector(selector) {
    if(selector==='[data-life-status]') return this.status;
    if(selector==='[data-life-dirty]') return this.dirty;
    if(selector==='[data-life-results]') {
      const token='<div data-life-results>', start=this._html.indexOf(token);
      if(start<0) return null;
      const contentStart=start+token.length, tail='</div></div></div><p class="life-footer">', end=this._html.lastIndexOf(tail);
      assert.ok(end>=contentStart,'results wrapper exists in the rendered DOM');
      const root=this;
      return {get innerHTML(){return root._html.slice(contentStart,end);},set innerHTML(value){root._html=root._html.slice(0,contentStart)+value+root._html.slice(end);}};
    }
    // Form controls are supplied explicitly for input/submit tests; post-render form decoration is outside this harness.
    return null;
  }
  async emit(type,target) { const event={target,preventDefault(){this.defaultPrevented=true;}}; await Promise.all((this.listeners[type] || []).map(fn=>fn(event))); await settle(); }
  button(attribute,value) { const found=this.buttons().find(b=>b.getAttribute(attribute)===value); assert.ok(found,`rendered button ${attribute}=${value}`); return found; }
  async click(attribute,value) { const button=this.button(attribute,value); assert.ok(!button.disabled,`button ${value} is enabled`); await this.emit('click',button); }
  syntheticAction(value) { const button={owner:this,dataset:{lifeAction:value}}; button.closest=selector=>selector==='button'?button:null; return button; }
}
const categories=['living','work','health','moving','shopping','family','travel','education'];
function lifeState(persona='A',withDraft=true) {
  const scene={id:'scene-'+persona,title:persona+'-scene <img src=x onerror=bad()>',category:'living',amount:500,earliestDay:1,preferredDay:5,latestDay:15,importance:'flexible',allowSkip:true,allowAlternative:true,alternativeAmount:200,alternativeLabel:'借用 <svg onload=bad()>',status:withDraft?'draft':'committed'};
  return {version:7,settings:{illiquidAssets:2000,delayDays:3,expenseShock:600,shockDay:5,goalAmount:1000},scenes:[scene],catalogue:categories.map((category,i)=>({...scene,id:'catalogue-'+i,category,title:category+' "><script>bad()</script>',description:'额外开支 <img src=x onerror=bad()>'})),snapshot:{user:{persona,revision:11},balance:20000,goalFunded:3000,profile:{reserve:8000},bills:[{day:4,label:'租金 <iframe src=bad>',amount:-4000}]}};
}
function analysisFor(data) {
  const scenarios=['base','delay','shock','combined'].map((id,i)=>({id,label:['原情景','工资延迟','维修冲击','两者同时'][i]}));
  return {version:'METIS-LIFE-2',lifeVersion:data.version,accountRevision:data.snapshot.user.revision,financial:{liquidAssets:20000,goalFunded:3000,illiquidAssets:2000,reserve:8000},stressScenarios:scenarios,policies:['original','buffer','balanced'].map((id,i)=>({id,title:id+' <script>bad()</script>',summary:'测试 <img src=x>',allocations:[{sceneId:data.scenes[0].id,title:'生活事项 <svg onload=bad()>',amount:i===1?200:500,day:i===1?15:5,mode:i===1?'alternative':'original',importance:'flexible',changeReason:i===1?'借用代替购买':'保留安排'},{sceneId:'skipped-extra',title:'放弃的耳机',amount:0,day:5,mode:'skipped',importance:'flexible',changeReason:'本次不买'}],projections:scenarios.map(s=>({scenarioId:s.id,label:s.label,pts:[20000,16000,15000],min:15000,minDay:2,breachDays:0,maxGap:0,negativeCashDays:0,cashDeficit:0})),metrics:{worstGap:0,worstDeficit:0,breachDays:0,adjustmentScore:i,goalAmount:i===1?120:300,deferredGoal:700,expenseTotal:i===1?200:500}})),sensitivity:[{buffer:0,worstGap:0,breachDays:0,min:15000}],breakpoint:{extraDelayDays:30,alreadyAtRisk:false},assumptions:['明确假设 <script>bad()</script>'],recommendation:{policyId:'balanced',rationale:'请自行判断 <img src=x>'}};
}
function harness({completed=true,serverMode=true,withDraft=true,load,analyze,goal}={}) {
  const root=new ControlledRoot(), ctx={persona:'A',completed,serverMode,accountRevision:11}, calls={load:0,analyze:0,goal:[],commitPlan:[],save:[],openProfile:0,snapshots:0};
  const states={A:lifeState('A',withDraft),B:lifeState('B',withDraft)};
  let confirmResult=true;
  const confirmations=[];
  const adapter={context:()=>({...ctx}),load:async()=>{calls.load++;return load?load(ctx.persona):clone(states[ctx.persona]);},analyze:async()=>{calls.analyze++;return analyze?analyze(ctx.persona,states[ctx.persona]):analysisFor(states[ctx.persona]);},openProfile:()=>{calls.openProfile++;},proposeGoal:async request=>{calls.goal.push(request);if(goal)return goal(request);return {ok:true};},handleSnapshot:snapshot=>{calls.snapshots++;ctx.accountRevision=snapshot.user.revision;},commitPlan:async body=>{calls.commitPlan.push(clone(body));const data=states[ctx.persona];data.version++;data.snapshot.user.revision++;data.scenes.forEach(s=>{s.status='committed';s.committedAmount=200;s.committedDay=15;});return clone(data);},saveSettings:async body=>{calls.save.push(clone(body));const data=states[ctx.persona];data.version++;data.settings=clone(body.settings);return clone(data);},addScene:async()=>{throw new Error('not expected');},cancelScene:async()=>{throw new Error('not expected');},commitScene:async()=>{throw new Error('not expected');}};
  class FormDataStub { constructor(form){this.form=form;} get(name){const el=this.form.elements[name];return el && !el.disabled ? el.value : null;} }
  const sandbox={window:{confirm:text=>{confirmations.push(text);return confirmResult;}},document:{getElementById:id=>id==='lifeMount'?root:null},crypto:{randomUUID:()=> 'a177f0b5-7713-42b8-bd0b-380a8f901224'},FormData:FormDataStub};
  vm.runInNewContext(source,sandbox,{filename:'assets/life-ui.js'});
  const ui=sandbox.window.TroposLifeUI;
  return {root,ctx,calls,states,ui,adapter,confirmations,setConfirm:value=>{confirmResult=value;},start:async()=>{ui.init(adapter);await settle();}};
}
function settingsInput(data,goalAmount) {
  const form={dataset:{lifeForm:'settings'},elements:{},reportValidity:()=>true};
  for(const [name,value] of Object.entries({...data.settings,goalAmount})) form.elements[name]={name,value:String(value)};
  form.closest=selector=>selector==='[data-life-form]'?form:null;
  const target=form.elements.goalAmount;
  target.form=form;target.matches=selector=>selector==='input,select';
  target.closest=selector=>selector==='[data-life-form="settings"]'?form:null;
  return {target,form};
}
let passed=0;
async function check(name,run) {await run();passed++;console.log('PASS '+name);}
(async()=>{
  await check('consent and HTTP gates do not read the ledger',async()=>{
    const h=harness({completed:false});await h.start();assert.equal(h.calls.load,0);await h.ui.refresh();h.ui.render();assert.equal(h.calls.load,0);assert.ok(h.root.button('data-life-action','profile'));await h.root.click('data-life-action','profile');assert.equal(h.calls.openProfile,1);
    const file=harness({serverMode:false});await file.start();await file.ui.refresh();assert.equal(file.calls.load,0);assert.ok(!file.root.buttons().some(b=>['analyze','adopt'].includes(b.dataset.lifeAction)));
  });
  await check('init, render, refresh, eight categories and escaped user strings',async()=>{
    const h=harness();await h.start();assert.equal(h.calls.load,1);h.ui.init(h.adapter);h.ui.render();assert.equal(h.calls.load,1);
    assert.equal(h.root.buttons().filter(b=>b.getAttribute('data-life-category')!==null).length,8);
    for(const category of categories) assert.ok(h.root.buttons().some(b=>b.dataset.lifeCategory===category));
    assert.ok(h.root.innerHTML.includes('&lt;script&gt;bad()&lt;/script&gt;'));assert.ok(h.root.innerHTML.includes('&lt;iframe src=bad&gt;'));assert.ok(!h.root.innerHTML.includes('<script>'));assert.ok(!h.root.innerHTML.includes('<img'));
    await h.root.click('data-life-category','education');assert.equal(h.root.button('data-life-category','education').getAttribute('aria-pressed'),'true');assert.equal(h.root.focused.dataset.lifeCategory,'education');
    h.states.A.snapshot.balance=12345;await h.ui.refresh();assert.equal(h.calls.load,2);assert.ok(h.root.innerHTML.includes('¥12,345'));
  });
  await check('late load from a previous persona cannot overwrite the current account',async()=>{
    const waits={A:deferred(),B:deferred()},h=harness({load:who=>waits[who].promise});h.ui.init(h.adapter);assert.equal(h.calls.load,1);
    h.ctx.persona='B';h.ui.render();assert.equal(h.calls.load,2);waits.B.resolve(lifeState('B'));await settle();assert.match(h.root.innerHTML,/B-scene/);
    waits.A.resolve(lifeState('A'));await settle();h.ui.render();assert.match(h.root.innerHTML,/B-scene/);assert.ok(!h.root.innerHTML.includes('A-scene'));
  });
  await check('late analysis from a previous persona is discarded',async()=>{
    const waiting=deferred(),h=harness({analyze:()=>waiting.promise});await h.start();await h.root.click('data-life-action','analyze');assert.equal(h.calls.analyze,1);
    h.ctx.persona='B';h.ui.render();await settle();waiting.resolve(analysisFor(h.states.A));await settle();assert.match(h.root.innerHTML,/B-scene/);assert.ok(!h.root.innerHTML.includes('data-life-action="adopt"'));assert.ok(!h.root.innerHTML.includes('data-life-action="goal"'));
  });
  await check('mismatched analysis versions expose no adoption or transfer action',async()=>{
    for(const wrong of [{lifeVersion:99},{accountRevision:99},{version:'OLD-LIFE'}]) {
      const h=harness({analyze:(_,data)=>({...analysisFor(data),...wrong})});await h.start();await h.root.click('data-life-action','analyze');assert.match(h.root.innerHTML,/计算所用账本或假设已变化/);assert.ok(!h.root.innerHTML.includes('data-life-action="adopt"'));assert.ok(!h.root.innerHTML.includes('data-life-action="goal"'));
      await h.root.emit('click',h.root.syntheticAction('adopt'));await h.root.emit('click',h.root.syntheticAction('goal'));assert.equal(h.calls.commitPlan.length,0);assert.equal(h.calls.goal.length,0);
    }
  });
  await check('failed recalculation clears an earlier valid result instead of retaining actionable controls',async()=>{
    let stale=false;
    const h=harness({analyze:(_,data)=>({...analysisFor(data),...(stale?{lifeVersion:999}:{})})});await h.start();await h.root.click('data-life-action','analyze');assert.ok(h.root.innerHTML.includes('data-life-action="adopt"'));stale=true;await h.root.click('data-life-action','analyze');assert.ok(!h.root.innerHTML.includes('data-life-action="adopt"'));assert.ok(!h.root.innerHTML.includes('data-life-action="goal"'));
  });
  await check('three policies and four stress tabs execute; target amount follows the selected policy',async()=>{
    const h=harness({withDraft:false});await h.start();await h.root.click('data-life-action','analyze');assert.equal(h.root.buttons().filter(b=>b.dataset.lifePlan).length,3);assert.equal(h.root.buttons().filter(b=>b.dataset.lifeStress).length,4);assert.ok(!h.root.innerHTML.includes('<script>'));assert.ok(h.root.innerHTML.includes('至少 30'));
    await h.root.click('data-life-stress','combined');assert.equal(h.root.button('data-life-stress','combined').getAttribute('aria-pressed'),'true');await h.root.click('data-life-plan','buffer');await h.root.click('data-life-action','goal');assert.deepEqual(clone(h.calls.goal),[{amount:120,lifeVersion:h.states.A.version,accountRevision:h.states.A.snapshot.user.revision}]);assert.equal(h.calls.commitPlan.length,0);
  });
  await check('dirty settings remove old result actions and block stale handlers until save and recalculation',async()=>{
    const h=harness();await h.start();await h.root.click('data-life-action','analyze');const oldAdopt=h.root.button('data-life-action','adopt'),oldGoal=h.root.button('data-life-action','goal');const {target,form}=settingsInput(h.states.A,222);
    await h.root.emit('input',target);assert.ok(!h.root.innerHTML.includes('data-life-action="adopt"'));assert.ok(!h.root.innerHTML.includes('data-life-action="goal"'));await h.root.emit('click',oldAdopt);await h.root.emit('click',oldGoal);assert.equal(h.calls.commitPlan.length,0);assert.equal(h.calls.goal.length,0);await h.root.click('data-life-action','analyze');assert.equal(h.calls.analyze,1);
    await h.root.emit('submit',form);assert.equal(h.calls.save.length,1);assert.equal(h.calls.save[0].version,7);assert.equal(h.calls.save[0].settings.goalAmount,222);await h.root.click('data-life-action','analyze');assert.equal(h.calls.analyze,2);assert.ok(h.root.innerHTML.includes('data-life-action="adopt"'));
  });
  await check('drafts block goal proposals even when the disabled button handler is invoked',async()=>{
    const h=harness();await h.start();await h.root.click('data-life-action','analyze');const button=h.root.button('data-life-action','goal');assert.equal(button.disabled,true);await h.root.emit('click',button);assert.deepEqual(h.calls.goal,[]);assert.match(h.root.status.textContent,/先采用或取消生活假设/);
  });
  await check('cancelled confirmation does not call commitPlan; acceptance sends server policy and versions only',async()=>{
    const h=harness();await h.start();await h.root.click('data-life-action','analyze');await h.root.click('data-life-plan','buffer');h.setConfirm(false);await h.root.click('data-life-action','adopt');assert.equal(h.calls.commitPlan.length,0);assert.match(h.confirmations[0],/生活事项/);assert.match(h.confirmations[0],/¥200，第 15 天/);assert.match(h.confirmations[0],/放弃的耳机：本次不安排/);assert.match(h.confirmations[0],/只登记待付事项，未付款/);
    h.setConfirm(true);await h.root.click('data-life-action','adopt');assert.equal(h.calls.commitPlan.length,1);assert.deepEqual(Object.keys(h.calls.commitPlan[0]).sort(),['accountRevision','confirmation','policyId','requestKey','version'].sort());assert.deepEqual(h.calls.commitPlan[0],{version:7,accountRevision:11,policyId:'buffer',confirmation:true,requestKey:'a177f0b5-7713-42b8-bd0b-380a8f901224'});assert.equal(h.calls.snapshots,1);assert.equal(h.calls.analyze,2);assert.ok(!h.root.innerHTML.includes('data-life-action="adopt"'));assert.equal(h.root.button('data-life-action','goal').disabled,false);
  });
  await check('goal draft carries the analysed versions; a 409 clears the stale analysis',async()=>{
    const stale=Object.assign(new Error('生活规划已更新，请刷新后重试。'),{status:409,code:'LIFE_VERSION_CONFLICT'});
    const h=harness({withDraft:false,goal:()=>{throw stale;}});await h.start();await h.root.click('data-life-action','analyze');
    const analysed=analysisFor(h.states.A);
    h.states.A.version=8;await h.root.click('data-life-action','goal');
    assert.equal(h.calls.goal.length,1);assert.equal(h.calls.goal[0].lifeVersion,analysed.lifeVersion);assert.equal(h.calls.goal[0].accountRevision,analysed.accountRevision);
    assert.match(h.root.status.textContent,/已更新/);assert.ok(!h.root.innerHTML.includes('data-life-action="goal"'));assert.ok(!h.root.innerHTML.includes('data-life-action="adopt"'));
    await h.root.emit('click',h.root.syntheticAction('goal'));assert.equal(h.calls.goal.length,1);
  });
  console.log(`${passed} Life UI behavior checks passed (controlled DOM; no visual acceptance).`);
})().catch(error=>{console.error(error);process.exitCode=1;});
