/* TROPOS · 现金行动中心。只展示服务端快照，所有取消交由已有确认流程。 */
(function () {
  'use strict';
  let adapter = null, data = null, identity = '', loadedIdentity = '', loading = false, busy = false;
  let sequence = 0, mutationSequence = 0, selectedDay = 0, selectedSubscription = '', message = '', failed = false;
  const mount = () => document.getElementById('actionMount');
  const preview = () => document.getElementById('actionPreview');
  const esc = value => String(value == null ? '' : value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const list = value => Array.isArray(value) ? value : [];
  const num = value => value !== null && value !== '' && Number.isFinite(Number(value)) ? Number(value) : null;
  const money = value => num(value) == null ? '—' : '¥' + Number(value).toLocaleString('zh-CN', {maximumFractionDigits:2});
  const count = value => num(value) == null ? '—' : String(Number(value));
  const ctx = () => adapter && adapter.context ? adapter.context() || {} : {};
  const contextKey = value => JSON.stringify([value.persona, value.accountRevision, Boolean(value.completed), Boolean(value.serverMode)]);
  const blocked = () => loading || busy ? ' disabled' : '';
  const isCancelled = s => s.status === 'cancelled' || s.status === 'canceled';
  const current = () => !gate() && data && loadedIdentity === contextKey(ctx()) && data.account && data.account.persona === ctx().persona;
  function incomeNotice() {
    const received=data && data.snapshot && data.snapshot.salaryReceived;
    if(received === true) return '本期工资已有合成入账，现金预测不会重复计入。';
    if(received === false) return '本期工资预计未到账；预计入账不是到账承诺。';
    return '预计收入不等于已到账余额。';
  }
  const sourceLabel = value => ({bill:'已登记账单', subscription:'订阅续费', salary:data && data.snapshot && data.snapshot.salaryReceived === false ? '预计工资（未到账）' : '工资事件'}[value] || '服务端登记事项');
  function button(action, label, extra, primary) {
    return `<button type="button" class="ac-button${primary ? ' ac-button-primary' : ''}" data-action-ui="${action}" ${extra || ''}${blocked()}>${esc(label)}</button>`;
  }
  function date(day) {
    const base = data && data.basis && data.basis.baseDate || '2026-10-20';
    if (!/^\d{4}-\d{2}-\d{2}$/.test(base) || num(day) == null) return '第 ' + count(day) + ' 天';
    const d = new Date(base + 'T00:00:00Z');
    d.setUTCDate(d.getUTCDate() + Number(day));
    return Number.isNaN(d.getTime()) ? '第 ' + count(day) + ' 天' : d.toISOString().slice(0, 10);
  }
  function dayLabel(day) { return num(day) == null ? '尚无数据' : date(day) + ' · 第 ' + count(day) + ' 天'; }
  function eventsFor(day) { return list(data && data.upcoming).filter(e => num(e.day) === Number(day)); }
  function eventText(event) {
    return `${event.label || '待办事项'} · ${money(event.amount)} · ${sourceLabel(event.source)}${event.status === 'expected' ? ' · 预计，尚未到账' : ' · 已排期，尚未发生'}`;
  }
  function resetForContext() {
    const c = ctx(), key = contextKey(c);
    if (key === identity) return false;
    const previousPersona = identity ? JSON.parse(identity)[0] : null;
    const samePersona = previousPersona === c.persona;
    identity = key; data = null; loadedIdentity = ''; selectedDay = 0; selectedSubscription = ''; failed = false;
    message = samePersona ? '账户数据版本已更新，请刷新后查看最新行动。' : '';
    if (!samePersona) { loading = false; busy = false; ++sequence; ++mutationSequence; }
    return true;
  }
  function invalidate() {
    ++sequence; data = null; loadedIdentity = ''; loading = false; selectedSubscription = ''; message = ''; failed = false;
    render(false);
  }
  function gate() {
    const c = ctx();
    if (!c.serverMode || location.protocol === 'file:') return ['请先启动本地服务', '现金行动单需要本地服务提供当前账户数据。请按项目说明启动服务后，通过 http://localhost 打开演示。'];
    if (!c.completed) return ['先建立你的财务画像', '完成画像与数据使用确认后，才能根据这一个账户生成现金行动单。'];
    return null;
  }
  function emptyPanel(title, copy, profile) {
    return `<section class="ac-panel ac-empty"><span class="ac-empty-symbol" aria-hidden="true">↗</span><h2>${esc(title)}</h2><p>${esc(copy)}</p>${profile ? button('profile','建立我的画像','',true) : ''}</section>`;
  }
  function heading() {
    const a = data && data.account || {}, b = data && data.basis || {};
    return `<header class="ac-heading"><div><p class="ac-eyebrow">接下来怎么安排</p><h1>先看看眼下最要紧的事。</h1><p class="ac-intro">把房租、账单和你想留的钱放在一起，看看接下来怎么安排更稳妥。</p><p class="ac-meta">${data ? `账户 ${esc(a.name || a.persona)} <span>${esc(a.persona)}</span> · 基准日 ${esc(b.baseDate || '2026-10-20')} · 未来 ${count(b.horizonDays || 30)} 天` : `当前账户 ${esc(ctx().persona || '未选择')} · 基准日 2026-10-20`}</p></div><div class="ac-toolbar">${button('refresh',loading ? '正在更新…' : '重新查看')}${data ? button('download','下载这份安排','',true) : ''}</div></header>${data ? `<p class="ac-version">服务端版本 ${esc(data.version || '未提供')} · 账户修订 ${esc(a.revision == null ? '未提供' : a.revision)} · 生成时间 ${esc(data.generatedAt || '未提供')}</p>` : ''}${message ? `<p class="ac-status${failed ? ' ac-status-error' : ''}" role="status">${esc(message)}</p>` : ''}`;
  }
  function metrics() {
    const l = data.liquidity || {}, o = data.outcomes || {};
    const cards = [
      ['至少还差多少钱', money(l.gap), num(l.gap) === 0 ? '按目前情况，手头的钱够用' : `现金底线 ${money(l.reserve)} · ${count(l.breachDays)} 天低于底线`],
      ['未来现金最低点', money(l.minBalance), dayLabel(l.minDay)],
      ['今天可支付现金', money(l.balance), '来自当前账户快照'],
      ['已取消续费 · 本窗口预计少支出', money(o.avoidedUpcomingOutflow), `${count(o.cancelledCount)} 项已取消 · 按未来窗口内原续费计`]
    ];
    return `<dl class="ac-metrics">${cards.map((x,i) => `<div class="ac-metric${i === 0 && num(l.gap) > 0 ? ' ac-metric-alert' : ''}"><dt>${esc(x[0])}</dt><dd>${esc(x[1])}</dd><small>${esc(x[2])}</small></div>`).join('')}</dl>`;
  }
  function actionCards(compact) {
    const actions = list(data.actions).slice(0, compact ? 3 : undefined);
    if (!actions.length) return `<p class="ac-note">当前快照暂无行动建议。仍可核对现金路径与到期事项。</p>`;
    return `<ol class="ac-action-list${compact ? ' ac-compact' : ''}">${actions.map((a,i) => `<li class="ac-action"><span class="ac-order" aria-hidden="true">${String(i+1).padStart(2,'0')}</span><div><div class="ac-action-title"><h${compact ? '3' : '2'}>${esc(a.title || '看看这项安排')}</h${compact ? '3' : '2'}>${a.priority === 'high' ? '<span class="ac-tag">优先处理</span>' : ''}</div><p>${esc(a.reason || '请先核对当前账户与登记信息。')}</p>${!compact ? `<p class="ac-source">来源：${esc(actionSource(a))}</p>` : ''}<div class="ac-next"><span>下一步</span>${button('action',actionButtonLabel(a),`data-action-id="${esc(a.id)}"`)}</div></div></li>`).join('')}</ol>`;
  }
  function actionSource(a) {
    if (a.subscriptionId) { const s = list(data.subscriptions).find(s => s.id === a.subscriptionId); return s ? `${s.name} · ${money(s.fee)} · 续费日 ${s.next || '未提供'}` : '服务端订阅登记'; }
    return ({liquidity:'未来余额和你想留的钱',subscription:'服务端订阅登记',review:'当前可见流水的核对项',spending:'当前可见流水分类',income:'已登记的预计收入',salary:'工资事件与登记计划',bill:'已登记账单'}[a.kind] || '服务端根据当前账户快照生成');
  }
  function actionButtonLabel(a) { return a.subscriptionId ? '查看取消前后' : ({life:'安排生活支出',bills:'核对到期事项',transfer:'查看转账安排',profile:'调整我的画像',console:'核对待确认计划'}[a.view] || '查看相关安排'); }
  function selectedSub() { return list(data && data.subscriptions).find(s => s.id === selectedSubscription && !isCancelled(s) && s.impact) || null; }
  function chart() {
    const l = data.liquidity || {}, baseline = list(l.points).map(num), sub = selectedSub(), alternative = list(sub && sub.impact.points).map(num);
    if (!baseline.some(v => v != null)) return emptyPanel('现金路径尚未生成','刷新数据后，查看未来现金的逐日变化。');
    const values = baseline.concat(alternative, [num(l.reserve),0]).filter(v => v != null), low = Math.min(...values), high = Math.max(...values), spread = Math.max(high-low,100);
    const lo = low-spread*.12, hi = high+spread*.15, w=760,h=260,p={l:58,r:20,t:26,b:36}, last=Math.max(baseline.length-1,1);
    const x = i => p.l+i/last*(w-p.l-p.r), y = v => p.t+(hi-v)/(hi-lo)*(h-p.t-p.b);
    const path = points => points.map((v,i) => v == null ? '' : `${i === 0 || points[i-1] == null ? 'M' : 'L'}${x(i).toFixed(2)},${y(v).toFixed(2)}`).join(' ');
    const d = Math.min(Math.max(selectedDay,0),baseline.length-1); selectedDay=d;
    const reserve = num(l.reserve), tickValues = [high,(high+low)/2,low];
    const eventDays = Array.from(new Set(list(data.upcoming).map(e => num(e.day)).filter(v => v != null && v >= 0 && v <= last)));
    const svg = `<svg viewBox="0 0 ${w} ${h}" role="img" aria-labelledby="ac-chart-title ac-chart-desc"><title id="ac-chart-title">未来每天的余额估算${sub ? '，含单项取消预览' : ''}</title><desc id="ac-chart-desc">横轴为基准日起的天数，纵轴为人民币元。${esc(`现金底线 ${money(l.reserve)}，基准最低余额 ${money(l.minBalance)}，缺口 ${money(l.gap)}。`)}逐日数值与事件可在下方表格查看。${esc(incomeNotice())}</desc>${tickValues.map(v => `<line x1="${p.l}" y1="${y(v).toFixed(2)}" x2="${w-p.r}" y2="${y(v).toFixed(2)}" class="ac-gridline"/><text x="${p.l-9}" y="${(y(v)+4).toFixed(2)}" text-anchor="end">${esc(Math.round(v).toLocaleString('zh-CN'))}</text>`).join('')}${reserve != null ? `<line x1="${p.l}" y1="${y(reserve).toFixed(2)}" x2="${w-p.r}" y2="${y(reserve).toFixed(2)}" class="ac-reserve"/>` : ''}<path d="${path(baseline)}" class="ac-baseline"/>${sub && alternative.length ? `<path d="${path(alternative)}" class="ac-alternative"/>` : ''}${eventDays.map(i => baseline[i] == null ? '' : `<circle cx="${x(i).toFixed(2)}" cy="${y(baseline[i]).toFixed(2)}" r="4" class="ac-event-dot"/>`).join('')}<line x1="${x(d).toFixed(2)}" y1="${p.t}" x2="${x(d).toFixed(2)}" y2="${h-p.b}" class="ac-selected-line"/>${baseline[d] != null ? `<circle cx="${x(d).toFixed(2)}" cy="${y(baseline[d]).toFixed(2)}" r="6" class="ac-selected-dot"/>` : ''}${[0,Math.round(last/2),last].map(i=>`<text x="${x(i).toFixed(2)}" y="${h-10}" text-anchor="middle">第 ${i} 天</text>`).join('')}</svg>`;
    return `<section class="ac-panel" id="ac-cash-path"><div class="ac-section-head"><div><p class="ac-eyebrow">接下来 ${count((data.basis || {}).horizonDays || 30)} 天</p><h2>现金在哪一天最紧？</h2></div><span class="ac-tag">按天估算 · 工资不一定按时到账</span></div><p class="ac-note">余额按已登记事项和服务端假设推演。${esc(incomeNotice())}请展开下方假设核对计算范围。</p><div class="ac-legend"><span><i></i>当前基准路径</span><span><i class="ac-legend-reserve"></i>现金底线</span>${sub ? `<span><i class="ac-legend-alt"></i>仅取消 ${esc(sub.name)} 后</span>` : ''}</div><div class="ac-chart">${svg}</div>${sub ? `<p class="ac-callout">正在预览：仅取消「${esc(sub.name)}」。本次预计避免 ${money(sub.impact.avoidedOutflow)} 支出；预览尚未执行。${button('baseline','返回当前基准')}</p>` : ''}<div class="ac-day-picker"><label for="ac-day">查看某一天</label><input id="ac-day" type="range" min="0" max="${last}" value="${d}" data-action-ui-day${blocked()}><output for="ac-day">${esc(dayLabel(d))}</output></div><div class="ac-day-detail" aria-live="polite"><div><span>${esc(dayLabel(d))}</span><strong>${money(baseline[d])}</strong><small>当天基准预计现金${sub && alternative[d] != null ? ` · 单项取消后 ${money(alternative[d])}` : ''}</small></div><ul>${eventsFor(d).map(e=>`<li>${esc(eventText(e))}</li>`).join('') || '<li>这一天没有已登记的到期事件。余额仍可能受服务端日常支出假设影响。</li>'}</ul></div><details class="ac-details"><summary>查看逐日现金、到期事件与计算假设</summary><p class="ac-note">第 0 天是基准日。现金余额为日终模型值，事件金额以服务端记录的收支符号显示。</p><div class="ac-table-wrap"><table><caption>逐日现金与已登记事项（人民币元）</caption><thead><tr><th scope="col">日期</th><th scope="col">基准现金</th>${sub ? '<th scope="col">单项取消后</th>' : ''}<th scope="col">当日依据</th></tr></thead><tbody>${baseline.map((v,i)=>`<tr><th scope="row">${esc(date(i))} · 第 ${i} 天</th><td>${money(v)}</td>${sub ? `<td>${money(alternative[i])}</td>` : ''}<td>${eventsFor(i).map(eventText).map(esc).join('<br>') || '无登记事件；沿用模型日常支出假设'}</td></tr>`).join('')}</tbody></table></div>${assumptions()}</details></section>`;
  }
  function assumptions() { return `<ul class="ac-assumptions">${list(data.assumptions).map(a=>`<li>${esc(a)}</li>`).join('') || '<li>服务端未提供额外假设说明，请刷新后核对。</li>'}</ul>`; }
  function upcoming() {
    const rows = list(data.upcoming).slice().sort((a,b) => Number(a.day)-Number(b.day));
    return `<section class="ac-panel"><div class="ac-section-head"><div><p class="ac-eyebrow">日历里的钱</p><h2>接下来会发生什么</h2></div><span class="ac-tag">${rows.length} 项</span></div><ul class="ac-events">${rows.map(e=>`<li><button class="ac-event-button" type="button" data-action-ui="day" data-day="${esc(e.day)}"${blocked()}><span class="ac-event-date">${esc(date(e.day))}<small>第 ${count(e.day)} 天</small></span><span><strong>${esc(e.label)}</strong><small>${esc(sourceLabel(e.source))} · ${e.status === 'expected' ? '预计，尚未到账' : '已排期，尚未发生'}</small></span><span class="ac-event-amount">${money(e.amount)}<small>查看当天依据 ↗</small></span></button></li>`).join('') || '<li class="ac-note">未来窗口内没有已登记的到期事件。</li>'}</ul></section>`;
  }
  function spending() {
    const s = data.spending || {}, cats = list(s.categories), max = Math.max(1,...cats.map(c=>Math.abs(num(c.amount)||0)));
    return `<section class="ac-panel"><div class="ac-section-head"><div><p class="ac-eyebrow">先看见，再调整</p><h2>可见流水出入账</h2></div></div><p class="ac-note">${esc((data.basis || {}).historyLabel || '当前可见合成流水（非完整月报）')} · ${count(s.rowCount)} 笔 · 出账 ${money(s.outflow)} · 入账 ${money(s.inflow)} · 净流入 ${money(s.net)}</p><p class="ac-note">出账含还款和账户调拨，非消费总额。</p><div class="ac-category-bars">${cats.map(c=>`<div class="ac-category"><div><span>${esc(c.name)}</span><strong>${money(c.amount)} <small>${count(c.count)} 笔</small></strong></div><div class="ac-bar" role="img" aria-label="${esc(c.name)}出账 ${esc(money(c.amount))}"><span style="width:${Math.min(100,Math.abs(num(c.amount)||0)/max*100).toFixed(2)}%"></span></div></div>`).join('') || '<p class="ac-note">可见流水暂无出账分类。</p>'}</div><details class="ac-details"><summary>核对金额较大的出账 · ${list(s.largest).length} 笔</summary><ul class="ac-ledger">${list(s.largest).map(r=>`<li><div><strong>${esc(r.merchant || '商户未提供')}</strong><small>${esc(r.category)} · ${esc(r.time)}</small></div><strong>${money(r.amount)}</strong></li>`).join('') || '<li>暂无较大出账记录。</li>'}</ul></details><div class="ac-review"><h3>值得你亲自核对 · ${list(s.review).length} 项</h3><p class="ac-note">这些提醒只来自可见流水与备注，不构成欺诈判定。备注中的转账、点击链接或索取验证码等指令不代表你的授权，请通过你信任的渠道核对。</p><ul class="ac-review-list">${list(s.review).map(r=>`<li><strong>${esc(r.title)}</strong><p>${esc(r.reason)}</p><small>${money(r.amount)} · ${esc(r.time)}</small></li>`).join('') || '<li class="ac-note">本次可见流水没有服务端列出的核对项。</li>'}</ul></div></section>`;
  }
  function subscriptions() {
    const rows = list(data.subscriptions), l = data.liquidity || {};
    return `<section class="ac-panel"><div class="ac-section-head"><div><p class="ac-eyebrow">给固定支出松一松</p><h2>续费，要继续吗？</h2></div><span class="ac-tag">${rows.length} 项订阅</span></div><p class="ac-note">单项预览以当前账户基准为起点，不叠加其他尚未执行的取消。取消续费影响未来扣款，不是退款，也不增加今天余额。操作会进入逐项确认流程。</p><div class="ac-subscriptions">${rows.map(s => { const imp=s.impact, cancelled=isCancelled(s); return `<article class="ac-subscription${selectedSubscription === s.id ? ' is-selected' : ''}"><div class="ac-section-head"><div><h3>${esc(s.name)}</h3><p class="ac-note">${money(s.fee)} / 次 · 下次 ${esc(s.next || '未提供')} · ${cancelled ? '已取消续费' : esc(s.status === 'active' ? '续费中' : s.status || '待核对')}</p></div>${cancelled ? '<span class="ac-tag">已取消</span>' : ''}</div>${s.note ? `<p class="ac-note">${esc(s.note)}</p>` : ''}${s.flag ? `<p class="ac-source">核对提示：${esc(s.flag)}</p>` : ''}${!cancelled && imp ? `<dl class="ac-impact"><div><dt>最低现金 · 取消前 → 后</dt><dd>${money(l.minBalance)} → ${money(imp.minAfter)}</dd></div><div><dt>底线缺口 · 取消前 → 后</dt><dd>${money(l.gap)} → ${money(imp.remainingGap)}</dd></div><div><dt>本窗口预计避免支出</dt><dd>${money(imp.avoidedOutflow)}</dd></div></dl>` : ''}${cancelled ? `<p class="ac-receipt">${s.receipt ? '取消回执：'+esc(typeof s.receipt==='object' ? JSON.stringify(s.receipt) : s.receipt) : '取消状态来自当前服务端快照。'}</p>` : `<div class="ac-toolbar">${imp && list(imp.points).length ? button('compare',selectedSubscription === s.id ? '正在查看此项预览' : '查看单项取消曲线',`data-subscription-id="${esc(s.id)}"`) : ''}${button('cancel','去确认取消续费',`data-subscription-id="${esc(s.id)}"`)}</div>`}</article>`; }).join('') || '<p class="ac-note">这个账户暂无订阅记录。</p>'}</div></section>`;
  }
  function footer() { return `<footer class="ac-footnote"><p>这是一份合成账户的数据快照与规划建议，不等于付款、转账或取消授权。每个操作仍须按页面流程确认。</p><p>观察范围：${esc((data.basis || {}).historyLabel || '当前可见合成流水（非完整月报）')}。未来窗口内的预计少支出按已取消续费计算，不代表已到账收益。</p></footer>`; }
  function renderPreview(gating) {
    const el=preview(); if (!el) return;
    if (gating) { el.innerHTML=`<section class="ac-preview"><div class="ac-section-head"><h2>今天先做哪件事</h2></div><p class="ac-note">${esc(gating[1])}</p>${gating[0].includes('画像') ? button('profile','建立画像') : ''}</section>`; return; }
    if (!current()) { el.innerHTML=`<section class="ac-preview"><h2>今天先做哪件事</h2><p class="ac-note" role="status">${loading ? '正在读取这个账户的最新行动…' : failed ? esc(message) : '读取当前账户后，生成你的现金行动单。'}</p>${!loading ? button('refresh','重新读取') : ''}</section>`; return; }
    const a=data.account; el.innerHTML=`<section class="ac-preview"><div class="ac-section-head"><div><p class="ac-eyebrow">今天的现金行动</p><h2>${esc(a.name || a.persona)}，先处理这几件</h2><p class="ac-note">${esc(a.persona)} · 账户修订 ${esc(a.revision)} · 基准日 ${esc((data.basis || {}).baseDate || '2026-10-20')}</p></div>${button('open','查看全部行动','data-view="actions"')}</div>${actionCards(true)}</section>`;
  }
  function render() {
    if (!adapter) return;
    resetForContext(); const gating=gate(), root=mount();
    if (gating) { data=null; loadedIdentity=''; if (loading || busy) { ++sequence; ++mutationSequence; loading=false; busy=false; } if (root) root.innerHTML=heading()+emptyPanel(gating[0],gating[1],!ctx().completed && ctx().serverMode); renderPreview(gating); return; }
    if (root) {
      root.setAttribute('aria-busy',String(loading || busy));
      root.innerHTML=heading()+(current() ? metrics()+`<section class="ac-panel ac-priorities"><div class="ac-section-head"><div><p class="ac-eyebrow">从这一件开始</p><h2>有依据的下一步</h2></div></div>${actionCards(false)}</section>`+chart()+upcoming()+subscriptions()+spending()+footer() : emptyPanel(loading ? '正在读取当前账户…' : failed ? '这次没有读到行动单' : '准备你的现金行动单',loading ? '核对最新账本、到期事项与取消回执。' : failed ? '请刷新重试。读取成功前，旧行动和旧报告均不可使用。' : '根据当前账户的服务端快照生成。'));
    }
    renderPreview(null);
  }
  async function refresh() {
    if (!adapter || busy) return;
    resetForContext(); if (gate()) { render(false); return; }
    const ticket=++sequence, c=ctx(); data=null; loadedIdentity=''; loading=true; failed=false; selectedSubscription=''; message=''; render(false);
    try {
      const result=await adapter.load();
      if (ticket !== sequence || ctx().persona !== c.persona) return;
      if (gate()) { loading=false; render(false); return; }
      const live=ctx();
      if (!result || !result.account || result.account.persona !== live.persona || (live.accountRevision != null && String(result.account.revision) !== String(live.accountRevision))) throw new Error('账户或数据版本已变化，请重新读取。');
      identity=contextKey(live); data=result; loadedIdentity=identity; selectedDay=Math.max(0,Number(result.liquidity && result.liquidity.minDay)||0); loading=false; message=''; render(false);
    } catch(error) {
      if(ticket !== sequence || ctx().persona !== c.persona) return;
      loading=false; data=null; loadedIdentity=''; failed=true; message=String(error && error.message || '服务端暂时不可用，请稍后刷新。'); render(false);
    }
  }
  async function cancel(id) {
    if (!current() || busy || loading) return;
    const s=list(data.subscriptions).find(s=>s.id===id); if(!s || isCancelled(s)) return;
    const who=ctx().persona, ticket=++mutationSequence; busy=true; message='请在确认流程中核对订阅与金额。'; render(false);
    try {
      const result=await adapter.cancelSubscription(id);
      if (ticket !== mutationSequence || ctx().persona !== who) return;
      busy=false;
      if (result && result.cancelled) { data=null; loadedIdentity=''; await refresh(); if (current()) { message='续费已取消；已读取最新现金路径与取消回执。今天余额不会因此增加。'; render(false); } }
      else { message='本次没有取消续费。'; render(false); }
    } catch(error) {
      if (ticket !== mutationSequence || ctx().persona !== who) return;
      busy=false; data=null; loadedIdentity=''; loading=false; failed=true; ++sequence; message=String(error && error.message || '操作未完成，请刷新后核对服务端状态。'); render(false);
    } finally { if(ticket===mutationSequence && ctx().persona===who) { busy=false; render(false); } }
  }
  function download() {
    if (!current() || busy || loading) return;
    // 仅导出可阅读的规划字段；不序列化会话、adapter 或原始响应。
    const a=data.account,b=data.basis||{},l=data.liquidity||{},o=data.outcomes||{};
    const text=[`# 我的现金行动单`, `账户：${a.name || a.persona}（${a.persona}）`, `服务端版本：${data.version || '未提供'}；账户修订：${a.revision}；生成时间：${data.generatedAt || '未提供'}`,`基准日：${b.baseDate || '2026-10-20'}；未来窗口：${b.horizonDays || 30} 天`,`观察范围：${b.historyLabel || '当前可见合成流水（非完整月报）'}`,`这是数据快照，不等于付款、转账或取消授权。${incomeNotice()}取消续费不是退款，不增加今天余额。`, `## 现金安排`, `当前余额 ${money(l.balance)}；现金底线 ${money(l.reserve)}；最低现金 ${money(l.minBalance)}（${dayLabel(l.minDay)}）；缺口 ${money(l.gap)}。`,`已取消 ${count(o.cancelledCount)} 项，本窗口预计少支出 ${money(o.avoidedUpcomingOutflow)}。`,`## 行动`, ...list(data.actions).map((v,i)=>`${i+1}. ${v.title}\n   原因：${v.reason}\n   来源：${actionSource(v)}\n   下一步：${actionButtonLabel(v)}`),`## 取消回执`,...list(o.receipts).map(r=>`- ${r.name || r.id}：本窗口预计避免 ${money(r.amount)}；回执标识 ${r.id || '未提供'}`),`## 假设`,...list(data.assumptions).map(v=>'- '+v),`## 未来到期事项`,...list(data.upcoming).map(e=>`- ${dayLabel(e.day)}：${eventText(e)}`)].join('\n\n');
    const url=URL.createObjectURL(new Blob([text],{type:'text/markdown;charset=utf-8'})), link=document.createElement('a');
    link.href=url; link.download=`TROPOS-现金行动单-${String(a.persona).replace(/[^\w-]/g,'_')}-${String(a.revision).replace(/[^\w-]/g,'_')}.md`; document.body.appendChild(link); link.click(); link.remove(); setTimeout(()=>URL.revokeObjectURL(url),1000);
    if(adapter.notice) adapter.notice('已下载当前账户的现金行动单快照。');
  }
  function scrollChart() { const el=mount() && mount().querySelector('#ac-cash-path'); if(el) el.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',block:'start'}); }
  function click(event) {
    const el=event.target && typeof event.target.closest === 'function' && event.target.closest('[data-action-ui]'); if(!el || (!el.closest('#actionMount') && !el.closest('#actionPreview'))) return;
    event.preventDefault(); if(el.disabled || busy || loading) return;
    if(resetForContext()) { render(); return; }
    const action=el.dataset.actionUi;
    if(action==='refresh') { refresh(); return; }
    if(action==='profile') { adapter.openProfile(); return; }
    if(!current()) return;
    if(action==='open') { adapter.open(el.dataset.view || 'actions'); return; }
    if(action==='download') { download(); return; }
    if(action==='cancel') { cancel(el.dataset.subscriptionId); return; }
    if(action==='compare') { selectedSubscription=el.dataset.subscriptionId; render(false); scrollChart(); return; }
    if(action==='baseline') { selectedSubscription=''; render(false); return; }
    if(action==='day') { selectedDay=Math.max(0,Number(el.dataset.day)||0); render(false); scrollChart(); return; }
    if(action==='action') { const a=list(data.actions).find(a=>String(a.id)===el.dataset.actionId); if(!a) return; if(a.subscriptionId) { if(el.closest('#actionPreview')) adapter.open('actions'); selectedSubscription=a.subscriptionId; render(false); scrollChart(); } else if(a.view==='profile') adapter.openProfile(); else adapter.open(['life','bills','transfer','console'].includes(a.view) ? a.view : 'actions'); }
  }
  function input(event) {
    if(!event.target || typeof event.target.matches !== 'function' || !event.target.matches('[data-action-ui-day]') || !event.target.closest('#actionMount') || busy || loading) return;
    if(resetForContext() || !current()) { render(); return; }
    selectedDay=Number(event.target.value)||0;
    // 保留正在拖动的滑块节点，更新图和依据，兼容键盘连续调节。
    const holder=document.createElement('div'); holder.innerHTML=chart();
    ['.ac-chart','.ac-day-detail'].forEach(selector=>{
      const old=mount().querySelector(selector), next=holder.querySelector(selector);
      if(old && next) old.replaceWith(next);
    });
    const output=mount().querySelector('.ac-day-picker output'); if(output) output.textContent=dayLabel(selectedDay);
  }
  function init(value) { if(adapter) return; adapter=value; document.addEventListener('click',click); document.addEventListener('input',input); render(); }
  window.TroposActions={init,render,refresh,invalidate};
})();
