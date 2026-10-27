/* TROPOS · 一句话办完一串事。任务单只展示服务端草案；勾选与口令只表达授权，执行与核验都在服务端。 */
(function () {
  'use strict';
  let adapter = null, busy = false, walletSeq = 0;
  const missions = new Map();
  const esc = value => String(value == null ? '' : value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const money = value => value == null || !Number.isFinite(Number(value)) ? '—' : '¥' + Number(value).toLocaleString('zh-CN', {minimumFractionDigits:2, maximumFractionDigits:2});
  const list = value => Array.isArray(value) ? value : [];
  const TIER = {0:['t0','只读','已直接查询'],1:['t1','可逆','需你勾选'],2:['t2','资金','需你勾选 · 执行前重新核验'],3:['t3','不可逆','需你勾选并输入口令']};
  const STATUS = {ready:['待授权',''],done:['已完成','ok'],blocked:['守诺拦下','bad'],needs_clarification:['需要补充','warn'],refused:['整单拦截','bad'],
    failed:['未通过','bad'],paused:['已暂停','warn'],skipped:['未授权 · 跳过','']};
  const CARD_STATUS = {active:'正常',frozen:'已冻结',lost:'已挂失'};

  function readResult(step) {
    const r = step.result || {};
    if (step.tool === 'card_status') return `<ul class="ms-mini">${list(r.cards).map(c=>`<li><b>${esc(c.type)} 尾号 ${esc(c.last4)}</b><span>${esc(CARD_STATUS[c.status]||c.status)}</span></li>`).join('')}</ul>`;
    if (step.tool === 'wealth_catalog') return `<ul class="ms-mini">${list(r.products).map(p=>`<li><b>${esc(p.name)}</b><span>${esc(p.riskName)}${p.suitable?'':' · 超出你的承受上限'}</span></li>`).join('')}</ul><p class="ms-fine">你的风险承受上限（画像推断）：${esc(r.capacity)}</p>`;
    if (step.tool === 'subscriptions_report') return `<ul class="ms-mini">${list(r.subscriptions).map(s=>`<li><b>${esc(s.name)}</b><span>${money(s.fee)}/月${s.flag?' · '+esc(s.flag):''}</span></li>`).join('')}</ul>`;
    if (step.tool === 'bills_report') return `<ul class="ms-mini">${list(r.bills).slice(0,5).map(b=>`<li><b>第 ${esc(b.day)} 天 ${esc(b.label)}</b><span>${money(Math.abs(b.amount))}</span></li>`).join('')}</ul><p class="ms-fine">${esc(r.note||'')}</p>`;
    if (step.tool === 'cash_plan') return `<p class="ms-fine">已到账 ${money(r.balance)} · 30 天最低 ${money(r.min)}（第 ${esc(r.minDay)} 天）· 底线以上余量 ${money(r.free)}</p>`;
    return '';
  }
  function receipt(step) {
    const r = step.result || {};
    if (r.transaction) return `<div class="ms-receipt"><span class="ms-stamp">已记账</span><span>${money(Math.abs(r.transaction.amount))}</span><code>${esc(r.transaction.receipt)}</code></div>`;
    if (r.receipt) return `<div class="ms-receipt"><span class="ms-stamp">${r.receipt.status==='limit_application_submitted'?'已提交申请':'已生效'}</span><span>尾号 ${esc(r.receipt.card)} · ${esc(CARD_STATUS[r.receipt.status]||'待银行审核')}</span><code>${esc(r.receipt.id)}</code></div>`;
    if (r.receipts) return `<div class="ms-receipt"><span class="ms-stamp">已取消</span><span>${r.receipts.length} 项续费</span><code>${esc(r.receipts.map(x=>x.id).join(' · '))}</code></div>`;
    return '';
  }
  function stepHTML(mission, step, index) {
    const [tc, tname, trule] = TIER[step.tier] || TIER[0];
    const isClarify = step.tool === 'clarify' || step.tool === 'guard';
    const [label, tone] = STATUS[step.status] || [step.status, ''];
    const planned = mission.status === 'planned';
    const failedChecks = list(step.checks).filter(c => !c.ok);
    const head = `<div class="ms-head"><span class="ms-title">${esc(step.title)}</span>${step.amount?`<span class="ms-amt num">${money(step.amount)}</span>`:''}</div>`;
    const meta = `<div class="ms-meta">${isClarify?'<span class="ms-tier tq">澄清</span>':`<span class="ms-tier ${tc}">${tname}</span><span class="ms-rule">${esc(trule)}</span>`}<span class="ms-status ${tone}">${esc(label)}</span></div>`;
    const clause = step.clause && step.tool !== 'guard' ? `<p class="ms-clause">“${esc(step.clause)}”</p>` : '';
    const note = step.note ? `<p class="ms-note">${esc(step.note)}</p>` : '';
    const options = planned && list(step.options).length ? `<div class="ms-options">${step.options.map(o=>`<button type="button" class="ms-chip" data-mission-ask="${esc(o.message)}">${esc(o.label)}</button>`).join('')}</div>` : '';
    const checks = list(step.checks).length ? `<details class="ms-checks"${failedChecks.length?' open':''}><summary>${failedChecks.length?`${failedChecks.length} 项未通过`:`${step.checks.length} 项核验通过`}</summary><ul>${step.checks.map(c=>`<li class="${c.ok?'':'no'}"><i>${c.ok?'✓':'×'}</i><b>${esc(c.name)}</b>${esc(c.detail)}</li>`).join('')}</ul></details>` : '';
    const pick = planned && step.status === 'ready' ? `<label class="ms-pick"><input type="checkbox" data-mission-step="${esc(step.id)}" checked><span>授权这一步</span></label>` : '';
    const phrase = planned && step.status === 'ready' && step.tier === 3 ? `<label class="ms-phrase">确认口令<input inputmode="numeric" maxlength="4" autocomplete="off" placeholder="${esc(step.phraseHint||'卡号后四位')}" data-mission-phrase="${esc(step.id)}"></label>` : '';
    return `<li class="ms-step s-${esc(step.status)} ${isClarify?'tq':tc}"><span class="ms-n">${index + 1}</span><div class="ms-body">${head}${meta}${clause}${note}${step.tier===0&&step.status==='done'?readResult(step):''}${checks}${options}${step.status==='done'&&step.tier>0?receipt(step):''}${pick}${phrase}</div></li>`;
  }
  function html(mission) {
    if (!mission || !Array.isArray(mission.steps)) return '';
    missions.set(mission.id, mission);
    const s = mission.summary || {}, planned = mission.status === 'planned';
    const ready = mission.steps.filter(x => x.status === 'ready');
    const done = mission.steps.filter(x => x.status === 'done' && x.tier > 0).length;
    const foot = planned
      ? (ready.length ? `<div class="ms-foot"><p>本单预计转出 <b class="num">${money(s.outflow)}</b>。只执行你勾选的步骤；按顺序逐项重新核验，任一步未通过即停止，后面的暂停。</p><div class="ms-actions"><button type="button" class="btn solid" data-mission-confirm="${esc(mission.id)}">授权并按顺序执行</button><span class="ms-fine">凭证 10 分钟内有效 · 账户有变化即失效</span></div></div>` : '<div class="ms-foot"><p>本单没有需要你授权的写操作。</p></div>')
      : `<div class="ms-foot"><p>已执行 ${done} 步写操作。重复点击只会返回同一结果，不会重复扣款。</p></div>`;
    return `<section class="mission" data-mission-id="${esc(mission.id)}"><header><span class="ms-kicker">任务单</span><h3>${planned?'我把这句话拆成了下面几步':'执行结果'}</h3><p class="ms-fine">${esc(mission.id)} · ${mission.steps.length} 步${s.blocked?` · ${s.blocked} 步被拦下`:''}${s.clarify?` · ${s.clarify} 步需补充`:''}</p></header><ol class="ms-steps">${mission.steps.map((step, i) => stepHTML(mission, step, i)).join('')}</ol>${foot}<div class="ms-error" aria-live="polite"></div></section>`;
  }

  async function confirm(id, button) {
    const mission = missions.get(id), root = button.closest('.mission');
    if (!mission || busy || !adapter || adapter.busy()) return;
    const approve = [...root.querySelectorAll('[data-mission-step]')].filter(x => x.checked).map(x => x.dataset.missionStep);
    const phrases = {};
    root.querySelectorAll('[data-mission-phrase]').forEach(input => { if (approve.includes(input.dataset.missionPhrase)) phrases[input.dataset.missionPhrase] = input.value.trim(); });
    const error = root.querySelector('.ms-error');
    if (!approve.length) { error.textContent = '还没有勾选任何步骤；不勾选就不会执行。'; return; }
    const titles = mission.steps.filter(x => approve.includes(x.id)).map(x => `${x.title}${x.amount ? ' ' + money(x.amount) : ''}`);
    const ok = await adapter.modal({title:`授权 ${approve.length} 步？`, body:`<p>将按顺序执行：</p><ol>${titles.map(t=>`<li>${esc(t)}</li>`).join('')}</ol><p>每一步执行前服务端重新核验；任一步未通过，后面的步骤暂停。</p>`, ok:'授权执行'});
    if (!ok) return;
    busy = true; button.disabled = true; error.textContent = '';
    try {
      const result = await adapter.confirm(mission, approve, phrases);
      adapter.handleSnapshot(result.snapshot);
      root.outerHTML = html(result.mission);
      const done = result.mission.steps.filter(x => x.status === 'done' && x.tier > 0).length;
      const failed = result.mission.steps.find(x => x.status === 'failed');
      adapter.toast(failed ? `完成 ${done} 步；「${failed.title}」未通过，后续已暂停` : `${done} 步已完成，回执已核验`, failed ? 'warn' : undefined);
      if (document.getElementById('walletMount')?.offsetParent) render();
    } catch (e) {
      error.textContent = e.message + (e.code === 'STALE_APPROVAL' || e.code === 'APPROVAL_EXPIRED' ? ' 请重新发送这句话生成新任务单。' : ' 当前不宣称任何步骤成功。');
      button.disabled = false;
    } finally { busy = false; }
  }

  /* ---------------- 卡与理财 ---------------- */
  async function render() {
    const mount = document.getElementById('walletMount');
    if (!mount || !adapter) return;
    const ctx = adapter.context();
    if (!ctx.serverMode) { mount.innerHTML = '<article class="panel"><p>卡片与理财需要启动服务端沙箱。</p></article>'; return; }
    if (!ctx.completed) { mount.innerHTML = '<article class="panel"><p>请先建立画像并同意读取模拟账本。</p></article>'; return; }
    const seq = ++walletSeq;
    mount.innerHTML = mount.innerHTML || '<p class="ms-fine">正在读取…</p>';
    try {
      const data = await adapter.portfolio();
      if (seq !== walletSeq) return;
      const askBtn = (text, label, cls='') => `<button type="button" class="btn sm ${cls}" data-mission-ask="${esc(text)}">${esc(label)}</button>`;
      const cards = data.cards.map(c => `<article class="wcard ${c.type==='信用卡'?'credit':'debit'} st-${esc(c.status)}"><div class="wcard-top"><span>${esc(c.type)}</span><span class="wcard-st">${esc(CARD_STATUS[c.status]||c.status)}</span></div><div class="wcard-no num">•••• •••• •••• ${esc(c.last4)}</div><div class="wcard-foot"><span>${c.type==='信用卡'?'额度 '+money(c.limit):'单日限额 '+money(c.limit)}</span>${c.pendingLimit?`<span>调额申请 ${money(c.pendingLimit)} 审核中</span>`:''}</div><div class="wcard-act">${c.status==='active'?askBtn(`冻结${c.type}尾号${c.last4}`,'临时冻结'):''}${c.status==='frozen'?askBtn(`解冻${c.type}尾号${c.last4}`,'解冻'):''}${c.status!=='lost'?askBtn(`${c.type}尾号${c.last4}丢了，帮我挂失`,'挂失','quiet'):''}</div></article>`).join('');
      const held = list(data.holdings);
      const products = data.products.map(p => `<li class="${p.suitable?'':'unfit'}"><div><b>${esc(p.name)}</b><span class="ms-tier ${p.risk>2?'t3':p.risk>1?'t2':'t1'}">${esc(p.riskName)}</span>${p.lockDays?`<span class="ms-rule">锁定 ${p.lockDays} 天</span>`:''}</div><p>${esc(p.yield)} · 起购 ${money(p.minAmount)}</p>${p.suitable?askBtn(`买1000元${p.name}`,'草拟申购 ¥1,000'):`<p class="ms-fine">超出你的风险承受上限，守诺会拦下申购。</p>`}</li>`).join('');
      mount.innerHTML = `<div class="page-title"><h1>卡与理财</h1><p class="lead">这里的每个按钮都只是替你“说一句话”：同样生成任务单、同样逐步授权，没有绕过守诺的捷径。</p></div>
        <div class="wallet-grid"><section><div class="block-head"><div><h2>我的卡</h2><p>冻结可随时解冻；挂失不可撤回，需输入卡号后四位</p></div></div><div class="wcards">${cards}</div></section>
        <section><div class="block-head"><div><h2>持有与产品</h2><p>你的承受上限：${esc(data.riskCapacity.name)} · ${esc(data.riskCapacity.basis)}</p></div></div>
        ${held.length?`<ul class="ms-mini held">${held.map(h=>`<li><b>${esc(h.name)}</b><span>${money(h.amount)}${h.lockedUntil&&h.lockedUntil*1000>Date.now()?' · 锁定至 '+esc(new Date(h.lockedUntil*1000).toLocaleDateString('zh-CN')):''}</span>${askBtn(`赎回${h.name}理财`,'赎回','quiet')}</li>`).join('')}</ul>`:'<p class="ms-fine">当前没有持有理财。</p>'}
        <ul class="wproducts">${products}</ul><p class="ms-fine">产品、收益与风险等级均为合成演示数据，不构成投资建议；正式申购前应完成风险测评。</p></section></div>`;
    } catch (e) { if (seq === walletSeq) mount.innerHTML = `<article class="panel"><p class="warn-t">${esc(e.message)}</p></article>`; }
  }

  document.addEventListener('click', event => {
    const target = event.target.closest('[data-mission-confirm],[data-mission-ask]');
    if (!target || !adapter) return;
    if (target.dataset.missionConfirm) confirm(target.dataset.missionConfirm, target);
    else adapter.ask(target.dataset.missionAsk);
  });
  window.TroposMissionUI = {init(value) { adapter = value; }, html, render};
})();
