/* ETHOS-R1: deterministic, explainable policy priorities, never authorization. */
(function (root) {
  'use strict';
  const LABELS = {protect:'现金流守护',recover:'负债减压',accumulate:'财富积累',grow:'资产增长'};
  function defaults(p, id) {
    return {goalType:p.tone,goalName:p.goal.name,target:p.goal.target,reserve:p.reserve,
      incomeStability:id==='A'?'uncertain':'stable',style:id,automation:'confirm',readConsent:false};
  }
  function validate(v) {
    if (!v || !Object.prototype.hasOwnProperty.call(LABELS,v.goalType)) return {ok:false,error:'请选择当前最想解决的目标。'};
    if (typeof v.goalName!=='string' || !v.goalName.trim() || v.goalName.length>50) return {ok:false,error:'请用 1–50 个字写下目标。'};
    if (!Number.isFinite(Number(v.target)) || Number(v.target)<=0 || Number(v.target)>1e8) return {ok:false,error:'目标金额需要在 0 到 1 亿元之间。'};
    if (!Number.isFinite(Number(v.reserve)) || Number(v.reserve)<0 || Number(v.reserve)>1e8) return {ok:false,error:'请填写有效的最低现金预留。'};
    if (!['stable','mixed','uncertain'].includes(v.incomeStability) || !['A','B','C'].includes(v.style) || !['advice','confirm'].includes(v.automation)) return {ok:false,error:'请确认收入、展示和服务偏好。'};
    if (v.readConsent!==true) return {ok:false,error:'请主动同意读取当前模拟账户，才能建立画像。'};
    let assessment;
    if (Object.prototype.hasOwnProperty.call(v,'assessment')) {
      const checked=validateAssessment(v.assessment);
      if(!checked.ok) return checked;
      assessment=checked.value;
    }
    const value={goalType:v.goalType,goalName:v.goalName.trim(),target:Number(v.target),reserve:Number(v.reserve),incomeStability:v.incomeStability,style:v.style,automation:v.automation,readConsent:true};
    if(assessment!==undefined) value.assessment=assessment;
    return {ok:true,value};
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

  // ETHOS-R2: thresholds are prototype design choices, not a calibrated scale.
  const ASSESSMENT_FIELDS={"horizon":["使用期限",{"short":"一年以内（含一年）","medium":"一至三年（含三年）","long":"三年以上"}],"liquidityNeed":["用钱灵活性",{"high":"随时可能动用","medium":"部分资金可锁定","low":"三年以上不动用"}],"lossTolerance":["本金亏损接受程度",{"none":"不接受本金亏损","small":"可接受假设亏损 5%","medium":"可接受假设亏损 10%","high":"可接受假设亏损 20%"}],"lossReaction":["假设亏损后的选择",{"sell":"止损退出","review":"核对原因再决定","hold":"能接受且继续持有"}],"experience":["自述经验",{"none":"暂无经验","basic":"有基础经验","experienced":"较有经验"}],"knowledge":["非保本产品会否亏本金",{"possible":"可能亏损本金","impossible":"认为不可能亏损本金","unsure":"不确定是否会亏损本金"}],"clarification":["矛盾答案复核",{"safety":"以本金安全为先","confirmed":"已理解假设情景并确认原回答"}]};
  const ROLES={"review":{"name":"待完善/复核","headline":"先把情况说清楚","description":"补齐声明或核对未知数据后，再安排简单规划。","primaryView":"profile","actionLabel":"完善与复核画像"},"cash":{"name":"现金修复","headline":"先补上眼前的现金缺口","description":"先核对必要支付和现金预留，资金安排仍须通过 ARGOS。","primaryView":"bills","actionLabel":"核对现金缺口"},"buffer":{"name":"弹性缓冲","headline":"先给收入和开支留出余地","description":"围绕可随时动用的现金做简单规划，重新核对收入与缓冲。","primaryView":"life","actionLabel":"安排现金缓冲"},"debt":{"name":"负债减压","headline":"先理清近期还款压力","description":"核对未来 30 天还款与收入，优先安排必要还款。","primaryView":"bills","actionLabel":"核对还款安排"},"goal":{"name":"近期目标","headline":"先照顾一年内要用的钱","description":"按近期用途安排现金和目标进度，避免目标金额倒逼风险。","primaryView":"life","actionLabel":"查看近期目标"},"steady":{"name":"稳健积累","headline":"按能接受的节奏积累","description":"结合期限、灵活用钱需求与声明，进行简单的收支和目标规划。","primaryView":"actions","actionLabel":"查看积累安排"},"growth":{"name":"长期规划","headline":"为三年以上的目标留出时间","description":"在现金与期限约束内讨论长期规划，不推荐具体产品。","primaryView":"console","actionLabel":"查看长期规划"}};
  const BANDS=["未评估","本金安全优先","低波动意愿","中等波动意愿","较高波动意愿","高波动意愿"];
  const ASSESSMENT_CHECKS=["所有问卷字段均为用户声明；5%/10%/20%仅是假设亏损情景，不是收益、亏损概率或经验证的量表。","本原型设计：当前余额或30天最低预测余额低于现金预留，或预留后余量为负，视为现金缺口。","本原型设计：未来30天还款达到预计月收入60%（含），或零收入仍有还款，先处理债务压力。","本原型设计：余量小于30天日常支出与现金预留20%的较大值为缓冲薄；小于90天日常支出与现金预留的较大值，能力上限为3档。","本原型设计：亏损接受程度上限为1/2/3/5档；退出/复核/持有反应上限为2/4/5档；期限和流动性上限均为2/3/5档。","本原型设计：缺口能力上限1档；债务压力、收入不稳定、工资未到账冲击或薄缓冲上限2档；知识错误、不确定或无经验只做解释与简单规划，上限2档。","所有上限取较低值；收益目标与展示风格不参与风险上调，风险档位不等同授信评级。","画像角色不新增金融操作授权；钱的约束仍由 ARGOS 执行，每笔金融操作沿用原有确认流程。"];

  const has=(object,key)=>Object.prototype.hasOwnProperty.call(object,key);
  const ASSESSMENT_LIMITS={horizon:{short:2,medium:3,long:5},liquidityNeed:{high:2,medium:3,low:5},lossTolerance:{none:1,small:2,medium:3,high:5},lossReaction:{sell:2,review:4,hold:5}};
  const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
  function contradictory(a) {
    return (['medium','high'].includes(a.lossTolerance)&&a.lossReaction==='sell') ||
      (a.lossTolerance==='none'&&a.lossReaction==='hold');
  }
  function validateAssessment(value) {
    if(!object(value)) return {ok:false,error:'风险声明格式无效，请逐项填写。'};
    if(Object.keys(value).some(key=>!has(ASSESSMENT_FIELDS,key))) return {ok:false,error:'风险声明包含未知字段，请核对。'};
    for(const [key,[label,options]] of Object.entries(ASSESSMENT_FIELDS)) {
      if(key==='clarification'&&!has(value,key)) continue;
      if(!has(value,key)||typeof value[key]!=='string'||!has(options,value[key])) return {ok:false,error:'请确认'+label+'。'};
    }
    if(contradictory(value)&&!has(value,'clarification')) return {ok:false,error:'亏损接受程度与情景选择存在矛盾，请确认以本金安全为先，或理解情景后确认原回答。'};
    return {ok:true,value:Object.fromEntries(Object.keys(ASSESSMENT_FIELDS).filter(key=>has(value,key)).map(key=>[key,value[key]]))};
  }
  function assess({profile=null,balance=null,income=null,debt=null,daily=null,min=null,free=null,shocked=null,salaryReceived=null}={}) {
    const p=object(profile)?profile:{}, raw=p.assessment, a=object(raw)?raw:{};
    const checks=[...ASSESSMENT_CHECKS],reasons=[];
    const validated=validateAssessment(raw),complete=validated.ok;
    if(!complete) reasons.push(!has(p,'assessment')?'尚未填写风险声明；旧画像可继续保存，不补填风险答案。':validated.error);
    const facts={balance,income,debt,daily,min,free,reserve:p.reserve};
    const names={balance:'当前余额',income:'预计月收入',debt:'30天还款',daily:'日常支出',min:'30天最低预测余额',free:'预留后余量',reserve:'现金预留'};
    const unknown=Object.entries(facts).filter(([key,value])=>!Number.isFinite(value)||Math.abs(value)>1e308||(['income','debt','daily','reserve'].includes(key)&&value<0)).map(([key])=>names[key]);
    if(!['stable','mixed','uncertain'].includes(p.incomeStability)) unknown.push('收入稳定性');
    if(!['protect','recover','accumulate','grow'].includes(p.goalType)) unknown.push('当前目标');
    if(typeof shocked!=='boolean') unknown.push('工资冲击状态');
    if(typeof salaryReceived!=='boolean') unknown.push('工资到账状态');
    let known=unknown.length===0;
    if(unknown.length) reasons.push('待核对数据：'+unknown.join('、')+'；未知值不按零处理。');
    if(known&&(Math.abs(free-(min-p.reserve))>.011||min>balance+.011)) {
      known=false;reasons.push('最低余额、当前余额与预留后余量不一致，请使用同一快照复核。');
    }
    let capacity=0,capacityDetail='现金事实待核对，暂不判断承受能力。',role='review';
    const caps=[];
    if(known) {
      const gap=free<0||min<p.reserve||balance<p.reserve;
      const debtPressure=debt>0&&(income===0||debt>=income*.6);
      const thin=free<Math.max(daily*30,p.reserve*.2);
      const delayed=shocked&&!salaryReceived;
      const unstable=p.incomeStability!=='stable'||income===0||delayed;
      capacity=gap?1:(debtPressure||thin||unstable?2:(free<Math.max(daily*90,p.reserve)?3:5));
      capacityDetail='按本原型现金规则，承受能力上限为'+capacity+'档；不含未登记资产或支出。';
      caps.push(capacity);
      if(gap) {role='cash';reasons.push('当前余额或30天预测低于现金预留，优先修复现金缺口。');}
      else if(debtPressure) {role='debt';reasons.push('近期还款触及本原型债务压力阈值，优先于收入缓冲和长期目标。');}
      else if(unstable||thin) {role='buffer';reasons.push('收入不稳定、工资尚未到账冲击或现金缓冲较薄，先留出周转余地。');}
      else if(a.horizon==='short') {role='goal';reasons.push('你声明一年内需要使用这笔钱，先安排近期目标。');}
      else {role='steady';reasons.push('当前现金与还款未触及优先阈值，可按声明安排积累。');}
      if(delayed) reasons.push('工资冲击只更新现金能力与安排，不改写期限、亏损接受程度或其他风险偏好。');
    }
    for(const [key,limits] of Object.entries(ASSESSMENT_LIMITS)) {
      if(typeof a[key]==='string'&&has(limits,a[key])) caps.push(limits[a[key]]);
    }
    const simple=a.experience==='none'||['impossible','unsure'].includes(a.knowledge);
    if(simple) {caps.push(2);reasons.push('暂无经验或对非保本本金亏损的理解需解释；先说明可能亏损本金，仅做简单收支和目标规划，不作授信判断。');}
    const conflict=contradictory(a);
    if(conflict) {
      if(a.clarification==='safety') {caps.push(1);reasons.push('矛盾情景已选择本金安全优先，按1档约束，保留原声明。');}
      else if(a.clarification==='confirmed') reasons.push('已确认理解矛盾情景；保留原声明，仍取各项上限较低值。');
      else checks.push('需要复核矛盾情景后再完成评估。');
    }
    let level=complete&&known?Math.min(...caps):0;
    if(a.lossTolerance==='none') level=1;
    if(!complete||!known) role='review';
    else if(role==='steady'&&a.horizon==='long'&&a.liquidityNeed==='low'&&level>=3&&!simple) {
      role='growth';reasons.push('期限超过三年、流动性要求较低且约束允许，进入长期规划；目标金额不提高风险档位。');
    }
    const dimensions=Object.entries(ASSESSMENT_FIELDS).map(([key,[label,options]])=>{
      let value=typeof a[key]==='string'&&has(options,a[key])?options[a[key]]:'待声明',detail='用户声明；不自动推定或验证。';
      if(has(ASSESSMENT_LIMITS,key)&&typeof a[key]==='string'&&has(ASSESSMENT_LIMITS[key],a[key])) detail+='本原型设计：该声明对应上限'+ASSESSMENT_LIMITS[key][a[key]]+'档。';
      if(key==='clarification'&&!conflict) {
        detail='仅在亏损接受程度与情景选择矛盾时要求复核；当前无需复核。';
        if(!has(a,key)) value='无需复核';
      }
      return {key,label,value,detail};
    });
    const stability={stable:'稳定',mixed:'有波动',uncertain:'不确定'};
    dimensions.push({key:'incomeStability',label:'收入稳定性',value:typeof p.incomeStability==='string'&&has(stability,p.incomeStability)?stability[p.incomeStability]:'待声明',detail:'用户声明；工资事件不替你修改。'},
      {key:'capacity',label:'当前现金承受能力',value:capacity===0?'待核对':capacity+'档上限',detail:capacityDetail});
    let detail=level===0?'信息未齐或事实待核对，暂不评估风险档位。':'本原型取用户意愿、现金能力、期限、流动性与理解情况的较低上限；仅用于规划解释，不推荐具体产品。';
    if(a.lossTolerance==='none') detail='你声明不接受本金亏损，固定为1档；不承诺任何产品保本，其他信息待核对时仍需复核。';
    return {version:'ETHOS-R2',complete,reviewRequired:!complete||!known,role:{key:role,...ROLES[role]},riskBand:{level,label:BANDS[level],detail},dimensions,checks,reasons};
  }
  const api={LABELS,defaults,validate,policy,assess,version:'ETHOS-R1'};
  root.TroposProfiles=api;
  if(typeof module!=='undefined'&&module.exports) module.exports=api;
})(typeof window!=='undefined'?window:globalThis);
