/* ETHOS-R1: deterministic, explainable policy priorities, never authorization. */
(function (root) {
  'use strict';
  const LABELS = {protect:'现金流守护',recover:'负债减压',accumulate:'财富积累',grow:'资产增长'};
  function defaults(p, id) {
    return {goalType:p.tone,goalName:p.goal.name,target:p.goal.target,reserve:p.reserve,
      incomeStability:id==='A'?'uncertain':'stable',style:id,automation:'confirm',readConsent:false};
  }
  function validate(v) {
    if (!v || !LABELS[v.goalType]) return {ok:false,error:'请选择当前最想解决的目标。'};
    if (typeof v.goalName!=='string' || !v.goalName.trim() || v.goalName.length>50) return {ok:false,error:'请用 1–50 个字写下目标。'};
    if (!Number.isFinite(Number(v.target)) || Number(v.target)<=0 || Number(v.target)>1e8) return {ok:false,error:'目标金额需要在 0 到 1 亿元之间。'};
    if (!Number.isFinite(Number(v.reserve)) || Number(v.reserve)<0 || Number(v.reserve)>1e8) return {ok:false,error:'请填写有效的最低现金预留。'};
    if (!['stable','mixed','uncertain'].includes(v.incomeStability) || !['A','B','C'].includes(v.style) || !['advice','confirm'].includes(v.automation)) return {ok:false,error:'请确认收入、展示和服务偏好。'};
    if (v.readConsent!==true) return {ok:false,error:'请主动同意读取当前模拟账户，才能建立画像。'};
    return {ok:true,value:{goalType:v.goalType,goalName:v.goalName.trim(),target:Number(v.target),reserve:Number(v.reserve),incomeStability:v.incomeStability,style:v.style,automation:v.automation,readConsent:true}};
  }
  function policy({profile,free,income,debt,shocked}) {
    const scores={protect:20,recover:5,accumulate:15,grow:5};
    const reasons=Object.fromEntries(Object.keys(scores).map(k=>[k,[]]));
    const add=(k,v,s)=>{scores[k]+=v;reasons[k].push(s)};
    add(profile.goalType,40,'你确认的当前目标');
    if(profile.incomeStability!=='stable') add('protect',profile.incomeStability==='uncertain'?25:15,'你声明的收入存在波动');
    if(free<0) add('protect',80,'30 天预测最低余额低于你设定的预留');
    else if(free<profile.reserve*.2) add('protect',30,'安全线以上的余量较小');
    else add('accumulate',15,'必要支付和现金预留后仍有余量');
    if(income>0 && debt/income>=.6) add('recover',40,'未来 30 天还款占预计月收入较高');
    if(shocked) add('protect',50,'收入延期，相关任务需要暂停复核');
    const total=Object.values(scores).reduce((a,b)=>a+b,0);
    const rows=Object.entries(scores).map(([key,score])=>({key,name:LABELS[key],value:Math.floor(score/total*100),reasons:reasons[key].length?reasons[key]:['保留为兼顾目标']})).sort((a,b)=>b.value-a.value);
    rows[0].value+=100-rows.reduce((a,b)=>a+b.value,0);
    return rows;
  }
  const api={LABELS,defaults,validate,policy,version:'ETHOS-R1'};
  root.TroposProfiles=api;
  if(typeof module!=='undefined'&&module.exports) module.exports=api;
})(typeof window!=='undefined'?window:globalThis);
