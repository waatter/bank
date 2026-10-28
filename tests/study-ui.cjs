'use strict';
// Controlled DOM/unit acceptance only. All operations and exports are synthetic
// internal_acceptance, never evidence of human participation or real phone use.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {SCHEMA,TASKS,createTrial,sessionId} = require('../assets/study.js');
const js = fs.readFileSync(path.join(__dirname,'../assets/study.js'),'utf8');
const html = fs.readFileSync(path.join(__dirname,'../study.html'),'utf8');
const css = fs.readFileSync(path.join(__dirname,'../assets/study.css'),'utf8');
const settings = () => ({session_id:'01234567-89ab-4cde-8fab-0123456789ab',participant_id:'P999',record_kind:'internal_acceptance',interaction_source:'automated_test',consent:true,environment:{device:'emulation',network:'Wi-Fi',vpn:'off'}});
const result = () => ({completed:false,help_count:null,misunderstanding_count:null,misunderstanding_notes:null,comprehension_answer:null,comprehension_correct:null,feedback:null});
function model() {
  let ms = 0;
  const clock=()=>({wall:new Date(Date.UTC(2026,9,27)+ms).toISOString(),monotonic:ms});
  return {trial:createTrial(settings(),clock),advance:n=>{ms+=n;}};
}
function harness() {
  const ids=['setup','setup-form','record-kind','participant-id','device','network','vpn','consent','session','session-label','session-meta','tasks','export','restart','export-note','notice','archive-note'];
  const nodes=Object.fromEntries(ids.map(id=>[id,{id,value:'',checked:false,textContent:'',innerHTML:'',hidden:false,listeners:{},addEventListener(type,cb){this.listeners[type]=cb;}}]));
  const forbidden=[],downloads=[],blobs=[],revoked=[],events={}; let confirms=true;
  nodes['setup-form'].reset=()=>{for(const id of ['record-kind','participant-id','device','network','vpn']) nodes[id].value='';nodes.consent.checked=false;};
  const trip=what=>()=>{forbidden.push(what);throw Error('Forbidden '+what);};
  const context={window:{document:{getElementById:id=>nodes[id],body:{appendChild:()=>{}},createElement:tag=>{assert.equal(tag,'a');return {click(){downloads.push({name:this.download,url:this.href});},remove(){}};}},crypto:{randomUUID:()=>settings().session_id},
    URL:{createObjectURL:blob=>{blobs.push(blob);return 'blob:internal-only';},revokeObjectURL:url=>revoked.push(url)},Blob:class{constructor(parts,opts){this.parts=parts;this.type=opts.type;}},
    setTimeout:fn=>{fn();},confirm:()=>confirms,addEventListener:(name,fn)=>{events[name]=fn;},
    fetch:trip('fetch'),XMLHttpRequest:trip('XHR'),WebSocket:trip('socket'),EventSource:trip('events'),navigator:{sendBeacon:trip('beacon')},
    localStorage:{setItem:trip('storage'),getItem:trip('storage')},sessionStorage:{setItem:trip('storage')},
  },performance:{now:()=>12345},Date};
  Object.assign(context,{fetch:context.window.fetch,XMLHttpRequest:context.window.XMLHttpRequest,WebSocket:context.window.WebSocket,navigator:context.window.navigator,localStorage:context.window.localStorage});
  vm.runInNewContext(js,context,{filename:'study.js'});
  const fire=(id,type,extra={})=>nodes[id].listeners[type]({isTrusted:true,preventDefault(){},...extra});
  const setup=()=>{Object.assign(nodes['record-kind'],{value:'internal_acceptance'});nodes['participant-id'].value='P999';nodes.device.value='emulation';nodes.network.value='unknown';nodes.vpn.value='unknown';nodes.consent.checked=true;fire('consent','change');fire('setup-form','submit');};
  const start=id=>fire('tasks','click',{target:{closest:s=>s==='[data-start]'?{dataset:{start:id}}:null}});
  const finish=(id,changes={})=>{
    const values={completed:'false',help_count:'',misunderstanding_count:'',misunderstanding_notes:'',comprehension_answer:'',comprehension_correct:'',feedback:'',...changes};
    const form={dataset:{end:id},elements:{namedItem:name=>({value:values[name]})}};
    fire('tasks','submit',{target:{closest:()=>form}});
  };
  return {nodes,forbidden,downloads,blobs,revoked,events,fire,setup,start,finish,setConfirm:value=>{confirms=value;}};
}

