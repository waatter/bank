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
    if (step.tool === 'family_status') { const f = r.family || {}; const m = Object.fromEntries(list(f.members).map(x => [x.id, x]));
      return `<ul class="ms-mini">${list(f.members).map(x=>`<li><b>${esc(x.relation)} ${esc(x.name===x.relation?'':x.name)}</b><span>本月 ${money(x.used)} / ${money(x.monthlyCap)}${x.status==='paused'?' · 已暂停':''}</span></li>`).join('')}${list(f.requests).filter(q=>q.status==='pending').map(q=>`<li><b>待批准：${esc(m[q.member]?.relation||'')} · ${esc(q.merchant)}</b><span>${money(q.amount)} · ${esc(q.category)}</span></li>`).join('')}</ul>`; }
    if (step.tool === 'pocket_status') return `<ul class="ms-mini">${list(r.pockets).map(p=>`<li><b>${esc(p.name)}</b><span>${money(p.balance)}${p.target?' / 目标 '+money(p.target):''}</span></li>`).join('')}</ul>`;
    if (step.tool === 'budget_status' || step.tool === 'spending_report') return `<ul class="ms-mini">${list(r.spending?.rows).slice(0,6).map(x=>`<li><b>${esc(x.category)}</b><span>${money(x.spent)}${x.budget?' / 预算 '+money(x.budget):''}</span></li>`).join('')}</ul><p class="ms-fine">${esc(r.spending?.basis||'')}</p>`;
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
    const HOUSE = {declined:'已拒绝',active:'已恢复',paused:'已暂停',request_sent:'已发出请求',created:'已新建',budget_set:'已设定',noted:'已记下'};
    if (r.receipt && HOUSE[r.receipt.status] && !r.receipt.card) return `<div class="ms-receipt"><span class="ms-stamp">${HOUSE[r.receipt.status]}</span>${r.receipt.monthlyCap!=null?`<span>每月额度 ${money(r.receipt.monthlyCap)}</span>`:''}<code>${esc(r.receipt.id)}</code></div>`;
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
    const clause = step.tool === 'guard' || !step.clause ? '' : step.source && step.source !== step.clause
      ? `<p class="ms-clause">你说“${esc(step.source)}” → 理解为“${esc(step.clause)}”</p>` : `<p class="ms-clause">“${esc(step.clause)}”</p>`;
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
      if (document.getElementById('familyMount')?.offsetParent) renderFamily();
      if (document.getElementById('budgetMount')?.offsetParent) renderBudget();
    } catch (e) {
      error.textContent = e.message + (e.code === 'STALE_APPROVAL' || e.code === 'APPROVAL_EXPIRED' ? ' 请重新发送这句话生成新任务单。' : ' 当前不宣称任何步骤成功。');
      button.disabled = false;
    } finally { busy = false; }
  }

  /* ---------------- 亲情与荷包、记账与预算 ---------------- */
  const askBtn = (text, label, cls='') => `<button type="button" class="btn sm ${cls}" data-mission-ask="${esc(text)}">${esc(label)}</button>`;
  const bar = (value, max, tone='') => `<div class="hh-bar ${tone}"><i style="width:${Math.min(100, max ? value / max * 100 : 0).toFixed(1)}%"></i></div>`;
  let houseSeq = 0;
  async function loadHouse(mountId) {
    const mount = document.getElementById(mountId);
    if (!mount || !adapter) return null;
    const ctx = adapter.context();
    if (!ctx.serverMode || !ctx.completed) { mount.innerHTML = '<article class="panel"><p>请先建立画像并连接服务端沙箱。</p></article>'; return null; }
    const seq = ++houseSeq;
    try { const data = await adapter.household(); return seq === houseSeq ? data : null; }
    catch (e) { mount.innerHTML = `<article class="panel"><p class="warn-t">${esc(e.message)}</p></article>`; return null; }
  }
  async function renderFamily() {
    const data = await loadHouse('familyMount'); if (!data) return;
    const f = data.family, byId = Object.fromEntries(f.members.map(m => [m.id, m]));
    const who = m => m.relation + (m.name !== m.relation ? ' ' + m.name : '');
    const pending = f.requests.filter(r => r.status === 'pending'), done = f.requests.filter(r => r.status !== 'pending');
    const members = f.members.map(m => `<article class="hh-card"><header><span class="hh-avatar">${esc(m.relation.slice(0,1))}</span><div><b>${esc(who(m))}</b><small>可用于 ${esc(m.categories.join('、'))}</small></div><span class="pill ${m.status==='paused'?'warn':'ok'}">${m.status==='paused'?'已暂停':'可代付'}</span></header>${bar(m.used, m.monthlyCap, m.used / m.monthlyCap > .8 ? 'warn' : '')}<p class="hh-num"><b class="num">${money(m.used)}</b> / 每月 ${money(m.monthlyCap)}</p><div class="hh-act">${m.status==='paused'?askBtn(`恢复${m.relation}的亲情付`,'恢复'):askBtn(`暂停${m.relation}的亲情付`,'暂停','quiet')}${askBtn(`把${m.relation}的亲情付额度调到${m.monthlyCap + 200}`,`额度调到 ${money(m.monthlyCap + 200)}`,'quiet')}</div></article>`).join('');
    const requests = pending.length ? pending.map(r => { const m = byId[r.member]; const fit = m.categories.includes(r.category);
      return `<li class="hh-req ${r.risk||!fit?'risk':''}"><div><b>${esc(who(m))} · ${esc(r.merchant)}</b><small>${esc(r.memo)} · 类目「${esc(r.category)}」${fit?'':'不在允许范围'}</small>${r.risk?`<small class="bad-t">${esc(r.risk)}</small>`:''}</div><span class="num hh-amt">${money(r.amount)}</span><div class="hh-act">${askBtn(`批准${m.relation}的${r.merchant}代付`,'批准')}${askBtn(`拒绝${m.relation}的${r.merchant}代付`,'拒绝','quiet')}</div></li>`; }).join('') : '<li class="ms-fine">没有等你批准的代付请求。</li>';
    const history = done.map(r => `<li><b>${esc(who(byId[r.member]))} · ${esc(r.merchant)}</b><span>${money(r.amount)} · ${r.status==='approved'?'已代付':'已拒绝'}</span></li>`).join('');
    const incoming = f.incoming.map(i => `<article class="hh-card warm"><header><span class="hh-avatar">${esc(i.relation.slice(0,1))}</span><div><b>${esc(i.relation)} · 为你开通的亲情付</b><small>${esc(i.note||'')}</small></div></header>${bar(i.used, i.monthlyCap)}<p class="hh-num"><b class="num">${money(i.monthlyCap - i.used)}</b> 本月还可请${esc(i.relation)}代付</p><div class="hh-act">${askBtn(`让${i.relation}帮我付房租差额500`,`请${i.relation}代付房租差额 ¥500`)}</div></article>`).join('');
    const asks = list(f.asks).map(a => `<li><b>请${esc(a.relation)}代付 · ${esc(a.purpose)}</b><span>${money(a.amount)} · ${{waiting:'等待对方同意',paid:'已到账',declined:'对方未同意'}[a.status]}</span>${a.status==='waiting'?`<span class="hh-act"><button type="button" class="btn sm" data-family-sim="${esc(a.id)}" data-decision="approve">模拟${esc(a.relation)}同意</button><button type="button" class="btn sm quiet" data-family-sim="${esc(a.id)}" data-decision="decline">模拟拒绝</button></span>`:''}</li>`).join('');
    const pockets = data.pockets.map(p => `<article class="hh-pocket"><b>${esc(p.name)}</b><p class="num">${money(p.balance)}</p>${p.target?bar(p.balance, p.target, 'ok')+`<small>目标 ${money(p.target)}</small>`:'<small>未设目标</small>'}<div class="hh-act">${askBtn(`存200到${p.name}`,'存 ¥200')}${p.balance>0?askBtn(`从${p.name}取回${Math.min(200,p.balance)}`,'取回','quiet'):''}</div></article>`).join('');
    document.getElementById('familyMount').innerHTML = `<div class="page-title"><h1>一家人的钱，各有各的口袋。</h1><p class="lead">亲情付让家人在你定的额度和类目里花钱，每一笔都等你点头；小荷包把要攒的钱分开放。页面上的按钮同样只是替你说一句话，走同一张任务单。</p></div>
      <div class="hh-grid"><section><div class="block-head"><div><h2>待你批准</h2><p>超额、类目不符、直播间保健品等高风险商户会被守诺拦下</p></div></div><ul class="hh-list">${requests}</ul>${history?`<ul class="ms-mini">${history}</ul>`:''}
      <div class="block-head hh-sub"><div><h2>我为家人开通</h2><p>额度与类目由你决定，可随时暂停</p></div></div><div class="hh-cards">${members}</div></section>
      <section>${incoming?`<div class="block-head"><div><h2>家人为我开通</h2><p>父母帮子女支付：对方同意后才到账</p></div></div><div class="hh-cards">${incoming}</div>${asks?`<ul class="ms-mini hh-asks">${asks}</ul>`:''}`:''}
      <div class="block-head hh-sub"><div><h2>小荷包</h2><p>分开放的钱不计入可自由安排的余额</p></div>${askBtn('新建一个旅行小荷包目标5000','新建荷包','quiet')}</div><div class="hh-pockets">${pockets}</div></section></div>`;
  }
  async function renderBudget() {
    const data = await loadHouse('budgetMount'); if (!data) return;
    const rows = data.spending.rows.map(r => { const over = r.budget && r.spent > r.budget, near = r.budget && r.spent > r.budget * .8;
      return `<li><div class="bg-head"><b>${esc(r.category)}</b><span class="num">${money(r.spent)}${r.budget?` <small>/ 预算 ${money(r.budget)}</small>`:''}</span></div>${r.budget?bar(r.spent, r.budget, over?'bad':near?'warn':'ok'):''}${over?'<small class="bad-t">已超预算</small>':near?'<small class="warn-t">接近预算</small>':''}</li>`; }).join('');
    const notes = data.notes.map(n => `<li><b>${esc(n.label)}</b><span>${esc(n.category)} · ${money(n.amount)}</span></li>`).join('');
    document.getElementById('budgetMount').innerHTML = `<div class="page-title"><h1>记账与预算</h1><p class="lead">银行流水自动归类，现金和其他渠道的花销说一句就能记。预算只用来提醒你，不会替你拦下支付。</p></div>
      <div class="hh-grid"><section><div class="block-head"><div><h2>本月花在哪</h2><p>${esc(data.spending.basis)}</p></div><b class="num bg-total">${money(data.spending.total)}</b></div><ul class="bg-rows">${rows}</ul></section>
      <section><div class="block-head"><div><h2>记一笔</h2><p>例如“午饭 35”“打车 28”“电影 90”</p></div></div><form class="bg-form" id="noteForm"><input id="noteInput" autocomplete="off" placeholder="午饭 35" aria-label="记一笔"><button class="btn solid" type="submit">记下</button></form>
      <div class="hh-act bg-quick">${askBtn('外卖每月预算不超过1000','餐饮预算 ¥1,000','quiet')}${askBtn('交通每月预算不超过400','交通预算 ¥400','quiet')}${askBtn('看看这个月花在哪','让 TROPOS 分析','quiet')}</div>
      <div class="block-head hh-sub"><div><h2>手记明细</h2><p>只记入记账本，不改银行余额</p></div></div>${notes?`<ul class="ms-mini">${notes}</ul>`:'<p class="ms-fine">还没有手记支出。</p>'}</section></div>`;
  }
  document.addEventListener('submit', e => {
    if (e.target.id !== 'noteForm') return;
    e.preventDefault();
    const v = document.getElementById('noteInput').value.trim();
    if (v && adapter) adapter.ask('记一笔' + v);
  });
  document.addEventListener('click', async e => {
    const b = e.target.closest('[data-family-sim]');
    if (!b || !adapter || busy) return;
    busy = true; b.disabled = true;
    try { const r = await adapter.familySimulate(b.dataset.familySim, b.dataset.decision); adapter.handleSnapshot(r.snapshot); adapter.toast(r.ask.status === 'paid' ? '家人已同意，款项已到账' : '已模拟对方拒绝'); }
    catch (err) { adapter.toast(err.message, 'warn'); }
    finally { busy = false; renderFamily(); }
  });

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
  window.TroposMissionUI = {init(value) { adapter = value; }, html, render, renderFamily, renderBudget};
})();
