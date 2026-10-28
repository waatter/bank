/* TROPOS · 由用户建立、随账本更新的我的情况。数据与持久化由 adapter 管理。 */
(function () {
  'use strict';
  let adapter, screen = null, step = 0, draft = {}, editing = false, restoreFocus = null;
  let inertElements = [], busy = false, clarificationRequested = false, previewRisk = null;
  const goals = { protect: '保障必要生活支出', recover: '减轻还款压力', accumulate: '完成储蓄目标', grow: '规划中长期资金' };
  const stability = { stable: '收入比较稳定', mixed: '固定收入 + 浮动收入', uncertain: '收入时间和金额不固定' };
  const styles = { A: '简洁 · 结论优先', B: '柔和 · 说明充分', C: '数据 · 细节优先' };
  const assessmentFields = {
    horizon: { label: '这笔目标资金预计什么时候要用？', choices: { short: '1 年以内（含 1 年）', medium: '1–3 年（含 3 年）', long: '超过 3 年' } },
    liquidityNeed: { label: '你需要多灵活地取用这笔钱？', choices: { high: '需要随时取用', medium: '部分资金可以锁定一段时间', low: '三年以上不动用' } },
    lossTolerance: { label: '假设这笔钱出现本金亏损，你能接受到什么程度？', choices: { none: '不接受本金亏损', small: '假设亏损 5%', medium: '假设亏损 10%', high: '假设亏损 20%' } },
    lossReaction: { label: '假设 ¥10,000 变成 ¥9,000，你倾向怎么做？', choices: { sell: '止损退出', review: '核对原因，再作决定', hold: '能够接受，继续持有' } },
    experience: { label: '你如何描述自己的相关经验？', choices: { none: '没有相关经验', basic: '有一些基础经验', experienced: '有较多经验' } },
    knowledge: { label: '非保本产品是否可能亏损本金？', choices: { possible: '可能', impossible: '不可能', unsure: '不确定' } }
  };
  const clarifications = { safety: '以安全为先，按较低意愿理解我的安排', confirmed: '我已核对，这两项确实是我的选择，保留说明供复核' };
  const roles = { review: '待完善/复核', cash: '现金修复', buffer: '弹性缓冲', debt: '负债减压', goal: '近期目标', steady: '稳健积累', growth: '长期规划' };
  function conflictKey(a = {}) {
    return ((['medium', 'high'].includes(a.lossTolerance) && a.lossReaction === 'sell') || (a.lossTolerance === 'none' && a.lossReaction === 'hold')) ? `${a.lossTolerance}:${a.lossReaction}` : '';
  }
  function validAssessment(a) {
    return Boolean(a && Object.entries(assessmentFields).every(([key, field]) => Object.hasOwn(field.choices, a[key])) && (!conflictKey(a) || Object.hasOwn(clarifications, a.clarification)));
  }
  function profileComplete(d = data()) {
    return d.riskProfile && typeof d.riskProfile.complete === 'boolean' ? d.riskProfile.complete : validAssessment((d.profile || {}).assessment);
  }
  const esc = value => String(value == null ? '' : value).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const money = value => '¥' + (Number.isFinite(Number(value)) ? Number(value) : 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
  const data = () => adapter.getData();
  const gate = () => document.getElementById('profileGate');
  const mount = () => document.getElementById('profileMount');
  const stamp = value => { const date = new Date(value); return value && !Number.isNaN(date.getTime()) ? date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '本次体验'; };
  function focusHeading() { const title = gate().querySelector('[data-pu-title]'); if (title) title.focus(); }
  function setModal(on) {
    const root = gate();
    root.hidden = !on;
    document.body.classList.toggle('profile-gated', on);
    if (on && !inertElements.length) {
      let branch = root;
      while (branch && branch !== document.body) {
        Array.from(branch.parentElement.children).forEach(el => {
          if (el !== branch && !['SCRIPT', 'STYLE', 'LINK'].includes(el.tagName)) {
            inertElements.push([el, el.inert]); el.inert = true;
          }
        });
        branch = branch.parentElement;
      }
    }
    if (!on) { inertElements.forEach(([el, before]) => { el.inert = before; }); inertElements = []; }
  }
  function frame(inner, eyebrow) {
    gate().innerHTML = `<div class="pu-overlay" role="dialog" aria-modal="true" aria-labelledby="pu-title"><div class="pu-atmosphere" aria-hidden="true"><i></i><i></i><i></i></div><div class="pu-shell"><header class="pu-brand"><div class="pu-brand-lockup"><img src="assets/tropos-mark.svg" alt=""><span>TROPOS</span></div><small>看清变化，再作安排</small></header><div class="pu-card"><p class="pu-eyebrow">${eyebrow}</p>${inner}</div><p class="pu-footer">产品演示环境 · 账户与账本均为合成数据</p></div></div>`;
    setModal(true); focusHeading();
  }
  function error(message) {
    const el = gate().querySelector('[data-pu-error]');
    if (el) { el.textContent = message || '暂时未完成，请再试一次。'; el.hidden = false; el.focus(); }
  }
  function close() {
    screen = null; setModal(false); gate().innerHTML = ''; render();
    if (restoreFocus && document.contains(restoreFocus)) restoreFocus.focus();
    else if (editing) { const button = mount().querySelector('[data-pu-page="edit"]'); if (button && button.getClientRects().length) button.focus(); }
  }
  function login() {
    const d = data();
    frame(`<h1 id="pu-title" data-pu-title tabindex="-1">先说说你想做什么，也想留多少钱在手边。</h1><p class="pu-intro">从你在意的事开始。工资或账单有变化时，我们会重新算一遍，并告诉你为什么。</p><div class="pu-accounts" role="group" aria-label="选择模拟账户">${(d.accounts || []).map((account, i) => `<button type="button" class="pu-account ${account.id === d.selectedId ? 'is-selected' : ''}" data-pu-account="${esc(account.id)}" aria-pressed="${account.id === d.selectedId}"><span class="pu-window pu-window-${['A','B','C'][i % 3]}" aria-hidden="true"></span><strong>${esc(account.name)}</strong><small>${esc(account.description)}</small><span class="pu-selected">${account.id === d.selectedId ? '已选择' : '选择此账户'}</span></button>`).join('')}</div><p class="pu-note">A/B/C 仅是模拟账本身份，不决定风险角色。无需输入密码、手机号或银行卡信息。</p><p class="pu-error" data-pu-error tabindex="-1" role="alert" hidden></p><button class="btn solid pu-primary" type="button" data-pu="continue">${d.completed && profileComplete(d) ? '返回我的首页' : d.completed ? '补全风险与用钱期限' : '填写我的情况'} <span aria-hidden="true">→</span></button>${d.completed && profileComplete(d) ? '<button class="btn pu-login-back" type="button" data-pu="cancel">取消</button>' : ''}`, '选择演示账户');
  }
  function defaultDraft() {
    const d = data(), p = d.profile || {};
    const assessment = {};
    Object.entries(assessmentFields).forEach(([key, field]) => {
      if (p.assessment && Object.hasOwn(field.choices, p.assessment[key])) assessment[key] = p.assessment[key];
    });
    if (conflictKey(assessment) && Object.hasOwn(clarifications, (p.assessment || {}).clarification)) assessment.clarification = p.assessment.clarification;
    return { goalType: p.goalType || 'protect', goalName: p.goalName || '', target: p.target == null ? '' : p.target, reserve: p.reserve == null ? '' : p.reserve, incomeStability: p.incomeStability || 'stable', style: p.style || 'A', automation: p.automation || 'confirm', readConsent: Boolean(d.completed && p.readConsent), assessment };
  }
  function options(items, name, selected) {
    return Object.entries(items).map(([value, label]) => `<label class="pu-choice"><input type="radio" name="${name}" value="${value}" ${selected === value ? 'checked' : ''} required><span>${label}</span></label>`).join('');
  }
  function factsBlock() {
    const f = data().facts || {};
    return `<aside class="pu-ledger"><p class="pu-eyebrow">你的模拟账本</p><dl><div><dt>当前余额</dt><dd>${money(f.balance)}</dd></div><div><dt>每月预计收入</dt><dd>${money(f.income)}</dd></div><div><dt>已知待付账单</dt><dd>${money(f.billTotal)}</dd></div></dl><p>以上来自当前演示账户。这里只询问你的生活选择。</p></aside>`;
  }
  function riskQuestions() {
    const a = draft.assessment || {};
    const conflict = conflictKey(a);
    const clarification = conflict && (clarificationRequested || a.clarification) ? `<fieldset class="pu-clarification" data-pu-clarification><legend>这两项选择需要你再核对一次</legend><p>${a.lossTolerance === 'none' ? '你选择了“不接受本金亏损”，也选择了亏损后继续持有。' : '你选择了可以接受 10% 或 20% 的假设亏损，也选择了发生亏损时止损退出。'}</p><p>请确认应该如何理解；确认不会提高风险档位或新增操作授权。</p><div class="pu-choice-list">${options(clarifications, 'assessment.clarification', a.clarification)}</div></fieldset>` : '';
    return `<p class="pu-note pu-assessment-note">每题请主动选择。这些是你的个人声明，未经量表验证；5%、10%、20% 仅为亏损假设情景，不是发生概率或收益承诺。</p><div class="pu-question-grid">${Object.entries(assessmentFields).map(([key, field]) => `<fieldset><legend>${field.label}</legend><div class="pu-choice-list">${options(field.choices, `assessment.${key}`, a[key])}</div></fieldset>`).join('')}</div>${clarification}<p class="pu-note">收益目标不会倒推更高的风险意愿。用钱期限、流动性与当前资金承受能力会限制规划范围；没有经验或对本金亏损不确定时，会先解释并限制到简单规划。</p>`;
  }
  function setup() {
    const titles = ['你现在最想完成什么？', '你想留多少钱在手边？', '这笔钱何时要用，你能接受什么变化？', '选一种你看着舒服的页面和提醒方式。'];
    const descriptions = ['这是你想做成的事，之后随时可以改。', '这笔钱是你想留在手边的最低金额。', '请按自己的实际想法回答，不需要为了某个目标选择更高风险。', '三种风格仅改变信息呈现，不代表风险档级。读取同意与每笔操作授权分别确认。'];
    let body = '';
    if (step === 0) body = `<fieldset><legend>当前最重要的目标</legend><div class="pu-choice-grid">${options(goals, 'goalType', draft.goalType)}</div></fieldset><div class="pu-fields"><label>给目标起个名字<input name="goalName" maxlength="50" value="${esc(draft.goalName)}" placeholder="例如：安心过完这个月" required autocomplete="off"></label><label>目标金额（元）<input name="target" type="number" min="1" max="100000000" step="1" inputmode="numeric" value="${esc(draft.target)}" required><small>填写本次希望达到的目标金额，至少 ¥1。</small></label></div>`;
    if (step === 1) body = `<div class="pu-step-columns"><div><label class="pu-field">手边至少留多少钱（元）<input name="reserve" type="number" min="0" max="100000000" step="1" inputmode="numeric" value="${esc(draft.reserve)}" required><small>给房租、还款等日常开支留的钱。余额接近这笔钱时，系统会先提醒你。</small></label><fieldset><legend>你的收入状态</legend><div class="pu-choice-list">${options(stability, 'incomeStability', draft.incomeStability)}</div></fieldset></div>${factsBlock()}</div>`;
    if (step === 2) body = riskQuestions();
    if (step === 3) body = `<fieldset><legend>你喜欢哪种页面风格</legend><div class="pu-style-grid">${Object.entries(styles).map(([value,label]) => `<label class="pu-style"><input type="radio" name="style" value="${value}" ${draft.style === value ? 'checked' : ''} required><span class="pu-window pu-window-${value}" aria-hidden="true"></span><span>${label}</span></label>`).join('')}</div></fieldset><fieldset><legend>你希望怎么收到安排建议？</legend><div class="pu-choice-grid">${options({advice:'只提供建议，由我操作',confirm:'准备方案，每次由我确认'}, 'automation', draft.automation)}</div><p class="pu-note">这只是提醒方式；转账前仍要你亲自确认。</p></fieldset><label class="pu-consent"><input type="checkbox" name="readConsent" ${draft.readConsent ? 'checked' : ''} required><span>我同意读取当前账户的<strong>模拟账本</strong>，用于计算现金流和更新我的情况。<small>不连接真实银行，也不自动授权转账；想做什么由我决定。</small></span></label>`;
    frame(`<ol class="pu-progress" aria-label="画像建立进度">${['目标','现金与收入','风险与用钱期限','表达与读取同意'].map((label,i) => `<li class="${i <= step ? 'is-current' : ''}" ${i === step ? 'aria-current="step"' : ''}><span>${i + 1}</span>${label}</li>`).join('')}</ol><h1 id="pu-title" data-pu-title tabindex="-1">${titles[step]}</h1><p class="pu-intro">${descriptions[step]}</p><form id="pu-form">${body}<p class="pu-error" data-pu-error role="alert" tabindex="-1" hidden></p><div class="pu-form-actions"><button type="button" class="btn" data-pu="back">${step ? '上一步' : editing ? '取消修改' : '返回选择账户'}</button>${step !== 2 ? '<button type="button" class="btn link" data-pu="sample">载入当前账户样例</button>' : ''}<button type="submit" class="btn solid">${step === 3 ? '看看安排预览' : '继续'} <span aria-hidden="true">→</span></button></div></form>`, `说说你的情况 · ${esc(data().accountName)} · ${editing ? '编辑' : data().completed ? '补全问卷' : '初次设置'}`);
  }
  function collect() {
    const form = gate().querySelector('form'); if (!form) return;
    const previous = draft.assessment || {}, next = { ...previous };
    new FormData(form).forEach((value, name) => {
      if (name.startsWith('assessment.')) next[name.slice(11)] = value;
      else draft[name] = value;
    });
    if (!conflictKey(next) || previous.lossTolerance !== next.lossTolerance || previous.lossReaction !== next.lossReaction) {
      delete next.clarification; clarificationRequested = false;
    }
    draft.assessment = next;
    if (step === 3) draft.readConsent = form.elements.readConsent.checked;
    previewRisk = null;
  }
  function summaryCards(p) {
    return `<dl class="pu-summary"><div><dt>当前目标</dt><dd>${esc(p.goalName || goals[p.goalType] || '尚未设置')}</dd><small>${esc(goals[p.goalType] || '')} · ${money(p.target)}</small></div><div><dt>最低现金预留</dt><dd>${money(p.reserve)}</dd><small>${esc(stability[p.incomeStability] || '尚未设置')}</small></div><div><dt>信息呈现偏好</dt><dd>${esc(styles[p.style] || styles.A)}</dd><small>${p.automation === 'advice' ? '仅提供建议，由你操作' : '每项操作由你确认'}</small></div></dl>`;
  }
  function riskResult(result, assessment) {
    const r = result || {}, role = r.role || {}, band = r.riskBand || {};
    const roleKey = Object.hasOwn(roles, role.key) ? role.key : 'review';
    const level = Number.isInteger(band.level) && band.level >= 0 && band.level <= 5 ? band.level : 0;
    const list = value => Array.isArray(value) ? value : [];
    const conflict = conflictKey(assessment || {});
    return `<section class="pu-risk-result" aria-label="画像结果与复核依据"><div class="pu-role-heading"><div><p class="pu-eyebrow">${esc(r.version || 'ETHOS-R2')} · ${r.complete === true ? '问卷已完整' : '待补全问卷'}${r.reviewRequired ? ' · 需要复核' : ''}</p><h2>${esc(role.name || roles[roleKey])}</h2><p class="pu-role-headline">${esc(role.headline || '先补充并核对你的选择，再作安排。')}</p></div><div class="pu-risk-band"><span>风险意愿与约束</span><strong>${level === 0 ? '0 · 未评' : `${level} / 5 · ${esc(band.label || '规划参考档位')}`}</strong><p>${esc(band.detail || '尚无完整评估结果，不推定风险答案。')}</p></div></div><p class="pu-note">${esc(role.description || '问卷答案由你声明，结果由当前模拟账本与原型规则共同生成。')}</p><ul class="pu-role-list" aria-label="七种安排角色">${Object.entries(roles).map(([key, name]) => `<li ${key === roleKey ? 'class="is-current" aria-current="true"' : ''}>${esc(name)}${key === roleKey ? '<span class="pu-role-current"> · 当前</span>' : ''}</li>`).join('')}</ul><dl class="pu-dimensions">${list(r.dimensions).map(item => `<div><dt>${esc(item.label)}</dt><dd>${esc(item.value)}</dd><p>${esc(item.detail)}</p></div>`).join('') || '<div><dt>个人声明</dt><dd>等待完整评估</dd><p>旧画像未填写的风险问题不会自动补答。</p></div>'}</dl><div class="pu-risk-explanation"><div><h3>为什么分到这里</h3><ul>${list(r.reasons).map(reason => `<li>${esc(reason)}</li>`).join('') || '<li>等待问卷与账本的完整计算结果。</li>'}</ul></div><div><h3>需要核对的地方</h3><ul><li>${r.reviewRequired ? '请核对尚未明确的答案和账本数据。' : '如有尚未登记的支出，请补充后再作安排。'}</li><li>预计收入只有实际到账后，才计入今天可支付的余额。</li><li>需要办理转账时，另行核对收款人、金额并确认。</li></ul></div></div>${conflict ? `<aside class="pu-clarification-summary"><strong>矛盾选择复核</strong><p>${esc(assessmentFields.lossTolerance.choices[assessment.lossTolerance])} · ${esc(assessmentFields.lossReaction.choices[assessment.lossReaction])}</p><p>你的澄清：${esc(clarifications[assessment.clarification] || '尚未选择，请返回风险步骤核对。')}</p></aside>` : ''}${assessment && (assessment.knowledge !== 'possible' || assessment.experience === 'none') ? '<p class="pu-note pu-assessment-note">先了解再安排：非保本产品可能亏损本金。没有经验、回答错误或不确定时，当前仅提供解释和简单规划，不推荐具体产品。</p>' : ''}<details class="pu-risk-source"><summary>计算规则、来源与适用范围</summary><ul>${list(r.checks).map(check => `<li>${esc(check)}</li>`).join('')}</ul><p>期限、流动性、亏损意愿、应对方式、经验与知识回答均是用户声明，未经验证量表测量，不表示概率、授信评级或产品适当性结论。资金承受能力来自模拟账本；角色按当前资金与目标分流。</p><p>涉及缺口、债务压力、收入与缓冲的阈值均为本原型设计。用钱期限、流动性与承受能力只作上限；不接受本金亏损时为第 1 档，收益目标不会推高档位。</p><p>A/B/C 是模拟账本身份，不决定风险角色；三种视觉风格只影响信息呈现。所有角色均不新增金融操作授权，资金操作仍由 ARGOS 检查并逐笔确认。</p></details></section>`;
  }
  async function preview() {
    if (busy) return;
    busy = true;
    const form = gate().querySelector('form');
    if (form) form.setAttribute('aria-busy', 'true');
    const controls = Array.from(gate().querySelectorAll('button,input')).map(el => [el, el.disabled]);
    controls.forEach(([el]) => { el.disabled = true; });
    const trigger = form ? form.querySelector('[type="submit"]') : gate().querySelector('[data-pu="retry-preview"]');
    const triggerText = trigger && trigger.textContent;
    if (trigger) trigger.textContent = '正在生成预览…';
    try {
      const estimate = adapter.previewProfile ? await adapter.previewProfile({ ...draft, assessment: { ...draft.assessment } }) : null;
      previewRisk = estimate && estimate.riskProfile;
      const policy = estimate ? `<div class="pu-ledger"><strong>照你说的，目前会这样安排</strong><p>会先照顾：${esc(previewRisk?.role?.name || '待核对当前情况')}。按这份模拟账本估算，未来一个月手头最少会有 ${money(estimate.min)}；相对你想留的钱，${Number(estimate.free) < 0 ? '还差' : '余量为'} ${money(Math.abs(Number(estimate.free)))}。</p><p>这只是按目前账本算出的参考；每次转账都会再请你确认。</p></div>` : '';
      screen = 'preview';
      frame(`<h1 id="pu-title" data-pu-title tabindex="-1">看看这份安排合不合你心意。</h1><p class="pu-intro">目标与想留的钱由你设定；动态现金状态会随模拟账本更新。</p>${summaryCards(draft)}${riskResult(previewRisk, draft.assessment)}${policy}<div class="pu-promise"><strong>工资或账单变了，我们会重新算；最后怎么安排仍由你决定。</strong><p>工资晚到或多了一笔账单时，我们会更新手头余额和安排，并记下原因。你的目标和操作选择不会因此被擅自改掉。</p></div>${!previewRisk ? '<p class="pu-note" role="status">评估结果暂未就绪，请重试预览。</p>' : ''}<p class="pu-error" data-pu-error role="alert" tabindex="-1" hidden></p><div class="pu-form-actions"><button type="button" class="btn" data-pu="back-preview">返回修改</button>${!previewRisk ? '<button type="button" class="btn" data-pu="retry-preview">重试预览</button>' : ''}<button type="button" class="btn solid" data-pu="save" ${previewRisk && previewRisk.complete === true && validAssessment(draft.assessment) ? '' : 'disabled'}>确认后进入我的首页 <span aria-hidden="true">→</span></button></div>`, '确认我的情况');
    } catch (err) { error('画像预览暂未完成，请重试。'); }
    finally {
      busy = false;
      controls.forEach(([el, wasDisabled]) => { if (document.contains(el)) el.disabled = wasDisabled; });
      if (trigger && document.contains(trigger)) trigger.textContent = triggerText;
      if (form && document.contains(form)) form.removeAttribute('aria-busy');
    }
  }
  function describe(value) {
    if (value == null || value === '') return '—';
    if (typeof value !== 'object') return String(value);
    const labels = { min:'30 天最低余额', free:'可安排资金', mode:'策略', incomeStatus:'收入状态', reserve:'想留的钱', goalName:'目标', version:'版本' };
    return Object.entries(value).filter(([,v]) => typeof v !== 'object').slice(0,6).map(([key,v]) => `${labels[key] || key}：${['min','free','reserve'].includes(key) ? money(v) : v}`).join('；') || '已重新计算';
  }
  function profilePage() {
    const root = mount(); if (!root) return;
    const d = data(), p = d.profile || {}, s = d.snapshot || {}, priorities = s.priorities || [];
    const updates = (d.updates || []).slice().sort((a, b) => (new Date(b.at).getTime() || 0) - (new Date(a.at).getTime() || 0));
    root.innerHTML = `<header class="page-title pu-page-heading"><div><p class="pu-eyebrow">我的情况 · 账户视图</p><h1>你想做的事，以及现在手头的钱。</h1><p class="lead">${esc(d.accountName)} · 根据你告诉我们的情况和这份模拟账本整理。</p></div><button type="button" class="btn" data-pu-page="edit">修改我的情况</button></header><section class="panel pu-profile-section">${riskResult(d.riskProfile, p.assessment)}<div class="pu-arrangement-entry"><button type="button" class="btn solid" data-pu-page="workbench">${profileComplete(d) ? '进入我的安排' : '补全问卷后查看我的安排'} <span aria-hidden="true">→</span></button><button type="button" class="btn" data-pu-page="risk-edit">复核风险与用钱期限</button></div></section><section class="panel pu-profile-section"><div class="block-head"><div><h2>你想做的事和提醒方式</h2><p>工资或账单变化不会替你改这些选择。</p></div><span class="pill">${d.completed && profileComplete(d) ? '已确认' : '待完善/复核'}</span></div>${summaryCards(p)}<p class="pu-note">${p.readConsent ? '已同意读取模拟账本' : '尚未同意读取模拟账本'} · 每笔模拟转账仍需单独确认</p></section><div class="pu-dashboard-grid"><section class="panel pu-profile-section"><div class="block-head"><div><h2>现在手头的钱</h2><p>最近更新 ${esc(stamp(s.updatedAt))} · 版本 ${esc(s.version == null ? '—' : s.version)}</p></div><span class="pill">${esc(s.mode || '等待计算')}</span></div><dl class="pu-metrics"><div><dt>未来 30 天最低预测余额</dt><dd class="${Number(s.min) < Number(p.reserve) ? 'warn-t' : ''}">${money(s.min)}</dd></div><div><dt>当前可安排资金</dt><dd>${money(s.free)}</dd></div></dl><p class="pu-note">收入状态：${esc(s.incomeStatus || '依据当前模拟账本')}</p><p class="pu-note">预测依据为演示账本与既定支出假设；未登记的实际支出不会包含在内。</p></section><section class="panel pu-profile-section"><div class="block-head"><div><h2>为什么这样安排</h2><p>比例表示目前先照顾哪些事，不是成功概率。</p></div></div><div class="pu-priorities">${priorities.map(item => `<div class="pu-priority"><div><strong>${esc(item.name)}</strong><span>${esc(item.value)}%</span></div><meter min="0" max="100" value="${Math.min(100,Math.max(0,Number(item.value)||0))}" aria-label="${esc(item.name)}策略侧重">${esc(item.value)}%</meter><ul>${(item.reasons || []).map(reason => `<li>${esc(reason)}</li>`).join('')}</ul></div>`).join('') || '<p class="pu-note">填写情况后显示计算依据。</p>'}</div></section></div><section class="panel pu-profile-section"><div class="block-head"><div><h2>情况变了，会重新算</h2><p>试试工资晚到或多一笔账单，看看手头的钱和安排会怎么变。</p></div><span class="pill">模拟事件</span></div><div class="pu-events"><button class="pu-event" type="button" data-pu-event="income_delayed"><span aria-hidden="true">↘</span><strong>工资晚几天到账</strong><small>重新看看手头的钱和原来的安排</small></button><button class="pu-event" type="button" data-pu-event="bill_added"><span aria-hidden="true">＋</span><strong>新增维修支出</strong><small>计入一笔 ¥1,200 的模拟账单</small></button><button class="pu-event" type="button" data-pu-event="salary_received"><span aria-hidden="true">↗</span><strong>模拟收入到账</strong><small>把这笔模拟收入记到账上</small></button></div><p id="pu-page-status" class="pu-note" role="status" aria-live="polite"></p></section><section class="panel pu-profile-section"><div class="block-head"><div><h2>情况变化记录</h2><p>看看什么时候变了、为什么变。</p></div><button type="button" class="btn sm" data-pu-page="reset">重置当前演示</button></div><ol class="pu-updates">${updates.map(update => `<li><div class="pu-update-head"><strong>${esc(update.title)}</strong><time>${esc(stamp(update.at))}</time></div><span class="pu-update-source">来自：${esc(update.source || '模拟账本')}</span><div class="pu-before-after"><p><small>变化前</small>${esc(describe(update.before))}</p><span aria-hidden="true">→</span><p><small>变化后</small>${esc(describe(update.after))}</p></div><p class="pu-update-reason">${esc(update.reason)}</p></li>`).join('') || '<li class="pu-note">暂无变更记录。填写情况或触发模拟事件后，系统会记录更新依据。</li>'}</ol></section><p class="pu-boundary">动态更新现金状态与策略建议；长期目标、想留的钱和操作权限仍由你修改。</p>`;
  }
  function render() {
    if (!adapter) return;
    profilePage();
    if (!screen && !data().completed) { screen = 'login'; login(); }
    else if (!screen && !profileComplete()) { startSetup(2, true); }
    else if (!screen) setModal(false);
  }
  async function action(name) {
    if (busy) return;
    if (name === 'continue') {
      if (data().completed && profileComplete()) { close(); if (adapter.enterWorkbench) adapter.enterWorkbench(); }
      else startSetup(data().completed ? 2 : 0, true);
    } else if (name === 'cancel') { if (data().completed && profileComplete()) close(); }
    else if (name === 'back') {
      collect();
      if (step > 0) { step--; setup(); }
      else if (editing) close(); else { screen = 'login'; login(); }
    } else if (name === 'back-preview') { screen = 'setup'; step = 3; previewRisk = null; setup(); }
    else if (name === 'retry-preview') { await preview(); }
    else if (name === 'sample') {
      collect();
      const sample = defaultDraft(), f = data().facts || {};
      if (step === 0) Object.assign(draft, { goalType: sample.goalType, goalName: sample.goalName || '安心过完这个月', target: sample.target || 10000 });
      if (step === 1) Object.assign(draft, { reserve: sample.reserve === '' ? Math.round((Number(f.daily) || 100) * 30) : sample.reserve, incomeStability: sample.incomeStability });
      if (step === 3) Object.assign(draft, { style: sample.style, automation: sample.automation });
      setup();
    } else if (name === 'save') {
      if (!validAssessment(draft.assessment) || !previewRisk || previewRisk.complete !== true) { error('请补全风险与用钱期限，并取得完整的预览结果。'); return; }
      if (!draft.readConsent) { error('请返回上一步，主动勾选模拟账本读取同意。'); return; }
      busy = true;
      const button = gate().querySelector('[data-pu="save"]'); if (button) { button.disabled = true; button.textContent = '正在生成画像…'; }
      try {
        const result = await adapter.saveProfile({ ...draft, assessment: { ...draft.assessment }, target: Number(draft.target), reserve: Number(draft.reserve), goalName: String(draft.goalName).trim() });
        if (result && result.ok === false) error(result.error);
        else if (!profileComplete()) error('已保存的画像尚未完整，请返回核对风险问卷后重试。');
        else { close(); if (adapter.enterWorkbench) adapter.enterWorkbench(); }
      } catch (err) { error('画像暂未保存，请重试。'); }
      finally { busy = false; if (button && document.contains(button)) { button.disabled = false; button.textContent = '确认后进入我的首页 →'; } }
    }
  }
  function init(value) {
    if (adapter) return;
    adapter = value;
    gate().addEventListener('click', async event => {
      const account = event.target.closest('[data-pu-account]');
      if (account && !busy) { busy=true;try{await adapter.selectAccount(account.dataset.puAccount);login();}catch(err){error('模拟账户连接失败，请重试。')}finally{busy=false;}return; }
      const button = event.target.closest('[data-pu]'); if (button) action(button.dataset.pu);
    });
    gate().addEventListener('submit', event => {
      if (event.target.id !== 'pu-form') return; event.preventDefault();
      if (busy) return;
      if (!event.target.reportValidity()) return;
      collect();
      if (!String(draft.goalName || '').trim()) { error('请给当前目标起一个名字。'); return; }
      if (step === 2) {
        if (conflictKey(draft.assessment) && !draft.assessment.clarification) {
          clarificationRequested = true; setup();
          error('这两项选择有差异，请在下方选择如何理解，再继续。'); return;
        }
        if (!validAssessment(draft.assessment)) { error('请主动回答每一道风险与用钱期限问题。'); return; }
      }
      if (step < 3) { step++; setup(); } else { preview(); }
    });
    gate().addEventListener('change', event => {
      if (busy || step !== 2 || !event.target.name || !event.target.name.startsWith('assessment.')) return;
      collect();
      const clarification = gate().querySelector('[data-pu-clarification]');
      if (clarification && !clarificationRequested && !draft.assessment.clarification) {
        clarification.hidden = true;
        clarification.querySelectorAll('input').forEach(input => { input.checked = false; input.required = false; });
      }
    });
    gate().addEventListener('keydown', event => {
      if (!screen) return;
      if (event.key === 'Escape' && editing && !busy) { event.preventDefault(); close(); }
      if (event.key !== 'Tab') return;
      const controls = Array.from(gate().querySelectorAll('button:not(:disabled),input:not(:disabled),select,textarea,a[href],[tabindex="0"]')).filter(el => !el.hidden && el.getClientRects().length);
      const first = controls[0], last = controls[controls.length - 1];
      if (!first) { event.preventDefault(); return; }
      if (event.shiftKey && (document.activeElement === first || !controls.includes(document.activeElement))) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    });
    mount().addEventListener('click', async event => {
      if (busy) return;
      const button = event.target.closest('button'); if (!button) return;
      if (button.dataset.puPage === 'edit') { openSetup(); return; }
      if (button.dataset.puPage === 'risk-edit') { startSetup(2); return; }
      if (button.dataset.puPage === 'workbench') {
        if (!data().completed || !profileComplete()) startSetup(2, true);
        else if (adapter.enterWorkbench) adapter.enterWorkbench();
        return;
      }
      if (button.dataset.puPage === 'reset') {
        if (!window.confirm('重置当前账户的模拟画像和事件记录？其他演示账户不受影响。')) return;
        busy = true;
        try { await adapter.reset(); screen = 'login'; login(); render(); }
        catch (err) { const status = document.getElementById('pu-page-status'); if (status) status.textContent = '重置暂未完成，请重试。'; }
        finally { busy = false; }
        return;
      }
      if (!button.dataset.puEvent) return;
      busy = true; button.disabled = true;
      const kind = button.dataset.puEvent;
      try {
        const result = await adapter.sendEvent(kind); render();
        const status = document.getElementById('pu-page-status'); if (status) status.textContent = result && result.ok === false ? result.error : '模拟账本已更新，下方记录展示此次调整的依据。';
      } catch (err) { const status = document.getElementById('pu-page-status'); if (status) status.textContent = '模拟事件暂未完成，请重试。'; }
      finally { busy = false; const next = mount().querySelector(`[data-pu-event="${kind}"]`); if (next) next.focus(); }
    });
    render();
  }
  function startSetup(initialStep = 0, required = false) {
    restoreFocus = document.activeElement;
    editing = !required && Boolean(data().completed && profileComplete());
    screen = 'setup'; step = initialStep; draft = defaultDraft();
    clarificationRequested = Boolean(draft.assessment.clarification); previewRisk = null;
    setup();
  }
  function openLogin() { if (busy) return; restoreFocus = document.activeElement; editing = false; screen = 'login'; login(); }
  function openSetup() { if (!busy) startSetup(); }
  window.TroposProfileUI = { init, render, openLogin, openSetup };
})();
