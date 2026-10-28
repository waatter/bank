/* TROPOS · 独立生活规划。DeepSeek Pro 骨架经契约、确认流程与异步隔离审查后集成。 */
(function () {
  'use strict';
  const categories = {living:'日常生活',work:'工作需要',health:'健康医疗',moving:'搬家安居',shopping:'可选购物',family:'家庭支持',travel:'出行旅行',education:'学习成长'};
  const priorities = {essential:'必要',important:'重要',flexible:'可调整'};
  const statuses = {draft:'假设 · 未登记',committed:'已纳入未来账单 · 未付款',cancelled:'已取消假设'};
  let adapter, persona, state = null, analysis = null, selectedPlan = '', selectedStress = '', category = 'living', templateId = '';
  let busy = false, loading = false, message = '', messageError = false, dirtySettings = false;
  let sequence = 0, inputEpoch = 0, actionSequence = 0, settingsDraft = null, sceneDraft = null;
  const mount = () => document.getElementById('lifeMount');
  const context = () => adapter.context();
  const esc = value => String(value == null ? '' : value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const clamp = (value,min,max) => Math.min(Math.max(value,min),max);
  // 采纳 DS 的选择器与投影点归一化，补足本项目的每日余额数组格式。
  const policyById = (data,id) => (data && data.policies || []).find(p=>p.id===id) || null;
  const projectionFor = (policy,id) => (policy && policy.projections || []).find(p=>p.scenarioId===id) || (policy && policy.projections || [])[0] || null;
  function mapProjectionPoints(pts) {
    return (pts || []).map((p,i)=>Array.isArray(p) ? [number(p[0]),number(p[1])] : p && typeof p==='object' ? [number(p.day == null ? p.x : p.day),number(p.cash == null ? p.y : p.cash)] : [i,number(p)]);
  }
  const number = value => Number.isFinite(Number(value)) ? Number(value) : 0;
  const money = value => '¥' + number(value).toLocaleString('zh-CN',{maximumFractionDigits:2});
  const disabled = () => busy || loading ? ' disabled' : '';
  const version = () => state && state.version;
  const accountRevision = () => state && state.snapshot && state.snapshot.user && state.snapshot.user.revision;
  function validIdentity(result, who) { const snap = result && result.snapshot; return context().persona === who && (!snap || Boolean(snap.user && snap.user.persona === who)); }
  function failure(error) { return error && (error.status === 409 || /STALE|VERSION|REVISION/.test(error.code || '')) ? '账本或生活假设已更新，请点「刷新数据」后核对并重新操作。' : String(error && error.message || '暂时未完成，请稍后重试。'); }
  function setMessage(text, isError) {
    message = text || ''; messageError = Boolean(isError);
    const el = mount() && mount().querySelector('[data-life-status]');
    if (el) { el.textContent = message; el.hidden = !message; el.classList.toggle('life-status-error',messageError); }
  }
  function focusControl(attribute,value) { const button=Array.from(mount().querySelectorAll('button')).find(el=>el.getAttribute(attribute)===value); if(button) button.focus({preventScroll:true}); }
  function invalidate() { analysis = null; selectedPlan = ''; selectedStress = ''; }
  function syncPersona() {
    const who = context().persona;
    if (persona === who) return false;
    persona = who; state = null; invalidate(); busy = false; loading = false; dirtySettings = false;
    message = ''; settingsDraft=null; sceneDraft=null; category = 'living'; templateId = ''; ++sequence; ++actionSequence; ++inputEpoch;
    return true;
  }
  function setBusy(value) {
    busy = value;
    if (!mount()) return;
    mount().setAttribute('aria-busy',String(value || loading));
    mount().querySelectorAll('button,input,select').forEach(el => {
      if (value) { el.dataset.lifeWasDisabled = el.disabled ? '1' : '0'; el.disabled = true; }
      else if ('lifeWasDisabled' in el.dataset) { el.disabled = el.dataset.lifeWasDisabled === '1'; delete el.dataset.lifeWasDisabled; }
    });
  }
  function header() {
    return `<header class="life-header"><div><p class="life-eyebrow">资产与生活 · 把钱放回生活</p><h1>生活的下一步，留出余地。</h1><p class="life-intro">把想做的事摆在一起，看清未来 30 天的现金安排。先比较，再决定。</p></div><button type="button" class="life-button" data-life-action="refresh"${disabled()}>${loading ? '正在更新…' : '刷新数据'}</button></header><div data-life-status class="life-status ${messageError ? 'life-status-error' : ''}" role="status" aria-live="polite" ${message ? '' : 'hidden'}>${esc(message)}</div>`;
  }
  function assets() {
    const s = state.snapshot || {}, f = analysis && analysis.financial || {};
    const bills = (s.bills || []).filter(x => number(x.amount) < 0);
    const total = bills.reduce((sum,x) => sum + Math.abs(number(x.amount)),0);
    const cards = [
      ['当前可支付现金',f.liquidAssets == null ? s.balance : f.liquidAssets,'来自模拟账户，可用于近期支出'],
      ['隔离的目标资金',f.goalFunded == null ? s.goalFunded : f.goalFunded,'已单独存放，不计入日常现金'],
      ['不动用资产',state.settings.illiquidAssets,'非现金，仅记账，不用于支付'],
      ['必要未来账单',total,'已登记，尚未付款；不重复添加']
    ];
    return `<dl class="life-kpis">${cards.map(x => `<div class="life-kpi"><dt>${esc(x[0])}</dt><dd>${money(x[1])}</dd><small>${esc(x[2])}</small></div>`).join('')}</dl><details class="life-details life-panel"><summary>查看已登记的未来账单 · ${bills.length} 笔</summary><ul class="life-bills">${bills.map(b => `<li><span>第 ${number(b.day)} 天 · ${esc(b.label)}</span><span class="life-money">${money(Math.abs(number(b.amount)))}</span></li>`).join('') || '<li>目前没有已登记账单。</li>'}</ul><p class="life-note">这些账单已计入现金路径。新增生活场景只填写额外、未登记的开支。</p></details>`;
  }
  function numericField(name,label,value,min,max,step,note) {
    return `<label class="life-field">${label}<input type="number" name="${name}" min="${min}" max="${max}" step="${step}" value="${esc(value)}" inputmode="decimal" required${disabled()}>${note ? `<small>${note}</small>` : ''}</label>`;
  }
  function settingsPanel() {
    const s = settingsDraft || state.settings;
    return `<section class="life-panel"><div class="life-panel-head"><div><h2>给未来，留一点缓冲。</h2><p class="life-note">设置额外压力，比较现金是否仍能覆盖生活底线。它们是情景假设，不代表发生概率。</p></div></div><form data-life-form="settings"><div class="life-fields">${numericField('illiquidAssets','不动用资产（元）',s.illiquidAssets,0,1000000000,0.01,'例如长期持有资产；不计入可支付现金。')}${numericField('goalAmount','本次希望再存入目标（元）',s.goalAmount,0,1000000,0.01,'这只是规划金额，生成转账后仍需逐笔确认。')}${numericField('delayDays','工资额外延迟（天）',s.delayDays,0,30,1,'在账本已有延迟上，再增加这些天。')}${numericField('expenseShock','额外维修冲击（元）',s.expenseShock,0,1000000,0.01,'独立压力假设，未新增真实账单。')}${numericField('shockDay','维修冲击出现日（第几天）',s.shockDay,1,30,1,'按未来 30 天计算。')}</div><p class="life-note">现金底线：${money((state.snapshot.profile || {}).reserve)} · 可在「我的画像」中修改。</p><div class="life-actions"><button class="life-button life-button-primary" type="submit"${disabled()}>保存生活设置</button><span class="life-note" data-life-dirty>${dirtySettings ? '有未保存的设置' : '设置已同步'}</span></div></form></section>`;
  }
  function selectedTemplate() {
    const items = (state.catalogue || []).filter(x => x.category === category);
    return items.find(x => x.id === templateId) || items[0] || {};
  }
  function scenePanel() {
    const base = selectedTemplate(), t = {...base,...(sceneDraft || {} )}, options = (state.catalogue || []).filter(x => x.category === category);
    templateId = t.id || '';
    return `<section class="life-panel"><div class="life-panel-head"><div><h2>最近，你想安排什么？</h2><p class="life-note">选择生活场景，再改成自己的金额与时间。</p></div></div><div class="life-category-grid" role="group" aria-label="生活场景类别">${Object.entries(categories).map(([id,label]) => `<button class="life-category" type="button" data-life-category="${id}" aria-pressed="${category === id}"${disabled()}>${label}</button>`).join('')}</div><label class="life-field">场景参考<select data-life-template${disabled()}>${options.map(x => `<option value="${esc(x.id)}" ${t.id === x.id ? 'selected' : ''}>${esc(x.title)}</option>`).join('')}</select></label><p class="life-note">${esc(t.description || '为一笔额外开支设置金额与时间。')}</p><form data-life-form="scene"><div class="life-fields"><label class="life-field life-field-wide">事情的名字<input name="title" value="${esc(t.title || '')}" required maxlength="60" autocomplete="off"${disabled()}></label><label class="life-field">类别<select name="category"${disabled()}>${Object.entries(categories).map(([id,label]) => `<option value="${id}" ${id === (t.category || category) ? 'selected' : ''}>${label}</option>`).join('')}</select></label>${numericField('amount','预计金额（元）',t.amount || 500,0.01,1000000,0.01)}<div class="life-dayfields">${numericField('earliestDay','最早第几天',t.earliestDay || 1,1,30,1)}${numericField('preferredDay','希望第几天',t.preferredDay || 7,1,30,1)}${numericField('latestDay','最晚第几天',t.latestDay || 30,1,30,1)}</div><label class="life-field life-field-wide">这件事有多重要<select name="importance"${disabled()}>${Object.entries(priorities).map(([id,label]) => `<option value="${id}" ${t.importance === id ? 'selected' : ''}>${label}</option>`).join('')}</select></label></div><label class="life-check"><input type="checkbox" name="allowSkip" ${sceneDraft && sceneDraft.allowSkip ? 'checked' : ''}${disabled()}><span>现金紧张时，允许建议这次先不安排。</span></label><label class="life-check"><input type="checkbox" name="allowAlternative" ${sceneDraft && sceneDraft.allowAlternative ? 'checked' : ''}${disabled()}><span>允许建议使用下方的低价替代。</span></label><div class="life-fields">${numericField('alternativeAmount','替代金额（元）',t.alternativeAmount || '',0.01,1000000,0.01)}<label class="life-field">替代方式<input name="alternativeLabel" value="${esc(t.alternativeLabel || '')}" maxlength="100" autocomplete="off" disabled></label></div><label class="life-check"><input type="checkbox" name="extraOnly" ${sceneDraft && sceneDraft.extraOnly ? 'checked' : ''} required${disabled()}><span>我确认这是额外、未登记的支出，未包含在现有账单和日常预算里。</span></label><div class="life-callout">保存后仅用于比较。只有你另行确认「纳入未来账单」，才会登记待付事项；登记仍未付款。</div><button type="submit" class="life-button life-button-primary"${disabled()}>保存为生活假设</button></form></section>`;
  }
  function scenesPanel() {
    return `<section class="life-panel"><div class="life-panel-head"><div><h2>摆在一起的生活假设</h2><p class="life-note">已登记的场景通过账单参与计算，不会再重复扣除。</p></div><span class="life-tag">${(state.scenes || []).filter(s => s.status === 'draft').length} 个待比较</span></div><ul class="life-scene-list">${(state.scenes || []).map(s => `<li class="life-scene"><div class="life-scene-head"><strong>${esc(s.title)}</strong><span class="life-money">${money(s.status==='committed' && s.committedAmount != null ? s.committedAmount : s.amount)}</span></div><p class="life-note">${esc(categories[s.category] || s.category)} · ${esc(priorities[s.importance] || s.importance)} · ${s.status==='committed' ? '已安排第 '+number(s.committedDay == null ? s.preferredDay : s.committedDay)+' 天' : '希望第 '+number(s.preferredDay)+' 天（'+number(s.earliestDay)+'–'+number(s.latestDay)+' 天）'}</p><span class="life-tag ${s.status === 'committed' ? 'life-tag-accent' : ''}">${esc(statuses[s.status] || s.status)}</span>${s.status === 'draft' ? `<div class="life-actions"><button class="life-button life-button-small" type="button" data-life-commit="${esc(s.id)}"${disabled()}>按原金额与首选日纳入账单</button><button class="life-button life-button-small life-button-danger" type="button" data-life-cancel="${esc(s.id)}"${disabled()}>取消假设</button></div>` : ''}</li>`).join('') || '<li class="life-empty">从八类生活场景中，添加第一件想安排的事。</li>'}</ul></section>`;
  }
  function plot(projection,baseline,reserve) {
    const raw=mapProjectionPoints(projection.pts), baseRaw=baseline?mapProjectionPoints(baseline.pts):[];
    const pts=raw.map(p=>p[1]), base=baseRaw.map(p=>p[1]);
    if (!pts.length) return '<p class="life-note">暂无现金路径。</p>';
    const all = pts.concat(base,[reserve,0]), low = Math.min(...all), high = Math.max(...all), span = Math.max(1,high-low), bottom = low-span*.08, top = high+span*.12;
    const W=760,H=220,L=10,R=10,T=12,B=12, x=i=>L+(W-L-R)*i/Math.max(1,pts.length-1), y=v=>T+(H-T-B)*(top-v)/(top-bottom);
    const points=values=>values.map((v,i)=>`${x(i).toFixed(2)},${y(v).toFixed(2)}`).join(' ');
    const safe=y(number(reserve)), zero=y(0);
    return `<div class="life-chart" role="img" aria-label="未来三十天现金余额路径与现金安全线"><svg viewBox="0 0 ${W} ${H}" aria-hidden="true"><line x1="${L}" x2="${W-R}" y1="${zero.toFixed(2)}" y2="${zero.toFixed(2)}" stroke="var(--line2)"/><line x1="${L}" x2="${W-R}" y1="${safe.toFixed(2)}" y2="${safe.toFixed(2)}" stroke="var(--warn)" stroke-dasharray="6 5"/>${base.length ? `<polyline points="${points(base)}" fill="none" stroke="var(--mute)" stroke-width="1.6" stroke-dasharray="3 5"/>` : ''}<polyline points="${points(pts)}" fill="none" stroke="currentColor" stroke-width="2.7" stroke-linejoin="round" stroke-linecap="round"/></svg></div><div class="life-chart-labels"><span>今天 · ${money(pts[0])}</span><span>第 ${pts.length-1} 天 · ${money(pts[pts.length-1])}</span></div><div class="life-chart-legend"><span><i></i>所选方案</span>${base.length ? '<span><i class="life-line-base"></i>原安排</span>' : ''}<span><i class="life-line-safe"></i>现金底线 ${money(reserve)}</span></div>`;
  }
  function planCards() {
    return `<div class="life-plans">${analysis.policies.map(p => { const m=p.metrics; return `<button class="life-plan" type="button" data-life-plan="${esc(p.id)}" aria-pressed="${selectedPlan === p.id}"${disabled()}><h3>${esc(p.title)}${analysis.recommendation.policyId === p.id ? '<span class="life-tag life-tag-accent">建议先看</span>' : ''}</h3><p>${esc(p.summary)}</p><dl><div><dt>最坏底线缺口</dt><dd>${money(m.worstGap)}</dd></div><div><dt>最坏现金不足</dt><dd>${money(m.worstDeficit)}</dd></div><div><dt>低于底线天数</dt><dd>${number(m.breachDays)} 天</dd></div><div><dt>安排目标储蓄</dt><dd>${money(m.goalAmount)}</dd></div><div><dt>暂缓目标储蓄</dt><dd>${money(m.deferredGoal)}</dd></div><div><dt>生活支出合计</dt><dd>${money(m.expenseTotal)}</dd></div></dl></button>`; }).join('')}</div>`;
  }
  function sensitivity() {
    const rows=analysis.sensitivity || [], max=Math.max(1,...rows.map(x=>number(x.worstGap)));
    return `<section class="life-panel"><h2>多留一些现金，会怎样？</h2><p class="life-note">假设额外增加下列金额的可支付现金，逐一计算最坏压力情景下的底线缺口。条形越短，缺口越小。</p>${rows.map(r=>`<div class="life-sensitive-row"><span>额外 ${money(r.buffer)}</span><svg viewBox="0 0 100 12" preserveAspectRatio="none" role="img" aria-label="底线缺口 ${esc(money(r.worstGap))}"><rect width="100" height="12" rx="3" fill="var(--surface2)"/><rect width="${clamp(number(r.worstGap)/max*100,0,100).toFixed(2)}" height="12" rx="3" fill="currentColor"/></svg><span>缺口 ${money(r.worstGap)}<br>${number(r.breachDays)} 天</span></div>`).join('')}<p class="life-callout">${analysis.breakpoint && analysis.breakpoint.alreadyAtRisk ? '当前基准情景已触及现金底线，需要先处理现有缺口。' : !analysis.breakpoint || analysis.breakpoint.extraDelayDays == null ? '没有待到账工资，暂不计算可承受的额外延迟天数。' : `在当前假设下，工资还可额外延迟${number(analysis.breakpoint.extraDelayDays)>=30 ? '至少 30' : '约 '+number(analysis.breakpoint.extraDelayDays)} 天，现金仍可守住底线。`} 这不是工资到账的承诺。</p><p class="life-note">${esc(analysis.breakpoint && analysis.breakpoint.basis || '仅在当前假设与未来30天内逐日检验。')}</p></section>`;
  }
  function resultPanel() {
    if (!analysis) return `<section class="life-panel"><div class="life-panel-head"><div><h2>三种安排，放在一起看。</h2><p class="life-note">对同一份账本和生活假设，比较原安排、现金缓冲优先与兼顾生活的方案。</p></div></div><button class="life-button life-button-primary" data-life-action="analyze" type="button"${disabled()}>计算并比较三种方案</button><p class="life-note">比较不会转账，也不会把生活假设自动登记为账单。</p></section>`;
    const policy=policyById(analysis,selectedPlan) || analysis.policies[0];
    const projections=policy.projections || [], projection=projectionFor(policy,selectedStress);
    const original=analysis.policies.find(p=>p.id==='original'), baseline=original && original.id!==policy.id && original.projections.find(p=>p.scenarioId === (projection && projection.scenarioId));
    const m=policy.metrics, recommended=analysis.policies.find(p=>p.id===analysis.recommendation.policyId), goal=number(policy.metrics.goalAmount), hasDraft=(state.scenes || []).some(s=>s.status==='draft');
    const mode={original:'保留原安排',alternative:'采用低价替代',skip:'这次暂不安排',skipped:'这次暂不安排',defer:'调整日期',deferred:'调整日期',delayed:'调整日期',scheduled:'安排支出',goal:'目标储蓄'};
    return `<section class="life-panel"><div class="life-panel-head"><div><h2>选一种，更适合现在的安排。</h2><p class="life-note">数字来自同一份模拟账本；每种方案都经过四个压力情景。</p></div><button class="life-button life-button-small" data-life-action="analyze" type="button"${disabled()}>重新比较</button></div><div class="life-recommendation"><strong>建议先看：${esc(recommended && recommended.title || '当前方案')}</strong><br>${esc(analysis.recommendation.rationale)}</div>${planCards()}<h3>${esc(policy.title)} · 未来现金路径</h3><div class="life-stress-tabs" role="group" aria-label="压力情景">${(analysis.stressScenarios || []).map(s=>`<button class="life-button life-button-small" type="button" data-life-stress="${esc(s.id)}" aria-pressed="${projection && projection.scenarioId===s.id}"${disabled()}>${esc(s.label)}</button>`).join('')}</div>${projection ? plot(projection,baseline,number(analysis.financial.reserve)) : '<p>暂无计算结果。</p>'}${projection ? `<dl class="life-metrics"><div><dt>最低现金 · 第 ${number(projection.minDay)} 天</dt><dd>${money(projection.min)}</dd></div><div><dt>低于底线</dt><dd>${number(projection.breachDays)} 天</dd></div><div><dt>最大底线缺口</dt><dd class="${number(projection.maxGap)>0?'life-warning':''}">${money(projection.maxGap)}</dd></div><div><dt>实际现金不足</dt><dd class="${number(projection.cashDeficit)>0?'life-warning':''}">${money(projection.cashDeficit)}</dd></div></dl>` : ''}<p class="life-note">底线缺口表示现金低于你的预留；现金不足表示余额低于零。生活调整程度 ${number(m.adjustmentScore)}，数值越小，改变越少。</p><p class="life-note">现金路径已包含所选方案拟转入的目标储蓄 ${money(policy.metrics.goalAmount)}；这笔钱尚未转账。正式转账仍需另行确认。</p><h3>生活安排与调整依据</h3><ul class="life-adjustments">${(policy.allocations || []).map(a=>`<li><div><strong>${esc(a.title)}</strong><span>${money(a.amount)} · 第 ${number(a.day)} 天</span></div><p>${esc(priorities[a.importance] || '')} · ${esc(mode[a.mode] || a.mode)}${a.changeReason ? ' · '+esc(a.changeReason) : ''}</p></li>`).join('') || '<li>本方案没有额外生活场景。</li>'}</ul>${hasDraft ? `<div class="life-callout">先确认生活安排，再生成转账草案。采用此方案会按下方调整登记未来账单；这一步未付款。</div><button class="life-button life-button-primary" type="button" data-life-action="adopt"${disabled()}>采用此安排，纳入未来账单</button>` : ''}${goal>0 ? `<div class="life-callout">所选方案安排 ${money(goal)} 存入目标。生成草案后，将进入已有的逐笔转账确认页面。${hasDraft ? '请先采用或取消所有生活假设，再重新比较。' : ''}</div><button class="life-button life-button-primary" type="button" data-life-action="goal"${hasDraft ? ' disabled' : disabled()}>生成目标转账草案 · ${money(goal)}</button>` : '<p class="life-callout">所选方案暂不新增目标转账，优先保留现金。</p>'}<details class="life-details"><summary>查看计算假设</summary><ul>${(analysis.assumptions || []).map(a=>`<li>${esc(a)}</li>`).join('')}</ul></details></section>${sensitivity()}`;
  }
  function render() {
    if (!adapter || !mount()) return;
    const changed=syncPersona(), ctx=context();
    if (!ctx.completed) { mount().innerHTML=`${header()}<section class="life-panel life-empty"><h2>先确认你的财务画像。</h2><p>确认读取模拟账本与现金底线后，再开始安排生活。</p><button class="life-button life-button-primary" type="button" data-life-action="profile">去建立我的画像</button></section>`; return; }
    if (!ctx.serverMode) { mount().innerHTML=`${header()}<section class="life-panel life-empty"><h2>通过本地服务，打开生活规划。</h2><p>生活假设与版本记录需要 HTTP 服务。请按项目说明启动服务，再打开服务提供的页面。</p></section>`; return; }
    if (!state) { mount().innerHTML=`${header()}<section class="life-panel life-empty">${loading ? '正在读取你的生活与账本…' : '请刷新，读取当前账户的生活设置。'}</section>`; if (changed && !loading) refresh(); return; }
    mount().innerHTML=`${header()}${assets()}<div class="life-columns"><div>${settingsPanel()}${scenePanel()}</div><div>${scenesPanel()}<div data-life-results>${resultPanel()}</div></div></div><p class="life-footer">生活规划使用模拟账本与明确假设。比较结果只是安排建议；场景登记、目标转账与付款都需要你分别确认。</p>`;
    const form=mount().querySelector('[data-life-form="scene"]'); if(form) { form.elements.allowSkip.disabled=form.elements.importance.value==='essential' || busy; if(form.elements.importance.value==='essential') form.elements.allowSkip.checked=false; } if(form) ['alternativeAmount','alternativeLabel'].forEach(name=>{form.elements[name].disabled=!(sceneDraft && sceneDraft.allowAlternative) || busy; form.elements[name].required=Boolean(sceneDraft && sceneDraft.allowAlternative);});
    mount().setAttribute('aria-busy',String(busy || loading));
  }
  async function loadCurrent(who,seq) {
    const result=await adapter.load();
    if (seq!==sequence || !validIdentity(result,who)) return false;
    if (!result || !Array.isArray(result.scenes) || !result.settings || !result.snapshot) throw new Error('生活数据不完整，请刷新后重试。');
    state=result; dirtySettings=false; settingsDraft=null; return true;
  }
  async function refresh() {
    if (!adapter) return;
    syncPersona(); const ctx=context(); if (!ctx.completed || !ctx.serverMode || busy) { render(); return; }
    const who=ctx.persona, seq=++sequence; loading=true; invalidate(); ++inputEpoch; render();
    try { if (await loadCurrent(who,seq)) setMessage('生活设置与当前账本已同步。',false); }
    catch(error) { if(seq===sequence && context().persona===who) setMessage(failure(error),true); }
    finally { if(seq===sequence && context().persona===who) { loading=false; render(); } }
  }
  async function mutate(operation,success) {
    if (busy || loading || !state) return;
    const who=context().persona, action=++actionSequence, seq=++sequence; invalidate(); setBusy(true); setMessage('正在保存，请稍候…',false);
    try {
      const result=await operation();
      if (action!==actionSequence || seq!==sequence || !validIdentity(result,who)) return;
      if (result && result.ok===false) throw new Error(result.error || '操作未完成。');
      if (result && result.snapshot && adapter.handleSnapshot) adapter.handleSnapshot(result.snapshot);
      if (await loadCurrent(who,seq)) { setMessage(success,false); ++inputEpoch; return true; }
    } catch(error) { if(action===actionSequence && context().persona===who) setMessage(failure(error),true); }
    finally { if(action===actionSequence && context().persona===who) { setBusy(false); render(); } }
  }
  async function compare() {
    if (busy || loading || !state) return;
    if (dirtySettings) { setMessage('请先保存生活设置，再比较方案。',true); return; }
    if(context().accountRevision != null && context().accountRevision!==accountRevision()) { invalidate(); setMessage('账本已更新，请刷新数据后重新比较。',true); render(); return; }
    const who=context().persona, lifeVersion=version(), revision=accountRevision(), epoch=inputEpoch, seq=++sequence, action=++actionSequence;
    invalidate(); setBusy(true); setMessage('正在比较现金安排与压力情景…',false);
    try {
      const result=await adapter.analyze();
      if (seq!==sequence || context().persona!==who || epoch!==inputEpoch || version()!==lifeVersion || accountRevision()!==revision || (context().accountRevision != null && context().accountRevision!==revision)) return;
      if (!result || result.version!=='METIS-LIFE-2' || result.lifeVersion!==lifeVersion || result.accountRevision!==revision || !Array.isArray(result.policies) || !result.policies.length) throw new Error('计算所用账本或假设已变化，请刷新数据后重新比较。');
      analysis=result; selectedPlan=result.recommendation.policyId; selectedStress=(result.stressScenarios[0] || {}).id || ''; setMessage('三种方案已计算完成。你可以切换压力情景，查看现金余量。',false);
    } catch(error) { if(seq===sequence && context().persona===who) setMessage(failure(error),true); }
    finally { if(action===actionSequence && context().persona===who) { setBusy(false); render(); } }
  }
  function sceneFromForm(form) {
    const f=new FormData(form), value=name=>f.get(name), allowAlternative=form.elements.allowAlternative.checked;
    return {title:String(value('title') || '').trim(),category:value('category'),amount:Number(value('amount')),earliestDay:Number(value('earliestDay')),preferredDay:Number(value('preferredDay')),latestDay:Number(value('latestDay')),importance:value('importance'),allowSkip:form.elements.allowSkip.checked,allowAlternative,alternativeAmount:allowAlternative?Number(form.elements.alternativeAmount.value):null,alternativeLabel:allowAlternative?String(form.elements.alternativeLabel.value || '').trim():''};
  }
  async function click(event) {
    const button=event.target.closest('button'); if (!button || !mount().contains(button) || busy || loading) return;
    if (button.dataset.lifeCategory) { category=button.dataset.lifeCategory; templateId=''; sceneDraft=null; ++inputEpoch; render(); focusControl('data-life-category',category); return; }
    if (button.dataset.lifePlan) { selectedPlan=button.dataset.lifePlan; render(); focusControl('data-life-plan',selectedPlan); return; }
    if (button.dataset.lifeStress) { selectedStress=button.dataset.lifeStress; render(); focusControl('data-life-stress',selectedStress); return; }
    const name=button.dataset.lifeAction;
    if(dirtySettings && (name==='adopt' || name==='goal' || button.dataset.lifeCommit || button.dataset.lifeCancel)) { setMessage('请先保存生活设置，再决定生活安排。',true); return; }
    if(name==='profile') { adapter.openProfile(); return; }
    if(name==='refresh') { refresh(); return; }
    if(name==='analyze') { compare(); return; }
    if(name==='adopt' && analysis) {
      const p=analysis.policies.find(x=>x.id===selectedPlan); if(!p) return;
      const rows=(p.allocations || []).map(a=>`${a.title}：${['skip','skipped'].includes(a.mode) ? '本次不安排' : money(a.amount)+'，第 '+number(a.day)+' 天'}${a.changeReason ? '（'+a.changeReason+'）' : ''}`);
      if(!window.confirm('采用「'+p.title+'」并按以下安排纳入未来账单？\n\n'+rows.join('\n')+'\n\n此操作只登记待付事项，未付款。未安排的假设将取消。目标储蓄仍需另行生成草案和确认。')) return;
      const v=version(), r=accountRevision(), id=p.id;
      const applied=await mutate(()=>adapter.commitPlan({version:v,accountRevision:r,policyId:id,confirmation:true,requestKey:crypto.randomUUID()}),'所选生活安排已纳入未来账单，尚未付款。');
      if(applied && state && !(state.scenes || []).some(s=>s.status==='draft') && !messageError) await compare();
      return;
    }
    if(name==='goal' && analysis) {
      if ((state.scenes || []).some(s=>s.status==='draft')) { setMessage('请先采用或取消生活假设，再重新比较。',true); return; }
      const p=analysis.policies.find(x=>x.id===selectedPlan), amount=number(p && p.metrics.goalAmount);
      if (amount<=0) return;
      // 提交这次分析所依据的版本；服务端据此拒绝分析之后发生的任何变化。
      const request={amount,lifeVersion:analysis.lifeVersion,accountRevision:analysis.accountRevision};
      const who=context().persona, action=++actionSequence; setBusy(true);
      try { const result=await adapter.proposeGoal(request); if(result && result.ok===false) throw new Error(result.error || '草案未生成。'); if(context().persona===who) setMessage('目标转账草案已交给逐笔确认页面。请核对后决定是否执行。',false); }
      catch(error) { if(context().persona===who) { if(error && error.status===409) invalidate(); setMessage(failure(error),true); } }
      finally { if(action===actionSequence && context().persona===who) { setBusy(false); render(); } }
      return;
    }
    const id=button.dataset.lifeCommit || button.dataset.lifeCancel;
    const scene=state && state.scenes.find(s=>s.id===id);
    if (!scene || scene.status!=='draft') return;
    if(button.dataset.lifeCommit) {
      if(!window.confirm(`将「${scene.title}」${money(scene.amount)} 纳入未来账单？这只是登记待付事项，未付款，也不会立即转账。`)) return;
      const v=version(), r=accountRevision(); mutate(()=>adapter.commitScene(id,{version:v,accountRevision:r,confirmation:true}),'已纳入未来账单，尚未付款。现金路径将计入这笔待付事项。');
    } else {
      if(!window.confirm(`取消「${scene.title}」这个生活假设？取消后将不再参与方案比较。`)) return;
      const v=version(); mutate(()=>adapter.cancelScene(id,{version:v,confirmation:true}),'生活假设已取消。');
    }
  }
  function submit(event) {
    const form=event.target.closest('[data-life-form]'); if(!form) return; event.preventDefault(); if(busy || loading || !form.reportValidity()) return;
    if(form.dataset.lifeForm==='settings') {
      const settings={}; ['illiquidAssets','delayDays','expenseShock','shockDay','goalAmount'].forEach(k=>{settings[k]=Number(form.elements[k].value);});
      const v=version(); mutate(()=>adapter.saveSettings({version:v,settings}),'生活设置已保存，可以重新比较方案。');
    } else {
      if(dirtySettings) { setMessage('请先保存生活设置，再添加场景。',true); return; }
      const scene=sceneFromForm(form);
      if (!scene.title) { setMessage('请为这件事填写名字。',true); return; }
      if(scene.earliestDay>scene.preferredDay || scene.preferredDay>scene.latestDay) { setMessage('请按最早、希望、最晚的顺序设置日期。',true); return; }
      if(scene.allowAlternative && (!scene.alternativeLabel || scene.alternativeAmount>=scene.amount)) { setMessage('请填写替代方式，并把替代金额设为低于原金额。',true); return; }
      const v=version(); mutate(async()=>{const result=await adapter.addScene({version:v,scene}); sceneDraft=null; return result;},'已保存为生活假设，尚未登记为未来账单。');
    }
  }
  function input(event) {
    if(!event.target.matches('input,select')) return;
    ++inputEpoch;
    if(event.target.closest('[data-life-form="settings"]')) { dirtySettings=true; settingsDraft={}; ['illiquidAssets','delayDays','expenseShock','shockDay','goalAmount'].forEach(k=>{settingsDraft[k]=event.target.form.elements[k].value;}); invalidate(); const el=mount().querySelector('[data-life-dirty]'); if(el) el.textContent='有未保存的设置，请保存后比较'; const results=mount().querySelector('[data-life-results]'); if(results) results.innerHTML=resultPanel(); }
    if(event.target.closest('[data-life-form="scene"]')) { const form=event.target.form; form.elements.allowSkip.disabled=form.elements.importance.value==='essential'; if(form.elements.importance.value==='essential') form.elements.allowSkip.checked=false; sceneDraft={...sceneFromForm(form),extraOnly:form.elements.extraOnly.checked}; }
    if(event.target.name==='allowAlternative') {
      const form=event.target.form, on=event.target.checked;
      ['alternativeAmount','alternativeLabel'].forEach(name=>{form.elements[name].disabled=!on;form.elements[name].required=on;});
    }
  }
  function init(value) {
    if(adapter) return; adapter=value; const root=mount(); if(!root) return;
    root.addEventListener('click',click); root.addEventListener('submit',submit); root.addEventListener('input',input);
    root.addEventListener('change',event=>{ if(event.target.matches('[data-life-template]')) { templateId=event.target.value; sceneDraft=null; ++inputEpoch; render(); } });
    render();
  }
  window.TroposLifeUI={init,render,refresh};
})();