test('six task slots begin with no timing, result, help count or success inference',()=>{
  const {trial}=model(),r=trial.snapshot();assert.equal(r.schema,SCHEMA);assert.equal(r.tasks.length,6);
  for(const t of r.tasks){assert.equal(t.status,'not_started');for(const [k,v] of Object.entries(t))if(!['id','status'].includes(k))assert.equal(v,null,k);}
  assert.equal(r.consent.method,'internal_test_only');
});
test('LAN HTTP phones can create UUID without the secure-context randomUUID API',()=>{
  const id=sessionId({getRandomValues:bytes=>bytes.fill(17)});
  assert.match(id,/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  assert.equal(sessionId({randomUUID:()=>settings().session_id}),settings().session_id);
});
test('consent/device/label explicit; automated real labels forbidden',()=>{
  for(const changes of [{consent:false},{record_kind:''},{participant_id:'Name'},{participant_id:'P１２３'},{record_kind:'real_participant'},{environment:{device:'',network:'Wi-Fi',vpn:'off'}}]) assert.throws(()=>createTrial({...settings(),...changes}));
});
test('reject unstarted completion, parallel starts, second start and double end',()=>{
  const {trial}=model(),id=TASKS[0].id;assert.throws(()=>trial.end(id,result()));trial.start(id);
  assert.throws(()=>trial.start(id));assert.throws(()=>trial.start(TASKS[1].id));trial.end(id,result());
  assert.throws(()=>trial.end(id,{...result(),completed:true}));assert.throws(()=>trial.start(id));
});
test('raw seconds include waiting; no rounding or subtraction of help time',()=>{
  const {trial,advance}=model();trial.start(TASKS[0].id);advance(12345.678);trial.end(TASKS[0].id,{...result(),help_count:2});
  const t=trial.snapshot().tasks[0];assert.equal(t.duration,12.345678);assert.equal(t.help_count,2);assert.equal(t.completed,false);
});
test('returned snapshots cannot rewrite locked original or earlier exports',()=>{
  const {trial}=model();trial.start(TASKS[0].id);trial.end(TASKS[0].id,result());
  const earlier=trial.exportRecord();earlier.tasks[0].completed=true;trial.snapshot().tasks[0].help_count=9;
  assert.equal(trial.exportRecord().tasks[0].completed,false);assert.equal(trial.snapshot().tasks[0].help_count,null);
  assert.equal(trial.exportRecord().export_index,3);
});
test('invalid answers/counts/completion fail without partially ending task',()=>{
  const {trial}=model();trial.start(TASKS[0].id);
  for(const changes of [{completed:null},{help_count:-1},{help_count:1.2},{misunderstanding_count:true},{comprehension_correct:true},{feedback:'12345678901'},{feedback:'user@example.com'}]) {
    assert.throws(()=>trial.end(TASKS[0].id,{...result(),...changes}));assert.equal(trial.snapshot().tasks[0].status,'in_progress');
  }
});
test('DOM consent starts unchecked; untrusted checkbox/submit cannot create a trial',()=>{
  assert.doesNotMatch(html,/<input[^>]*id="consent"[^>]*checked/i);
  const h=harness();h.nodes.consent.checked=true;h.fire('consent','change',{isTrusted:false});assert.equal(h.nodes.consent.checked,false);
  h.fire('setup-form','submit',{isTrusted:false});assert.match(h.nodes.notice.textContent,/人工操作/);assert.equal(h.nodes.tasks.innerHTML,'');
  h.fire('setup-form','submit');assert.match(h.nodes.notice.textContent,/本人主动勾选/);
});
test('UI remains internal/emulated and starts without prefilled completion',()=>{
  const h=harness();h.setup();assert.match(h.nodes['session-label'].textContent,/内部验收数据/);assert.match(h.nodes['session-meta'].textContent,/设备模拟，不计真人结果/);
  h.start(TASKS[0].id);const markup=h.nodes.tasks.innerHTML;
  assert.match(markup,/计时中/);assert.match(markup,/<option value="">请选择/);assert.doesNotMatch(markup,/<option[^>]*selected/);
  assert.match(markup,/未采集；无帮助请填0/);assert.match(markup,/data-start="expected_vs_received" disabled/);
});
test('UI rejects unstarted finish and untrusted task clicks; end locks input controls',()=>{
  const h=harness();h.setup();h.finish(TASKS[0].id);assert.match(h.nodes.notice.textContent,/必须先开始/);
  h.fire('tasks','click',{isTrusted:false,target:{closest:()=>({dataset:{start:TASKS[0].id}})}});assert.doesNotMatch(h.nodes.tasks.innerHTML,/计时中/);
  h.start(TASKS[0].id);h.finish(TASKS[0].id,{completed:''});assert.match(h.nodes.notice.textContent,/主动选择是否完成/);
  h.finish(TASKS[0].id);assert.match(h.nodes.tasks.innerHTML,/已结束 · 已锁定/);assert.doesNotMatch(h.nodes.tasks.innerHTML,/data-end="profile_correction"/);
  h.finish(TASKS[0].id,{completed:'true'});assert.match(h.nodes.notice.textContent,/结束后不可改写/);
});
test('feedback is rendered as text and never interpreted as markup',()=>{
  const h=harness();h.setup();h.start(TASKS[0].id);h.finish(TASKS[0].id,{feedback:'<img src=x onerror=bad()> & "test"'});
  assert.match(h.nodes.tasks.innerHTML,/&lt;img/);assert.doesNotMatch(h.nodes.tasks.innerHTML,/<img/);
});
test('download is local Blob, missing fields remain null, and nothing uploads or persists',()=>{
  const h=harness();h.setup();h.start(TASKS[0].id);h.finish(TASKS[0].id);h.fire('export','click');
  assert.equal(h.downloads.length,1);assert.match(h.downloads[0].name,/tropos-trial-P999-.*-1.json/);
  const r=JSON.parse(h.blobs[0].parts.join(''));assert.equal(r.record_kind,'internal_acceptance');assert.equal(r.tasks[0].help_count,null);
  assert.equal(r.tasks[0].completed,false);assert.equal(h.blobs[0].type,'application/json');assert.equal(h.revoked.length,1);assert.deepEqual(h.forbidden,[]);
});
test('restart warns, can be cancelled, preserves export reminder, resets consent',()=>{
  const h=harness();h.setup();h.fire('export','click');h.setConfirm(false);h.fire('restart','click');assert.equal(h.nodes.setup.hidden,true);
  h.setConfirm(true);h.fire('restart','click');assert.equal(h.nodes.setup.hidden,false);assert.equal(h.nodes.consent.checked,false);
  assert.match(h.nodes['archive-note'].textContent,/曾发起下载/);assert.equal(h.downloads.length,1);
  h.fire('setup-form','submit');assert.match(h.nodes.notice.textContent,/本人主动勾选同意/);
});
test('restart before export never implies that a record has been saved',()=>{
  const h=harness();h.setup();h.fire('restart','click');assert.match(h.nodes['archive-note'].textContent,/未导出/);assert.deepEqual(h.downloads,[]);
});
test('CSP forbids connections/forms; assets/fonts local and 390px styles responsive',()=>{
  assert.match(html,/connect-src 'none'/);assert.match(html,/form-action 'none'/);assert.doesNotMatch(html,/https?:\/\//);
  assert.match(html,/assets\/typography.css/);assert.match(css,/@media\(max-width:600px\)/);assert.match(css,/min-width:0/);
  assert.doesNotMatch(js,/\b(?:fetch|XMLHttpRequest|WebSocket|EventSource|sendBeacon|localStorage|sessionStorage)\s*[(.]/);
  assert.doesNotMatch(html,/<form[^>]*\baction=/);assert.match(js,/event\.isTrusted/);
});
