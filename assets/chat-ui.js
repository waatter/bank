/* TROPOS conversation cards. Rendering and callbacks never execute a banking action. */
(function () {
  'use strict';

  let adapter = null;
  const proposals = new Map(), timers = new Map(), busy = new Map();
  const terminal = new Set(['stale', 'expired', 'revoked', 'executed']);
  const object = value => value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  const text = value => {
    if (value == null) return '';
    if (typeof value !== 'object') return String(value);
    try { return JSON.stringify(value); } catch (_) { return '待核对'; }
  };
  const esc = value => text(value).replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
  const number = value => {
    if (typeof value !== 'number' && typeof value !== 'string') return null;
    if (typeof value === 'string' && !value.trim()) return null;
    return Number.isFinite(Number(value)) ? Number(value) : null;
  };
  const money = value => number(value) == null ? '待核对' : '¥' + number(value).toLocaleString('zh-CN', {maximumFractionDigits:2});
  const id = value => typeof value === 'string' && value.trim() ? value : '';
  const cancelled = sub => ['cancelled', 'canceled'].includes(sub.status);
  const day = value => value == null || value === '' ? '待核对' : typeof value === 'number' ? '第 ' + value + ' 天' : text(value);
  const note = value => `<p class="cd-note">${esc(value)}</p>`;
  const metric = (label, value) => `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`;
  const metrics = rows => `<dl class="cd-metrics">${rows.map(row => metric(...row)).join('')}</dl>`;
  const card = (title, body) => `<section class="cd-card" aria-label="${esc(title)}"><h3 class="cd-title">${esc(title)}</h3>${body}</section>`;
  function button(action, label, attrs = '', primary = false) {
    return `<button type="button" class="cd-button${primary ? ' cd-button-primary' : ''}" data-cd-act="${action}"${attrs}>${esc(label)}</button>`;
  }
  function ask(label, prompt) {
    return button('ask', label, ` data-cd-prompt="${esc(prompt)}"`);
  }
  const prepare = subId => ask('重新准备取消方案', '取消订阅「' + subId + '」');
  function cashRows(snapshot) {
    const s = object(snapshot), cash = object(s.cash), profile = object(s.profile);
    const minimum = number(cash.min) == null ? number(cash.minBalance) : number(cash.min);
    let gap = number(cash.gap);
    if (gap == null && number(cash.free) != null) gap = Math.max(0, -number(cash.free));
    if (gap == null && minimum != null && number(profile.reserve) != null) gap = Math.max(0, number(profile.reserve) - minimum);
    return [['当前余额', money(s.balance)], ['最新最低现金', money(minimum)], ['现金底线', money(profile.reserve)], ['最新底线缺口', money(gap)]];
  }
  function impactRows(impact) {
    const i = object(impact);
    return [['本次预计少支出', money(i.avoidedOutflow)], ['取消后最低现金', money(i.minAfter)], ['最低点改善', money(i.minImprovement)], ['仍存底线缺口', money(i.remainingGap)]];
  }
  const refundNotice = () => note('取消续费影响未来扣款；今日不退款，当前余额不会因此增加。');
  function optionsCard(options) {
    const rows = Array.isArray(options) ? options : [];
    return card('先比较，再决定取消哪一项', note('以下按次列出登记费用与下次续费时间，不推算年度节省。') +
      `<ul class="cd-list">${rows.map(value => {
        const sub = object(value), subId = id(sub.id);
        return `<li><h4>${esc(sub.name || '未命名订阅')}</h4>${metrics([['每次费用', money(sub.fee)], ['下次续费', day(sub.next)], ['当前状态', cancelled(sub) ? '已取消续费' : sub.status === 'active' ? '续费中' : sub.status || '待核对']])}${subId && !cancelled(sub) ? ask('比较取消影响', '比较取消订阅「' + subId + '」的影响') : ''}</li>`;
      }).join('') || '<li class="cd-empty">暂无可比较的订阅。</li>'}</ul>`);
  }
  function focusCard(value, snapshot) {
    const sub = object(value), subId = id(sub.id), isCancelled = cancelled(sub);
    return card((sub.name || '订阅') + ' · 取消影响',
      `<span class="cd-tag">${isCancelled ? '已取消续费' : '单项预览 · 尚未执行'}</span>` +
      metrics([['每次费用', money(sub.fee)], ['下次续费', day(sub.next)]]) +
      metrics(cashRows(snapshot)) + (isCancelled ? note('这项订阅已取消，不再生成取消方案。') : metrics(impactRows(sub.impact))) +
      refundNotice() + (sub.note ? note(sub.note) : '') + (sub.flag ? note(sub.flag) : '') +
      (isCancelled && sub.receipt ? receiptDetails(sub.receipt) : '') +
      (subId && !isCancelled ? `<div class="cd-actions">${ask('准备取消方案', '取消订阅「' + subId + '」')}</div>` + note('这一步只准备方案，执行前仍要单独确认。') : ''));
  }
  function expiry(value) {
    const n = number(value);
    if (n != null) return n > 0 ? (n < 1e12 ? n * 1000 : n) : NaN;
    return typeof value === 'string' && value.trim() ? Date.parse(value) : NaN;
  }
  function effectiveStatus(p) {
    if (p.status !== 'pending_confirmation') return p.status;
    return Number.isFinite(p.deadline) && p.deadline <= Date.now() ? 'expired' : p.status;
  }
  function canConfirm(p) {
    return effectiveStatus(p) === 'pending_confirmation' && p.hasToken && p.canConfirm !== false &&
      p.id && id(p.subscription.id) && !cancelled(p.subscription) && Number.isFinite(p.deadline) && p.deadline > Date.now();
  }
  function receiptDetails(value) {
    const r = object(value);
    return `<div class="cd-receipt">${metrics([['回执编号', r.id || '待核对'], ['回执状态', r.status || '待核对'], ['记录时间', r.at || '待核对']])}</div>`;
  }
  function proposalBody(p) {
    const status = effectiveStatus(p), sub = p.subscription;
    const labels = {pending_confirmation:'待你核对', stale:'方案已失效', expired:'方案已过期', revoked:'方案已撤销', executed:'已执行'};
    let body = `<div class="cd-heading"><h3 class="cd-title">${esc(sub.name || '订阅')} · 取消方案</h3><span class="cd-tag">${esc(labels[status] || '状态待核对')}</span></div>`;
    body += metrics([['每次费用', money(sub.fee)], ['下次续费', day(sub.next)]]) +
      `<h4>取消前的账户依据</h4>${metrics([['当前余额', money(p.baseline.balance)], ['最低现金', money(p.baseline.minBalance)], ['现金底线', money(p.baseline.reserve)], ['底线缺口', money(p.baseline.gap)]])}` +
      `<h4>本次方案的预计影响</h4>${metrics(impactRows(p.impact))}` + refundNotice() +
      note('账户版本：' + (p.accountRevision == null ? '待核对' : text(p.accountRevision)) + ' · 有效期至：' + (Number.isFinite(p.deadline) ? new Date(p.deadline).toLocaleString('zh-CN', {timeZone:'Asia/Taipei', hour12:false}) + '（UTC+8）' : '待核对'));
    if (status === 'executed') body += receiptDetails(p.receipt) + note('本方案已执行，请以回执和最新账户快照为准。');
    else if (status === 'stale' || status === 'expired') body += note('原方案已不能确认。重新准备后仍要单独确认，才会执行取消。');
    else if (status === 'revoked') body += note('已保留订阅并撤销本方案。需要取消时，重新准备后仍要单独确认。');
    else if (!canConfirm(p)) body += note('方案凭证或有效期不完整，暂不能确认。请查询最新状态，重新准备后仍要单独确认。');
    else body += note('此按钮只进入核对流程；接下来会另弹确认框，单独确认后才取消这一项。');
    const actions = [];
    if (canConfirm(p)) actions.push(button('confirm-subscription', '核对并确认取消', busy.has(p.id) ? ' disabled aria-busy="true"' : '', true));
    if (status !== 'executed' && status !== 'revoked') actions.push(button('revoke-subscription', '保留订阅，撤销方案', busy.has(p.id) ? ' disabled' : ''));
    actions.push(button('query-subscription', '查询最新状态'));
    if (['stale', 'expired', 'revoked'].includes(status) && id(sub.id) && !cancelled(sub)) actions.push(prepare(sub.id));
    return body + `<div class="cd-actions">${actions.join('')}</div>`;
  }
  // Match IDs as text, never interpolate untrusted IDs into a CSS selector.
  function refreshProposal(p) {
    if (!adapter) return;
    adapter.root.querySelectorAll('[data-chat-proposal-id]').forEach(container => {
      if (container.getAttribute('data-chat-proposal-id') === p.id) container.innerHTML = proposalBody(p);
    });
  }
  function syncBusyControls(p) {
    if (!adapter) return;
    adapter.root.querySelectorAll('[data-chat-proposal-id]').forEach(container => {
      if (container.getAttribute('data-chat-proposal-id') !== p.id) return;
      ['confirm-subscription', 'revoke-subscription'].forEach(action => {
        container.querySelectorAll('button[data-cd-act="' + action + '"]').forEach(button => {
          button.disabled = busy.has(p.id) || (action === 'confirm-subscription' ? !canConfirm(p) : ['executed', 'revoked'].includes(effectiveStatus(p)));
          if (busy.has(p.id)) button.setAttribute('aria-busy', 'true');
          else button.removeAttribute('aria-busy');
        });
      });
    });
  }
  function scheduleExpiry(p) {
    if (timers.has(p.id)) { clearTimeout(timers.get(p.id)); timers.delete(p.id); }
    if (!canConfirm(p) || typeof setTimeout !== 'function') return;
    const timer = setTimeout(() => {
      timers.delete(p.id);
      if (proposals.get(p.id) !== p) return;
      if (p.deadline > Date.now()) { scheduleExpiry(p); return; }
      p.status = 'expired'; p.hasToken = false; refreshProposal(p);
    }, Math.min(p.deadline - Date.now(), 2147483647));
    timers.set(p.id, timer);
  }
  function renderSubscriptionProposal(value) {
    const raw = object(value), proposalId = id(raw.id);
    if (!proposalId) return card('取消方案待核对', note('缺少方案编号，无法确认或查询。请重新准备取消方案。'));
    let p = {id:proposalId, status:raw.status, subscription:{...object(raw.subscription)}, baseline:{...object(raw.baseline)},
      impact:{...object(raw.impact)}, accountRevision:raw.accountRevision, expiresAt:raw.expiresAt, deadline:expiry(raw.expiresAt),
      hasToken:Boolean(id(raw.confirmationToken)), canConfirm:raw.canConfirm, receipt:{...object(raw.receipt)}};
    const previous = proposals.get(p.id);
    // A replayed pending payload cannot revive a consumed or invalidated proposal ID.
    if (previous && ((previous.status === 'executed' && p.status !== 'executed') || (terminal.has(effectiveStatus(previous)) && p.status === 'pending_confirmation'))) p = previous;
    if (effectiveStatus(p) === 'expired') { p.status = 'expired'; p.hasToken = false; }
    proposals.set(p.id, p);
    refreshProposal(p);
    scheduleExpiry(p);
    return `<section class="cd-card cd-proposal" aria-label="订阅取消方案" data-chat-proposal-id="${esc(p.id)}">${proposalBody(p)}</section>`;
  }
  function renderSubscriptionReceipt(value) {
    const result = object(value), r = object(result.receipt), rawProposal = object(result.proposal);
    const proposalId = id(rawProposal.id) || id(r.proposalId);
    if (proposalId) {
      const previous = proposals.get(proposalId);
      if (previous || rawProposal.subscription) renderSubscriptionProposal({...previous, ...rawProposal, id:proposalId, status:'executed', receipt:r});
    }
    return card('订阅取消回执', `<h4>实际取消记录</h4>${receiptDetails(r)}${refundNotice()}<h4>最新资金摘要</h4>` +
      metrics(cashRows(result.snapshot)) + note('以本次返回的最新快照为准；少一次未来扣款，不代表现金缺口已经补齐。') +
      `<div class="cd-actions">${ask('按现在重新安排', '按现在重新安排资金')}</div>`);
  }
  function billCard(value) {
    const bill = object(value), rows = Array.isArray(bill.items) ? bill.items : [];
    return card(bill.title || '账单明细', `<ul class="cd-list">${rows.map(value => {
      const row = object(value);
      return `<li><h4>${esc(row.label || '待核对事项')}</h4>${metrics([['日期', day(row.day)], ['登记金额', money(row.amount)], ['类型', row.kind || '待核对']])}</li>`;
    }).join('') || '<li class="cd-empty">暂无账单明细。</li>'}</ul>` + (bill.summary != null ? note(bill.summary) : ''));
  }
  function renderResult(value) {
    const result = object(value), html = [];
    if (Object.prototype.hasOwnProperty.call(result, 'subscriptionOptions')) html.push(optionsCard(result.subscriptionOptions));
    if (result.subscriptionFocus) html.push(focusCard(result.subscriptionFocus, result.snapshot));
    if (result.subscriptionProposal) html.push(renderSubscriptionProposal(result.subscriptionProposal));
    if (result.billDetail) html.push(billCard(result.billDetail));
    if (result.taskState) {
      const task = object(result.taskState);
      html.push(card(task.label || '任务状态', `<div role="status">${note(task.status || '状态待核对')}${task.detail != null ? note(task.detail) : ''}</div>`));
    }
    return html.join('');
  }
  async function click(event) {
    const target = event.target && (event.target.nodeType === 3 ? event.target.parentElement : event.target);
    const button = target && typeof target.closest === 'function' ? target.closest('button[data-cd-act]') : null;
    if (!button || !adapter || !adapter.root.contains(button) || button.disabled || button.getAttribute('aria-disabled') === 'true') return;
    const current = adapter, action = button.getAttribute('data-cd-act');
    if (action === 'ask') {
      const prompt = button.getAttribute('data-cd-prompt');
      if (prompt && typeof current.onAsk === 'function') return current.onAsk(prompt);
      return;
    }
    const container = button.closest('[data-chat-proposal-id]');
    if (!container || !current.root.contains(container)) return;
    const p = proposals.get(container.getAttribute('data-chat-proposal-id'));
    if (!p) return;
    if (action === 'query-subscription') {
      if (typeof current.onQuerySubscription === 'function') return current.onQuerySubscription(p.id, button);
      return;
    }
    const confirm = action === 'confirm-subscription';
    if (!confirm && action !== 'revoke-subscription') return;
    if (busy.has(p.id)) return;
    if (confirm && !canConfirm(p)) { refreshProposal(p); return; }
    if (!confirm && ['executed', 'revoked'].includes(effectiveStatus(p))) return;
    const callback = confirm ? current.onConfirmSubscription : current.onRevokeSubscription;
    if (typeof callback !== 'function') return;
    const operation = {};
    busy.set(p.id, operation); syncBusyControls(p);
    try { await callback(p.id, button); }
    finally {
      button.removeAttribute('aria-busy');
      if (busy.get(p.id) === operation) {
        busy.delete(p.id);
        const latest = proposals.get(p.id);
        button.disabled = !latest || (confirm ? !canConfirm(latest) : ['executed', 'revoked'].includes(effectiveStatus(latest)));
        if (latest) syncBusyControls(latest);
      }
    }
  }
  function init(value) {
    if (!value || !value.root || typeof value.root.addEventListener !== 'function' || typeof value.root.querySelectorAll !== 'function') throw new TypeError('TroposChatUI.init requires a root element');
    if (adapter) {
      adapter.root.removeEventListener('click', click);
      if (adapter.root !== value.root) {
        timers.forEach(timer => clearTimeout(timer)); timers.clear(); proposals.clear(); busy.clear();
      }
    }
    adapter = {...value};
    adapter.root.addEventListener('click', click);
    const bound = adapter;
    return function dispose() {
      if (adapter !== bound) return;
      adapter.root.removeEventListener('click', click); adapter = null;
      timers.forEach(timer => clearTimeout(timer)); timers.clear(); proposals.clear(); busy.clear();
    };
  }
  window.TroposChatUI = {init, renderResult, renderSubscriptionProposal, renderSubscriptionReceipt};
})();
