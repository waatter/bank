/* TROPOS · 由用户建立、随账本更新的财务画像。数据与持久化由 adapter 管理。 */
(function () {
  'use strict';
  let adapter, screen = null, step = 0, draft = {}, editing = false, restoreFocus = null;
  let inertElements = [], busy = false;
  const goals = { protect: '守住生活底线', recover: '缓解还款压力', accumulate: '攒下第一笔目标金', grow: '安排长期积累' };
  const stability = { stable: '收入比较稳定', mixed: '固定收入 + 浮动收入', uncertain: '收入时间和金额不固定' };
  const styles = { A: '清晨 · 安稳留白', B: '深夜 · 温柔陪伴', C: '正午 · 数字清晰' };
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
    gate().innerHTML = `<div class="pu-overlay" role="dialog" aria-modal="true" aria-labelledby="pu-title"><div class="pu-atmosphere" aria-hidden="true"><i></i><i></i><i></i></div><div class="pu-shell"><header class="pu-brand"><span>TROPOS</span><small>每一笔钱，都更懂你</small></header><div class="pu-card"><p class="pu-eyebrow">${eyebrow}</p>${inner}</div><p class="pu-footer">独立演示环境 · 所有账户与账本均为合成数据</p></div></div>`;
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
    frame(`<h1 id="pu-title" data-pu-title tabindex="-1">先认识你，再安排钱。</h1><p class="pu-intro">从生活目标开始，建立属于你的财务画像。账本变化时，TROPOS 会解释为什么需要调整。</p><div class="pu-accounts" role="group" aria-label="选择模拟账户">${(d.accounts || []).map((account, i) => `<button type="button" class="pu-account ${account.id === d.selectedId ? 'is-selected' : ''}" data-pu-account="${esc(account.id)}" aria-pressed="${account.id === d.selectedId}"><span class="pu-window pu-window-${['A','B','C'][i % 3]}" aria-hidden="true"></span><strong>${esc(account.name)}</strong><small>${esc(account.description)}</small><span class="pu-selected">${account.id === d.selectedId ? '已选择' : '选择体验'}</span></button>`).join('')}</div><p class="pu-note">使用模拟账户体验，无需输入密码、手机号或银行卡。</p><p class="pu-error" data-pu-error tabindex="-1" role="alert" hidden></p><button class="btn solid pu-primary" type="button" data-pu="continue">${d.completed ? '回到我的工作台' : '开始建立我的画像'} <span aria-hidden="true">→</span></button>${d.completed ? '<button class="btn pu-login-back" type="button" data-pu="cancel">返回工作台</button>' : ''}`, 'WELCOME / 选择一个生活切面');
  }
  function defaultDraft() {
    const d = data(), p = d.profile || {};
    return { goalType: p.goalType || 'protect', goalName: p.goalName || '', target: p.target == null ? '' : p.target, reserve: p.reserve == null ? '' : p.reserve, incomeStability: p.incomeStability || 'stable', style: p.style || 'A', automation: p.automation || 'confirm', readConsent: Boolean(d.completed && p.readConsent) };
  }
  function options(items, name, selected) {
    return Object.entries(items).map(([value, label]) => `<label class="pu-choice"><input type="radio" name="${name}" value="${value}" ${selected === value ? 'checked' : ''} required><span>${label}</span></label>`).join('');
  }
  function factsBlock() {
    const f = data().facts || {};
    return `<aside class="pu-ledger"><p class="pu-eyebrow">你的模拟账本</p><dl><div><dt>当前余额</dt><dd>${money(f.balance)}</dd></div><div><dt>预期月收入</dt><dd>${money(f.income)}</dd></div><div><dt>已知待付账单</dt><dd>${money(f.billTotal)}</dd></div></dl><p>以上来自当前演示账户。这里只询问你的生活选择。</p></aside>`;
  }
  function setup() {
    const titles = ['最近，你最想把什么安顿好？', '为生活，留出多少安心？', '选择和你相处的方式。'];
    const descriptions = ['目标由你决定，之后可以随时修改。', '这条现金底线会进入后续资金安排。', '展示风格和建议方式，都可以由你掌握。'];
    let body = '';
    if (step === 0) body = `<fieldset><legend>当前最重要的目标</legend><div class="pu-choice-grid">${options(goals, 'goalType', draft.goalType)}</div></fieldset><div class="pu-fields"><label>给目标起个名字<input name="goalName" maxlength="50" value="${esc(draft.goalName)}" placeholder="例如：安心过完这个月" required autocomplete="off"></label><label>目标金额（元）<input name="target" type="number" min="1" max="100000000" step="1" inputmode="numeric" value="${esc(draft.target)}" required><small>填写本次希望达到的目标金额，至少 ¥1。</small></label></div>`;
    if (step === 1) body = `<div class="pu-step-columns"><div><label class="pu-field">最低现金预留（元）<input name="reserve" type="number" min="0" max="100000000" step="1" inputmode="numeric" value="${esc(draft.reserve)}" required><small>用于覆盖必要生活支出。低于这条线时，策略会优先保留现金。</small></label><fieldset><legend>你的收入状态</legend><div class="pu-choice-list">${options(stability, 'incomeStability', draft.incomeStability)}</div></fieldset></div>${factsBlock()}</div>`;
    if (step === 2) body = `<fieldset><legend>你喜欢哪一扇窗？</legend><div class="pu-style-grid">${Object.entries(styles).map(([value,label]) => `<label class="pu-style"><input type="radio" name="style" value="${value}" ${draft.style === value ? 'checked' : ''} required><span class="pu-window pu-window-${value}" aria-hidden="true"></span><span>${label}</span></label>`).join('')}</div></fieldset><fieldset><legend>希望如何接收资金建议？</legend><div class="pu-choice-grid">${options({advice:'只给建议，由我操作',confirm:'准备方案，每次由我确认'}, 'automation', draft.automation)}</div><p class="pu-note">这是操作偏好。选择任何一项，都不会产生转账授权。</p></fieldset><label class="pu-consent"><input type="checkbox" name="readConsent" ${draft.readConsent ? 'checked' : ''} required><span>我同意读取当前账户的<strong>模拟账本</strong>，用于计算现金流和更新画像。<small>不连接真实银行，不自动授权转账；长期目标仍由我决定。</small></span></label>`;
    frame(`<ol class="pu-progress" aria-label="画像建立进度">${['生活目标','现金底线','相处方式'].map((label,i) => `<li class="${i <= step ? 'is-current' : ''}" ${i === step ? 'aria-current="step"' : ''}><span>${i + 1}</span>${label}</li>`).join('')}</ol><h1 id="pu-title" data-pu-title tabindex="-1">${titles[step]}</h1><p class="pu-intro">${descriptions[step]}</p><form id="pu-form">${body}<p class="pu-error" data-pu-error role="alert" tabindex="-1" hidden></p><div class="pu-form-actions"><button type="button" class="btn" data-pu="back">${step ? '上一步' : editing ? '取消修改' : '返回选择账户'}</button><button type="button" class="btn link" data-pu="sample">填入当前账户样例</button><button type="submit" class="btn solid">${step === 2 ? '生成画像预览' : '继续'} <span aria-hidden="true">→</span></button></div></form>`, `FINANCIAL DNA SCAN / ${esc(data().accountName)} · ${editing ? '编辑我的画像' : '初次相识'}`);
  }
  function collect() {
    const form = gate().querySelector('form'); if (!form) return;
    new FormData(form).forEach((value, name) => { draft[name] = value; });
    if (step === 2) draft.readConsent = form.elements.readConsent.checked;
  }
  function summaryCards(p) {
    return `<dl class="pu-summary"><div><dt>当前目标</dt><dd>${esc(p.goalName || goals[p.goalType] || '尚未设置')}</dd><small>${esc(goals[p.goalType] || '')} · ${money(p.target)}</small></div><div><dt>最低现金预留</dt><dd>${money(p.reserve)}</dd><small>${esc(stability[p.incomeStability] || '尚未设置')}</small></div><div><dt>相处方式</dt><dd>${esc(styles[p.style] || '清晨')}</dd><small>${p.automation === 'advice' ? '只给建议，由我操作' : '每次行动由我确认'}</small></div></dl>`;
  }
  function preview() {
    const estimate = adapter.previewProfile ? adapter.previewProfile(draft) : null;
    const policy = estimate ? `<div class="pu-ledger"><strong>这些选择将如何影响安排</strong><p>当前策略侧重：${esc(estimate.mode)}。按现有模拟账本，30 天最低预测余额 ${money(estimate.min)}，相对你设定底线的余量为 ${money(estimate.free)}。</p><p>这是规则计算的初始策略；每次转账仍需另外确认。</p></div>` : '';
    frame(`<h1 id="pu-title" data-pu-title tabindex="-1">这是我们的第一份默契。</h1><p class="pu-intro">核对后进入工作台。目标与现金底线由你设定，动态现金状态会随着模拟账本更新。</p>${summaryCards(draft)}${policy}<div class="pu-promise"><strong>会变化的是资金安排，会保留的是你的选择。</strong><p>工资延期或新增账单会重新计算现金余量和策略侧重。系统会留下变化记录；目标、展示风格和操作权限不会因事件自动改变。</p></div><p class="pu-error" data-pu-error role="alert" tabindex="-1" hidden></p><div class="pu-form-actions"><button type="button" class="btn" data-pu="back-preview">返回修改</button><button type="button" class="btn solid" data-pu="save">确认画像，进入工作台 <span aria-hidden="true">→</span></button></div>`, 'YOUR FINANCIAL DNA / 画像确认');
  }
  function describe(value) {
    if (value == null || value === '') return '—';
    if (typeof value !== 'object') return String(value);
    const labels = { min:'30 天最低余额', free:'可安排资金', mode:'策略', incomeStatus:'收入状态', reserve:'现金底线', goalName:'目标', version:'版本' };
    return Object.entries(value).filter(([,v]) => typeof v !== 'object').slice(0,6).map(([key,v]) => `${labels[key] || key}：${['min','free','reserve'].includes(key) ? money(v) : v}`).join('；') || '已重新计算';
  }
  function profilePage() {
    const root = mount(); if (!root) return;
    const d = data(), p = d.profile || {}, s = d.snapshot || {}, priorities = s.priorities || [];
    const updates = (d.updates || []).slice().sort((a, b) => (new Date(b.at).getTime() || 0) - (new Date(a.at).getTime() || 0));
    root.innerHTML = `<header class="page-title pu-page-heading"><div><p class="pu-eyebrow">我的财务底色</p><h1>我的画像，和正在变化的生活。</h1><p class="lead">${esc(d.accountName)} · 由你的选择与模拟账本共同构成。</p></div><button type="button" class="btn" data-pu-page="edit">编辑我的选择</button></header><section class="panel pu-profile-section"><div class="block-head"><div><h2>由我决定的长期选择</h2><p>账户事件不会自动改写这些选择。</p></div><span class="pill">${d.completed ? '已确认' : '待建立'}</span></div>${summaryCards(p)}<p class="pu-note">${p.readConsent ? '已同意读取模拟账本' : '尚未同意读取模拟账本'} · 每次模拟转账仍需单独确认</p></section><div class="pu-dashboard-grid"><section class="panel pu-profile-section"><div class="block-head"><div><h2>此刻的现金状态</h2><p>最近更新 ${esc(stamp(s.updatedAt))} · 版本 ${esc(s.version == null ? '—' : s.version)}</p></div><span class="pill">${esc(s.mode || '等待计算')}</span></div><dl class="pu-metrics"><div><dt>30 天最低预测余额</dt><dd class="${Number(s.min) < Number(p.reserve) ? 'warn-t' : ''}">${money(s.min)}</dd></div><div><dt>当前可安排资金</dt><dd>${money(s.free)}</dd></div></dl><p class="pu-note">收入状态：${esc(s.incomeStatus || '依据当前模拟账本')}</p><p class="pu-note">现金预测以演示账本与既定支出假设计算，实际生活可能出现未记录支出。</p></section><section class="panel pu-profile-section"><div class="block-head"><div><h2>为什么这样安排</h2><p>规则分配的策略侧重；数值不代表预测概率。</p></div></div><div class="pu-priorities">${priorities.map(item => `<div class="pu-priority"><div><strong>${esc(item.name)}</strong><span>${esc(item.value)}%</span></div><meter min="0" max="100" value="${Math.min(100,Math.max(0,Number(item.value)||0))}" aria-label="${esc(item.name)}策略侧重">${esc(item.value)}%</meter><ul>${(item.reasons || []).map(reason => `<li>${esc(reason)}</li>`).join('')}</ul></div>`).join('') || '<p class="pu-note">建立画像后显示计算依据。</p>'}</div></section></div><section class="panel pu-profile-section"><div class="block-head"><div><h2>生活有变化，安排跟着变</h2><p>触发一个模拟事件，观察现金状态、策略侧重和变化记录。</p></div><span class="pill">演示事件</span></div><div class="pu-events"><button class="pu-event" type="button" data-pu-event="income_delayed"><span aria-hidden="true">↘</span><strong>工资延迟了</strong><small>重新评估可用现金与待执行计划</small></button><button class="pu-event" type="button" data-pu-event="bill_added"><span aria-hidden="true">＋</span><strong>多了一笔维修费</strong><small>新增一笔 ¥1,200 的模拟账单</small></button><button class="pu-event" type="button" data-pu-event="salary_received"><span aria-hidden="true">↗</span><strong>工资到账了</strong><small>按当前演示账户模拟收入到账</small></button></div><p id="pu-page-status" class="pu-note" role="status" aria-live="polite"></p></section><section class="panel pu-profile-section"><div class="block-head"><div><h2>画像更新记录</h2><p>每次变化都保留来源、前后状态与原因。</p></div><button type="button" class="btn sm" data-pu-page="reset">重置当前账户演示</button></div><ol class="pu-updates">${updates.map(update => `<li><div class="pu-update-head"><strong>${esc(update.title)}</strong><time>${esc(stamp(update.at))}</time></div><span class="pu-update-source">来源：${esc(update.source || '模拟账本')}</span><div class="pu-before-after"><p><small>变化前</small>${esc(describe(update.before))}</p><span aria-hidden="true">→</span><p><small>变化后</small>${esc(describe(update.after))}</p></div><p class="pu-update-reason">${esc(update.reason)}</p></li>`).join('') || '<li class="pu-note">还没有更新记录。建立画像或触发模拟事件后，这里会留下依据。</li>'}</ol></section><p class="pu-boundary">画像动态更新的是现金状态和策略建议。长期目标、现金底线与操作权限，都需要你主动修改。</p>`;
  }
  function render() {
    if (!adapter) return;
    profilePage();
    if (!screen && !data().completed) { screen = 'login'; login(); }
    else if (!screen) setModal(false);
  }
  async function action(name) {
    if (busy) return;
    if (name === 'continue') {
      if (data().completed) { close(); adapter.enterWorkbench(); } else { screen = 'setup'; editing = false; draft = defaultDraft(); step = 0; setup(); }
    } else if (name === 'cancel') { if (data().completed) close(); }
    else if (name === 'back') {
      collect();
      if (step > 0) { step--; setup(); }
      else if (editing) close(); else { screen = 'login'; login(); }
    } else if (name === 'back-preview') { screen = 'setup'; step = 2; setup(); }
    else if (name === 'sample') {
      collect();
      const sample = defaultDraft(), f = data().facts || {};
      if (step === 0) Object.assign(draft, { goalType: sample.goalType, goalName: sample.goalName || '安心过完这个月', target: sample.target || 10000 });
      if (step === 1) Object.assign(draft, { reserve: sample.reserve === '' ? Math.round((Number(f.daily) || 100) * 30) : sample.reserve, incomeStability: sample.incomeStability });
      if (step === 2) Object.assign(draft, { style: sample.style, automation: sample.automation });
      setup();
    } else if (name === 'save') {
      if (!draft.readConsent) { error('请返回上一步，主动勾选模拟账本读取同意。'); return; }
      busy = true;
      const button = gate().querySelector('[data-pu="save"]'); if (button) { button.disabled = true; button.textContent = '正在生成画像…'; }
      try {
        const result = await adapter.saveProfile({ ...draft, target: Number(draft.target), reserve: Number(draft.reserve), goalName: String(draft.goalName).trim() });
        if (result && result.ok === false) error(result.error); else { close(); adapter.enterWorkbench(); }
      } catch (err) { error('画像暂未保存，请重试。'); }
      finally { busy = false; if (button && document.contains(button)) { button.disabled = false; button.textContent = '确认画像，进入工作台 →'; } }
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
      if (!event.target.reportValidity()) return;
      collect();
      if (!String(draft.goalName || '').trim()) { error('请给当前目标起一个名字。'); return; }
      if (step < 2) { step++; setup(); } else { screen = 'preview'; preview(); }
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
  function openLogin() { restoreFocus = document.activeElement; editing = false; screen = 'login'; login(); }
  function openSetup() { restoreFocus = document.activeElement; editing = Boolean(data().completed); screen = 'setup'; step = 0; draft = defaultDraft(); setup(); }
  window.TroposProfileUI = { init, render, openLogin, openSetup };
})();
