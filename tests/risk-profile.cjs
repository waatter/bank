'use strict';
// Pure rule tests; --assess is the batch bridge for full JSON parity with Python.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const engine=require('../assets/profile-engine.js');
const assessment={horizon:'long',liquidityNeed:'low',lossTolerance:'high',lossReaction:'hold',experience:'experienced',knowledge:'possible'};
const profile={goalType:'grow',goalName:'长期目标',target:100000,reserve:1000,incomeStability:'stable',style:'C',automation:'confirm',readConsent:true,assessment};
const base={profile,balance:50000,income:10000,debt:0,daily:100,min:30000,free:29000,shocked:false,salaryReceived:false};
const evaluate=(facts={},answers={})=>engine.assess({...base,...facts,profile:{...profile,...facts.profile,assessment:{...assessment,...answers}}});
if(process.argv.includes('--assess')) {
  process.stdout.write(JSON.stringify(JSON.parse(fs.readFileSync(0,'utf8')).map(input=>engine.assess(input))));
} else {
  let count=0;
  function test(name,run){run();count++;console.log('PASS',name);}
  test('R1 policy and defaults stay compatible; R2 has its own version',()=>{
    assert.equal(engine.version,'ETHOS-R1');assert.equal(evaluate().version,'ETHOS-R2');
    const legacy={...profile};delete legacy.assessment;
    assert.equal(engine.validate(legacy).ok,true);assert.ok(!('assessment' in engine.validate(legacy).value));
    assert.deepEqual(engine.policy({...base,profile:legacy}).map(x=>x.value).reduce((a,b)=>a+b,0),100);
    assert.ok(!('assessment' in engine.defaults({tone:'grow',goal:{name:'目标',target:100},reserve:100},'C')));
    const r=engine.assess({...base,profile:legacy});assert.equal(r.complete,false);assert.equal(r.reviewRequired,true);assert.equal(r.role.key,'review');assert.equal(r.riskBand.level,0);
  });
  test('all seven roles and primary views',()=>{
    const cases=[['review',engine.assess()],['cash',evaluate({min:999,free:-1})],['debt',evaluate({debt:6000})],['buffer',evaluate({profile:{incomeStability:'mixed'}})],['goal',evaluate({}, {horizon:'short'})],['steady',evaluate({}, {lossTolerance:'small'})],['growth',evaluate()]];
    for(const [key,result] of cases){assert.equal(result.role.key,key);assert.ok(['profile','actions','bills','life','console'].includes(result.role.primaryView));assert.equal(Object.keys(result.role).length,6);}
  });
  test('gap precedes debt; debt precedes instability and near goal',()=>{
    assert.equal(evaluate({debt:9000,min:999,free:-1}).role.key,'cash');
    assert.equal(evaluate({debt:6000,profile:{incomeStability:'uncertain'}},{horizon:'short'}).role.key,'debt');
    assert.equal(evaluate({debt:5999.99}).role.key,'growth');assert.equal(evaluate({income:0,debt:1}).role.key,'debt');
  });
  test('buffer and ability thresholds use strict less-than',()=>{
    assert.equal(evaluate({min:3999.99,free:2999.99}).role.key,'buffer');
    assert.equal(evaluate({min:4000,free:3000}).role.key,'growth');
    assert.equal(evaluate({min:4000,free:3000}).riskBand.level,3);
    assert.equal(evaluate({min:10000,free:9000}).riskBand.level,5);
    assert.equal(evaluate({profile:{reserve:20000},min:23999.99,free:3999.99}).role.key,'buffer');
  });
  test('risk caps respect term, liquidity, experience and knowledge',()=>{
    for(const key of ['horizon','liquidityNeed']) for(const [value,level] of (key==='horizon'?[['short',2],['medium',3],['long',5]]:[['high',2],['medium',3],['low',5]])) assert.equal(evaluate({}, {[key]:value}).riskBand.level,level);
    for(const knowledge of ['impossible','unsure']) {const r=evaluate({}, {knowledge});assert.equal(r.riskBand.level,2);assert.equal(r.role.key,'steady');assert.ok(r.reasons.some(x=>x.includes('可能亏损本金')));}
    assert.equal(evaluate({}, {experience:'none'}).riskBand.level,2);
    assert.equal(evaluate({}, {lossReaction:'review'}).riskBand.level,4);
  });
  test('contradictions need clarification; no-loss always stays at one',()=>{
    for(const lossTolerance of ['medium','high']) {
      const a={...assessment,lossTolerance,lossReaction:'sell'};
      assert.equal(engine.validate({...profile,assessment:a}).ok,false);
      assert.equal(evaluate({},a).role.key,'review');
      assert.equal(evaluate({}, {...a,clarification:'safety'}).riskBand.level,1);
      assert.equal(evaluate({}, {...a,clarification:'confirmed'}).riskBand.level,2);
    }
    assert.equal(evaluate({}, {lossTolerance:'none'}).riskBand.level,1);
    assert.equal(evaluate({}, {lossTolerance:'none'}).role.key,'review');
    for(const lossReaction of ['sell','review','hold']) {
      const a={lossTolerance:'none',lossReaction,...(lossReaction==='hold'?{clarification:'confirmed'}:{})};
      assert.equal(evaluate({},a).riskBand.level,1);assert.equal(evaluate({},a).complete,true);
      assert.equal(evaluate({income:null},a).riskBand.level,1);
    }
  });
  test('unknown and inconsistent facts stay unknown; zero is valid',()=>{
    for(const key of ['balance','income','debt','daily','min','free','shocked','salaryReceived']) for(const value of [null,'',[],{},'0']) {
      const r=evaluate({[key]:value});assert.equal(r.complete,true);assert.equal(r.reviewRequired,true);assert.equal(r.role.key,'review');assert.equal(r.riskBand.level,0);
    }
    for(const facts of [{income:-1},{debt:-1},{daily:-1},{free:2},{min:60000,free:59000},{income:NaN},{balance:Infinity}]) assert.equal(evaluate(facts).role.key,'review');
    assert.equal(evaluate({balance:0,income:0,debt:0,daily:0,min:0,free:0,profile:{reserve:0}}).role.key,'buffer');
    assert.equal(evaluate({balance:-1,min:-1,free:-1001}).role.key,'cash');
  });
  test('events, style and goal amount never rewrite declared preferences',()=>{
    const original=JSON.stringify(base);const before=evaluate();const after=evaluate({shocked:true});
    assert.equal(after.role.key,'buffer');assert.equal(after.riskBand.level,2);
    assert.deepEqual(after.dimensions.filter(d=>d.key!=='capacity'),before.dimensions.filter(d=>d.key!=='capacity'));
    assert.equal(evaluate({shocked:true,salaryReceived:true}).role.key,before.role.key);
    for(const style of ['A','B','C']) assert.deepEqual(evaluate({profile:{style}}),before);
    assert.deepEqual(evaluate({profile:{target:1e8,goalType:'grow'}}),before);assert.equal(JSON.stringify(base),original);
  });
  test('strict assessment validation preserves answers without filling any',()=>{
    assert.deepEqual(engine.validate(profile).value.assessment,assessment);
    for(const value of [null,[],{},'high',{...assessment,horizon:'forever'},{...assessment,knowledge:true},{...assessment,lossTolerance:5},{...assessment,clarification:null},{...assessment,confidence:.9}]) assert.equal(engine.validate({...profile,assessment:value}).ok,false);
    for(const key of Object.keys(assessment)){const a={...assessment};delete a[key];assert.equal(engine.validate({...profile,assessment:a}).ok,false);}
    assert.equal(engine.validate({...profile,goalType:'toString'}).ok,false);
    const result=evaluate();assert.ok(result.checks.some(x=>x.includes('本原型设计')));assert.ok(result.checks.some(x=>x.includes('ARGOS')));assert.ok(!JSON.stringify(result).includes('confidence'));
  });
  console.log(`${count} risk-profile groups passed.`);
}
