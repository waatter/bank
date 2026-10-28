'use strict';

const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../assets/chat-ui.js'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, '../assets/chat-ui.css'), 'utf8');
const decode = value => value.replace(/&(amp|lt|gt|quot|#39);/g, (_, entity) => ({amp:'&', lt:'<', gt:'>', quot:'"', '#39':"'"}[entity]));

// Small controlled DOM for the actual renderer and delegated listener. No browser,
// dependencies, network, provider keys or financial service are used by these tests.
class Element {
  constructor(tag, attrs = {}) {
    this.tagName = tag.toUpperCase(); this.attrs = {...attrs}; this.children = []; this.parentElement = null;
    this.nodeType = 1; this.listeners = new Map(); this._html = ''; this.disabled = Object.hasOwn(attrs, 'disabled');
  }
  getAttribute(name) { return Object.hasOwn(this.attrs, name) ? this.attrs[name] : null; }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  removeAttribute(name) { delete this.attrs[name]; }
  append(child) { child.parentElement = this; this.children.push(child); }
  contains(child) { return child === this || this.children.some(node => node.contains(child)); }
  matches(selector) {
    const match = selector.match(/^(\w+)?\[([\w-]+)(?:="([^"]*)")?\]$/);
    if (!match) throw new Error('Unsupported test selector: ' + selector);
    return (!match[1] || this.tagName === match[1].toUpperCase()) && this.getAttribute(match[2]) != null &&
      (match[3] == null || this.getAttribute(match[2]) === match[3]);
  }
  closest(selector) { return this.matches(selector) ? this : this.parentElement && this.parentElement.closest(selector); }
  querySelectorAll(selector) {
    return this.children.flatMap(child => [...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)]);
  }
  addEventListener(type, handler) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type).add(handler);
  }
  removeEventListener(type, handler) { this.listeners.get(type)?.delete(handler); }
  async click(target) {
    await Promise.all([...(this.listeners.get('click') || [])].map(handler => handler({target})));
  }
  set innerHTML(html) {
    this.children.forEach(child => { child.parentElement = null; }); this.children = []; this._html = html;
    const stack = [this];
    for (const match of html.matchAll(/<(\/?)([a-z][\w-]*)([^>]*)>/gi)) {
      if (match[1]) { assert.equal(stack.pop().tagName, match[2].toUpperCase(), 'balanced generated HTML'); continue; }
      const attrs = {};
      for (const attr of match[3].matchAll(/([\w-]+)(?:="([^"]*)")?/g)) attrs[attr[1]] = decode(attr[2] || '');
      const child = new Element(match[2], attrs); stack.at(-1).append(child); stack.push(child);
    }
    assert.equal(stack.length, 1, 'all generated elements close');
  }
  get innerHTML() { return this._html; }
}

function setup(callbacks = {}) {
  let now = Date.parse('2026-10-27T03:00:00Z'), timerId = 0;
  const timers = new Map(), root = new Element('main'), calls = [], forbidden = [];
  class Clock extends Date { static now() { return now; } }
  const context = {
    window:{}, Date:Clock,
    setTimeout:(fn, delay) => { timers.set(++timerId, {fn, at:now + delay}); return timerId; },
    clearTimeout:id => timers.delete(id),
    fetch:() => { forbidden.push('fetch'); throw new Error('UI must never call HTTP'); },
    localStorage:{setItem:() => { forbidden.push('storage'); }},
  };
  vm.runInNewContext(source, context, {filename:'chat-ui.js'});
  const ui = context.window.TroposChatUI;
  const config = {root,
    onAsk:prompt => calls.push(['ask', prompt]),
    onConfirmSubscription:(id, button) => calls.push(['confirm', id, button]),
    onRevokeSubscription:(id, button) => calls.push(['revoke', id, button]),
    onQuerySubscription:(id, button) => calls.push(['query', id, button]), ...callbacks};
  const dispose = ui.init(config);
  const proposal = overrides => ({id:'proposal-1', status:'pending_confirmation',
    subscription:{id:'video', name:'视频会员', fee:29, next:6, status:'active'}, accountRevision:7,
    expiresAt:(now + 60000) / 1000, confirmationToken:'secret-token-never-in-html',
    baseline:{balance:1000, minBalance:100, reserve:200, gap:100},
    impact:{avoidedOutflow:29, minAfter:129, minImprovement:29, remainingGap:71}, ...overrides});
  return {ui, root, calls, forbidden, proposal, dispose, config, timers,
    buttons:action => root.querySelectorAll('button[data-cd-act="' + action + '"]'),
    mount:html => { root.innerHTML = html; },
    advance:ms => {
      now += ms;
      for (const [id, timer] of [...timers]) if (timer.at <= now) { timers.delete(id); timer.fn(); }
    }};
}
const snapshot = {balance:1000, profile:{reserve:200}, cash:{min:129, free:-71}};
const attack = `"><img src=x onerror=evil()><script>evil()</script>&'`;
function assertEscaped(html) {
  assert.doesNotMatch(html, /<script|<img|onerror="|onclick=|data-act=/i);
  assert.ok(html.includes('&lt;img'));
  assert.ok(html.includes('&quot;'));
  assert.ok(html.includes('&#39;'));
  assert.ok(html.includes('&amp;'));
}

test('public API returns only appended cards and does not modify unrelated root content', () => {
  const h = setup(); h.mount('<p>父页面的对话</p>');
  assert.deepEqual(Object.keys(h.ui).sort(), ['init', 'renderResult', 'renderSubscriptionProposal', 'renderSubscriptionReceipt'].sort());
  for (const value of [undefined, null, {}, {reply:attack}, {plan:{amount:9999}}]) assert.equal(h.ui.renderResult(value), '');
  h.ui.renderResult({billDetail:{title:'账单', items:[]}});
  assert.equal(h.root.innerHTML, '<p>父页面的对话</p>'); assert.deepEqual(h.calls, []);
});

test('comparable options preserve per-occurrence fees and delegate the exact comparison prompt', async () => {
  const h = setup(), subId = 'video"<&\'测试';
  const html = h.ui.renderResult({subscriptionOptions:[{id:subId, name:'视频', fee:29.5, next:6, status:'active'},
    {id:'music', name:'音乐', fee:12, next:8, status:'active'}, {id:'done', name:'旧订阅', status:'cancelled'}]});
  assert.match(html, /¥29.5/); assert.match(html, /第 6 天/); assert.match(html, /已取消续费/);
  assert.doesNotMatch(html, /¥354|¥348|¥144/);
  h.mount(html); assert.equal(h.buttons('ask').length, 2);
  await h.root.click(h.buttons('ask')[0]); await h.root.click(h.buttons('ask')[1]);
  assert.deepEqual(h.calls, [['ask', '比较取消订阅「' + subId + '」的影响'], ['ask', '比较取消订阅「music」的影响']]);
});

test('focus shows the supplied impact, residual gap and no refund; prepare only asks', async () => {
  const h = setup(), p = h.proposal();
  const html = h.ui.renderResult({subscriptionFocus:{...p.subscription, impact:p.impact}, snapshot});
  for (const phrase of ['¥29', '¥129', '¥71', '¥1,000', '最低点改善', '仍存底线缺口', '今日不退款', '当前余额不会因此增加', '单独确认']) assert.ok(html.includes(phrase));
  h.mount(html); await h.root.click(h.buttons('ask')[0]);
  assert.deepEqual(h.calls, [['ask', '取消订阅「video」']]);
  assert.equal(h.buttons('confirm-subscription').length, 0);
});

test('cancelled focus cannot prepare another cancellation or promise a new saving', () => {
  const h = setup(), p = h.proposal();
  const html = h.ui.renderResult({subscriptionFocus:{...p.subscription, status:'cancelled', impact:p.impact, receipt:{id:'old', status:'cancelled', at:'today'}}, snapshot});
  h.mount(html); assert.equal(h.buttons('ask').length, 0); assert.match(html, /不再生成取消方案/);
  assert.doesNotMatch(html, /本次预计少支出/); assert.match(html, /回执编号/);
});

test('pending proposal has one exact ID container; token is never serialized; confirmation is a callback only', async () => {
  const h = setup(), p = h.proposal(), before = JSON.stringify(p);
  const html = h.ui.renderSubscriptionProposal(p);
  h.mount(html);
  assert.equal(h.root.querySelectorAll('[data-chat-proposal-id]').length, 1);
  assert.equal(h.root.querySelectorAll('[data-chat-proposal-id]')[0].getAttribute('data-chat-proposal-id'), p.id);
  assert.doesNotMatch(html, /secret-token|confirmationToken|<script|onclick=|data-act=/);
  assert.match(html, /另弹确认框/);
  const button = h.buttons('confirm-subscription')[0]; assert.ok(button);
  await h.root.click(button);
  assert.equal(h.calls.length, 1); assert.deepEqual(h.calls[0], ['confirm', p.id, button]);
  assert.equal(JSON.stringify(p), before, 'click cannot mutate account or proposal input');
  assert.deepEqual(h.forbidden, []);
});

test('every non-pending status, missing/invalid token or expired/invalid expiry suppresses confirmation', () => {
  const h = setup();
  const variants = [
    ...['stale', 'expired', 'revoked', 'executed', 'unknown'].map(status => ({status})),
    {confirmationToken:null}, {confirmationToken:''}, {confirmationToken:'  '}, {confirmationToken:{}},
    {expiresAt:0}, {expiresAt:'bad-date'}, {expiresAt:undefined}, {expiresAt:Date.parse('2026-10-26') / 1000},
    {expiresAt:Date.parse('2026-10-27T03:00:00Z') / 1000}, {canConfirm:false},
    {subscription:{id:'video', status:'cancelled'}}, {subscription:{name:'no id'}},
  ];
  variants.forEach((variant, i) => {
    const html = h.ui.renderSubscriptionProposal(h.proposal({id:'variant-' + i, ...variant}));
    assert.doesNotMatch(html, /data-cd-act="confirm-subscription"/, JSON.stringify(variant));
    h.mount(html); assert.equal(h.buttons('query-subscription').length, 1);
  });
});

test('Unix seconds, milliseconds and ISO expiry are supported without weakening the deadline check', () => {
  const h = setup();
  ['2026-10-27T03:01:00Z', Date.parse('2026-10-27T03:01:00Z'), Date.parse('2026-10-27T03:01:00Z') / 1000].forEach((expiresAt, i) => {
    h.mount(h.ui.renderSubscriptionProposal(h.proposal({id:'time-' + i, expiresAt})));
    assert.equal(h.buttons('confirm-subscription').length, 1);
  });
});

test('invalidated proposals can be prepared again only through onAsk and require separate confirmation', async () => {
  const h = setup();
  for (const status of ['stale', 'expired', 'revoked']) {
    const html = h.ui.renderSubscriptionProposal(h.proposal({id:status, status})); h.mount(html);
    assert.match(html, /重新准备后仍要单独确认/);
    await h.root.click(h.buttons('ask')[0]);
    assert.deepEqual(h.calls.at(-1), ['ask', '取消订阅「video」']);
  }
});

test('revoke and status query delegate proposal IDs and the actual buttons', async () => {
  const h = setup(); h.mount(h.ui.renderSubscriptionProposal(h.proposal()));
  const revoke = h.buttons('revoke-subscription')[0], query = h.buttons('query-subscription')[0];
  await h.root.click(revoke); await h.root.click(query);
  assert.deepEqual(h.calls, [['revoke', 'proposal-1', revoke], ['query', 'proposal-1', query]]);
  for (const status of ['stale', 'expired', 'revoked', 'executed']) {
    h.mount(h.ui.renderSubscriptionProposal(h.proposal({id:status, status})));
    assert.equal(h.buttons('query-subscription').length, 1);
    await h.root.click(h.buttons('query-subscription')[0]); assert.equal(h.calls.at(-1)[1], status);
    if (['revoked', 'executed'].includes(status)) assert.equal(h.buttons('revoke-subscription').length, 0);
  }
  assert.deepEqual(h.forbidden, []);
});

test('executed proposals expose receipt details and cannot be revived by a pending replay', async () => {
  const h = setup(), pending = h.proposal(); h.mount(h.ui.renderSubscriptionProposal(pending));
  const oldButton = h.buttons('confirm-subscription')[0];
  const html = h.ui.renderSubscriptionProposal({...pending, status:'executed', receipt:{id:'receipt-1', status:'cancelled', at:'2026-10-27T03:00:00Z'}});
  assert.equal(h.buttons('confirm-subscription').length, 0, 'existing cards are updated');
  assert.match(html, /receipt-1/); assert.match(html, /cancelled/); assert.match(html, /2026-10-27T03:00:00Z/);
  const replay = h.ui.renderSubscriptionProposal(pending); assert.doesNotMatch(replay, /data-cd-act="confirm-subscription"/);
  h.mount(replay); await h.root.click(oldButton); assert.deepEqual(h.calls, []);
  const injected = new Element('button', {'data-cd-act':'confirm-subscription'});
  h.root.querySelectorAll('[data-chat-proposal-id]')[0].append(injected);
  await h.root.click(injected); assert.deepEqual(h.calls, [], 'even a retained or reinserted button cannot replay confirmation');
});

test('terminal status updates refresh every old occurrence and prevent resurrection of the same ID', () => {
  for (const status of ['stale', 'expired', 'revoked']) {
    const h = setup(), p = h.proposal(), first = h.ui.renderSubscriptionProposal(p), second = h.ui.renderSubscriptionProposal(p);
    h.mount(first + second); assert.equal(h.buttons('confirm-subscription').length, 2);
    h.ui.renderSubscriptionProposal({...p, status}); assert.equal(h.buttons('confirm-subscription').length, 0);
    assert.doesNotMatch(h.ui.renderSubscriptionProposal(p), /data-cd-act="confirm-subscription"/);
    assert.match(h.ui.renderSubscriptionProposal({...p, id:'new-proposal'}), /data-cd-act="confirm-subscription"/);
  }
});

test('expiry removes live confirmation controls, and checks at click time without waiting for a timer', async () => {
  const h = setup(), p = h.proposal(); h.mount(h.ui.renderSubscriptionProposal(p));
  const oldButton = h.buttons('confirm-subscription')[0];
  h.advance(60000); assert.equal(h.buttons('confirm-subscription').length, 0);
  assert.match(h.root.querySelectorAll('[data-chat-proposal-id]')[0].innerHTML, /方案已过期/);
  await h.root.click(oldButton); assert.deepEqual(h.calls, []);
  const late = setup(); late.mount(late.ui.renderSubscriptionProposal(late.proposal()));
  const lateButton = late.buttons('confirm-subscription')[0];
  late.timers.clear(); late.advance(60000); await late.root.click(lateButton);
  assert.deepEqual(late.calls, []); assert.equal(late.buttons('confirm-subscription').length, 0);
});

test('a repeated click or duplicate card cannot open simultaneous confirmation flows', async () => {
  let resolve, count = 0;
  const h = setup({onConfirmSubscription:() => { count++; return new Promise(done => { resolve = done; }); }});
  const html = h.ui.renderSubscriptionProposal(h.proposal()); h.mount(html + html);
  const [first, second] = h.buttons('confirm-subscription');
  const pending = h.root.click(first); assert.equal(first.disabled, true);
  await h.root.click(first); await h.root.click(second); assert.equal(count, 1);
  resolve(false); await pending; assert.equal(first.disabled, false, 'cancelling the parent dialog can leave a valid proposal available');
  assert.deepEqual(h.forbidden, []);
});

test('a parent refresh during confirmation keeps replacement controls busy, then available on dialog cancellation', async () => {
  let resolve;
  const h = setup({onConfirmSubscription:() => new Promise(done => { resolve = done; })});
  const p = h.proposal(); h.mount(h.ui.renderSubscriptionProposal(p));
  const pending = h.root.click(h.buttons('confirm-subscription')[0]);
  h.mount(h.ui.renderSubscriptionProposal(p));
  assert.equal(h.buttons('confirm-subscription')[0].disabled, true);
  resolve(false); await pending;
  assert.equal(h.buttons('confirm-subscription')[0].disabled, false);
  assert.equal(h.buttons('revoke-subscription')[0].disabled, false);
  assert.equal(h.buttons('confirm-subscription')[0].getAttribute('aria-busy'), null);
});

test('a rejected parent callback releases controls without changing financial state', async () => {
  const h = setup({onConfirmSubscription:async () => { throw new Error('parent handled request failure'); }});
  h.mount(h.ui.renderSubscriptionProposal(h.proposal()));
  const b = h.buttons('confirm-subscription')[0];
  await assert.rejects(h.root.click(b), /parent handled request failure/);
  assert.equal(b.disabled, false); assert.equal(b.getAttribute('aria-busy'), null);
  assert.deepEqual(h.forbidden, []);
});

test('receipt displays actual record and latest cash gap without adding the fee to balance', async () => {
  const h = setup(), p = h.proposal(); h.mount(h.ui.renderSubscriptionProposal(p));
  const html = h.ui.renderSubscriptionReceipt({receipt:{id:'cancel-001', status:'cancelled', at:'today', proposalId:p.id}, snapshot,
    plan:{amount:9000, status:'pending_confirmation'}});
  assert.equal(h.buttons('confirm-subscription').length, 0, 'receipt invalidates pending controls');
  for (const phrase of ['实际取消记录', 'cancel-001', 'cancelled', 'today', '¥1,000', '¥129', '¥71', '当前余额不会因此增加']) assert.ok(html.includes(phrase));
  assert.doesNotMatch(html, /¥1,029|¥9,000|data-cd-act="confirm-subscription"/);
  h.mount(html); await h.root.click(h.buttons('ask')[0]);
  assert.deepEqual(h.calls, [['ask', '按现在重新安排资金']]);
});

test('gap is derived only from supplied cash facts; missing amounts never turn into invented zeros', () => {
  const h = setup(), p = h.proposal();
  const empty = h.ui.renderSubscriptionProposal({...p, id:'empty', baseline:{}, impact:{}, subscription:{id:'missing'}});
  assert.match(empty, /待核对/); assert.doesNotMatch(empty, /¥0(?:\D|$)/);
  for (const amount of [null, undefined, '', ' ', 'not-a-number', Infinity, NaN, false, [], {}]) {
    const html = h.ui.renderResult({subscriptionOptions:[{id:'x', name:'未知金额', fee:amount}]});
    assert.match(html, /待核对/); assert.doesNotMatch(html, /¥/);
  }
  assert.match(h.ui.renderSubscriptionReceipt({snapshot:{balance:0, profile:{reserve:200}, cash:{min:129}}}), /¥71/);
  assert.match(h.ui.renderSubscriptionReceipt({snapshot:{balance:0, profile:{reserve:0}, cash:{min:0, free:0}}}), /¥0/);
  assert.doesNotMatch(h.ui.renderSubscriptionReceipt({}), /¥0/);
});

test('empty options, empty bills and task explanations remain useful without creating actions', () => {
  const h = setup();
  for (const options of [[], null, {}]) {
    h.mount(h.ui.renderResult({subscriptionOptions:options})); assert.match(h.root.innerHTML, /暂无可比较的订阅/); assert.equal(h.buttons('ask').length, 0);
  }
  const html = h.ui.renderResult({billDetail:{title:'本周账单', items:[], summary:'本期无登记事项'}, taskState:{status:'waiting_condition', label:'等待到账', detail:'工资还没有到账，只说明，不执行。'}});
  assert.match(html, /暂无账单明细/); assert.match(html, /本期无登记事项/); assert.match(html, /waiting_condition/);
  assert.match(html, /role="status"/); h.mount(html); assert.equal(h.root.querySelectorAll('button[data-cd-act]').length, 0);
});

test('bill details preserve signed amounts and supplied dates without inventing totals', () => {
  const h = setup(), html = h.ui.renderResult({billDetail:{title:'到期事项',
    items:[{id:'rent', label:'房租', day:3, amount:-800, kind:'房租'},
      {id:'income', label:'登记收入', day:'2026-11-01', amount:100, kind:'收入'}], summary:'按登记收支符号显示'}});
  assert.match(html, /¥-800/); assert.match(html, /¥100/); assert.match(html, /第 3 天/);
  assert.match(html, /2026-11-01/); assert.match(html, /按登记收支符号显示/);
  assert.doesNotMatch(html, /¥700|¥900|data-cd-act=/);
});

test('all dynamic text including IDs, JSON summaries and receipt fields is escaped', async () => {
  const h = setup(), p = h.proposal({id:attack, subscription:{id:attack, name:attack, fee:attack, next:attack},
    accountRevision:attack, baseline:{balance:attack}, impact:{avoidedOutflow:attack}});
  const html = h.ui.renderResult({subscriptionOptions:[{id:attack, name:attack, fee:attack, next:attack, status:attack}],
    subscriptionFocus:{id:attack, name:attack, fee:attack, next:attack, note:attack, flag:attack}, snapshot:{balance:attack},
    subscriptionProposal:p, billDetail:{title:attack, items:[{id:attack, label:attack, day:attack, amount:attack, kind:attack}], summary:{text:attack}},
    taskState:{status:attack, label:attack, detail:attack}});
  assertEscaped(html); h.mount(html);
  assert.equal(h.root.querySelectorAll('[data-chat-proposal-id]')[0].getAttribute('data-chat-proposal-id'), attack);
  await h.root.click(h.buttons('ask')[0]); assert.deepEqual(h.calls[0], ['ask', '比较取消订阅「' + attack + '」的影响']);
  await h.root.click(h.buttons('confirm-subscription')[0]); assert.equal(h.calls.at(-1)[1], attack);
  assertEscaped(h.ui.renderSubscriptionReceipt({receipt:{id:attack, status:attack, at:attack}, snapshot:{balance:attack}}));
  assertEscaped(h.ui.renderSubscriptionProposal({...p, id:attack + '-done', status:'executed', receipt:{id:attack, status:attack, at:attack}}));
});

test('delegation ignores existing data-act, unknown actions, outside buttons and disabled controls', async () => {
  const h = setup(); h.mount(h.ui.renderSubscriptionProposal(h.proposal()));
  for (const attrs of [{'data-act':'cancel'}, {'data-cd-act':'transfer'}, {'data-cd-act':'cancelSubscription'}]) {
    const b = new Element('button', attrs); h.root.append(b); await h.root.click(b);
  }
  const outside = new Element('button', {'data-cd-act':'ask', 'data-cd-prompt':'outside'}); await h.root.click(outside);
  const button = h.buttons('confirm-subscription')[0]; button.disabled = true; await h.root.click(button);
  button.disabled = false; button.setAttribute('aria-disabled', 'true'); await h.root.click(button);
  assert.deepEqual(h.calls, []);
});

test('native buttons support nested targets and keyboard-generated click without duplicate listeners', async () => {
  const h = setup(); h.ui.init(h.config); h.ui.init(h.config);
  h.mount(h.ui.renderSubscriptionProposal(h.proposal()));
  const b = h.buttons('query-subscription')[0], nested = new Element('span'); b.append(nested);
  await h.root.click(nested); assert.equal(h.calls.length, 1);
  assert.deepEqual(h.calls[0], ['query', 'proposal-1', b]);
  for (const button of h.root.querySelectorAll('button[data-cd-act]')) assert.equal(button.getAttribute('type'), 'button');
  assert.doesNotMatch(h.root.innerHTML, /tabindex="-1"|onkeydown|onclick/);
  const dispose = h.ui.init(h.config); dispose(); await h.root.click(b); assert.equal(h.calls.length, 1);
  assert.equal(h.timers.size, 0);
});

test('root replacement removes old handlers and prevents old proposal IDs from authorizing a new root', async () => {
  const h = setup(); h.mount(h.ui.renderSubscriptionProposal(h.proposal())); const oldButton = h.buttons('confirm-subscription')[0];
  const nextRoot = new Element('main'); h.ui.init({...h.config, root:nextRoot});
  await h.root.click(oldButton); assert.deepEqual(h.calls, []);
  nextRoot.innerHTML = '<section data-chat-proposal-id="proposal-1"><button type="button" data-cd-act="confirm-subscription">旧按钮</button></section>';
  await nextRoot.click(nextRoot.querySelectorAll('button[data-cd-act]')[0]); assert.deepEqual(h.calls, []);
});

test('CSS scopes styles, provides at least 44px hit areas and a single column at 390px', () => {
  assert.match(css, /\.cd-card \.cd-button\{[^}]*min-height:44px/);
  assert.match(css, /\.cd-card \.cd-button\{[^}]*min-width:44px/);
  assert.match(css, /:focus-visible\{outline:3px/);
  assert.match(css, /@media\(max-width:540px\)\{[^\n]*grid-template-columns:minmax\(0,1fr\)/);
  assert.match(css, /overflow-wrap:anywhere/); assert.match(css, /var\(--accent,#3d7a73\)/);
  assert.doesNotMatch(css, /(?:^|\})\s*(?:body|button|h3|\*)\s*\{/m);
});
