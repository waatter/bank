'use strict';
/* Isolated client regressions: no server, real network, model, or browser. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

const source = fs.readFileSync(path.join(__dirname, '../assets/backend-client.js'), 'utf8');
const CONVERSATIONS = 'tropos-api-conversations';
const SESSIONS = 'tropos-api-sessions';
const plain = value => JSON.parse(JSON.stringify(value));
const snapshot = persona => ({user: {id: 'demo_' + persona, persona}, balance: 20000});
const conversation = (persona, version = 1, id = 'chat_' + persona) => ({id, version});
const turnResult = (persona, version = 1, id) => ({
  conversation: conversation(persona, version, id), snapshot: snapshot(persona),
  proposal: {id: 'proposal_' + persona, status: 'pending_confirmation', confirmationToken: 'approval_' + persona},
});
const historyResult = (persona, version = 1, id) => ({
  conversation: conversation(persona, version, id), messages: [], currentResult: turnResult(persona, version, id),
});
const response = (body, status = 200) => ({ok: status >= 200 && status < 300, status, json: async () => body});

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => {resolve = yes; reject = no;});
  return {promise, resolve, reject};
}

function storage(conversations = {}, pendingTurns = {}) {
  return new Map([
    [SESSIONS, JSON.stringify({A: 'A-live', B: 'B-live', C: 'C-live'})],
    [CONVERSATIONS, JSON.stringify({conversations, pendingTurns})],
  ]);
}

function harness(t, saved = storage()) {
  const calls = [], queued = [], unexpected = [];
  let generatedKeys = 0;
  const context = vm.createContext({
    window: {},
    crypto: {randomUUID: () => 'turn-' + String(++generatedKeys).padStart(4, '0')},
    AbortSignal: {timeout: milliseconds => ({timeout: milliseconds})},
    sessionStorage: {
      getItem: key => saved.get(key) ?? null,
      setItem: (key, value) => saved.set(key, String(value)),
    },
    fetch: async (url, options) => {
      const call = {
        url, method: options.method, token: options.headers.Authorization,
        body: options.body === undefined ? undefined : JSON.parse(options.body),
      };
      calls.push(call);
      if (url === '/api/state') return response(snapshot(call.token.slice(7, 8)));
      if (!queued.length) {
        unexpected.push(call);
        throw new Error('Unexpected controlled fetch: ' + url);
      }
      const outcome = queued.shift();
      if (outcome instanceof Error) throw outcome;
      return outcome;
    },
  });
  vm.runInContext(source, context, {filename: 'backend-client.js'});
  const client = new context.window.TroposSandboxClient();
  t.after(() => {
    assert.deepEqual(unexpected, [], 'all requests must have a controlled response');
    assert.equal(queued.length, 0, 'all scripted responses must be consumed');
  });
  return {
    client, calls, saved,
    queue: outcome => queued.push(outcome),
    persisted: () => JSON.parse(saved.get(CONVERSATIONS)),
    generatedKeys: () => generatedKeys,
    posts: () => calls.filter(call => call.method === 'POST'),
  };
}

test('a validated account choice persists across reload for dialogue recovery', async t => {
  const h = harness(t);
  await h.client.select('B');
  assert.equal(h.saved.get('tropos-api-active-persona'), 'B');
  const refreshed = harness(t, h.saved);
  assert.equal(refreshed.client.lastPersona, 'B');
  assert.equal(refreshed.client.token, null);
});

test('agent generates a request key, persists it before POST and continues exact conversation ID/version', async t => {
  const h = harness(t);
  await h.client.select('A');
  const gate = deferred();
  h.queue(gate.promise);
  const first = h.client.agent('查看视频会员');
  const initial = h.posts()[0];
  assert.equal(initial.url, '/api/agent');
  assert.equal(initial.token, 'Bearer A-live');
  assert.deepEqual(initial.body, {message: '查看视频会员', requestKey: 'turn-0001'});
  assert.deepEqual(h.persisted().pendingTurns.A, initial.body);
  gate.resolve(response(turnResult('A', 3, 'original_conversation')));
  await first;
  assert.equal(h.persisted().pendingTurns.A, undefined);
  h.queue(response(turnResult('A', 4, 'original_conversation')));
  await h.client.agent('再看看云存储');
  assert.deepEqual(h.posts()[1].body, {
    message: '再看看云存储', requestKey: 'turn-0002',
    conversationId: 'original_conversation', conversationVersion: 3,
  });
  assert.equal(h.generatedKeys(), 2);
  assert.deepEqual(h.persisted().conversations.A, {id: 'original_conversation', version: 4});
});

test('network-unknown retry preserves original message, key and conversation version', async t => {
  const h = harness(t, storage({A: conversation('A', 7)}));
  await h.client.select('A');
  h.queue(new Error('offline after request may have committed'));
  await assert.rejects(h.client.agent('  请核对续费  '), /服务连接中断/);
  const original = h.posts()[0].body;
  assert.deepEqual(h.persisted().pendingTurns.A, original);
  h.queue(response(turnResult('A', 8)));
  await h.client.agent('  请核对续费  ');
  assert.deepEqual(h.posts()[1].body, original);
  assert.equal(original.message, '  请核对续费  ');
  assert.equal(original.conversationVersion, 7);
  assert.equal(h.generatedKeys(), 1);
  assert.equal(h.persisted().pendingTurns.A, undefined);
});

test('personal model test timeout is reported as a read-only failure', async t => {
  const h = harness(t);
  await h.client.select('A');
  h.queue(new Error('provider timeout'));
  await assert.rejects(
    h.client.testPersonalModel('qwen'),
    error => error.code === 'NETWORK_ERROR'
      && /只读服务连接中断/.test(error.message)
      && !/原交易状态/.test(error.message),
  );
  assert.deepEqual(h.posts()[0], {
    url: '/api/model-center/personal/providers/qwen/test', method: 'POST',
    token: 'Bearer A-live', body: {confirmation: true},
  });
});

test('a different message is blocked by TURN_UNRESOLVED without another request or key', async t => {
  const h = harness(t);
  await h.client.select('A');
  h.queue(new Error('offline'));
  await assert.rejects(h.client.agent('原消息'));
  const before = h.saved.get(CONVERSATIONS), count = h.calls.length;
  await assert.rejects(h.client.agent('另一条消息'), error => error.code === 'TURN_UNRESOLVED');
  assert.equal(h.calls.length, count);
  assert.equal(h.saved.get(CONVERSATIONS), before);
  assert.equal(h.generatedKeys(), 1);
});

test('a refreshed VM restores pending POST and retries its original key without generating one', async t => {
  const saved = storage({A: conversation('A', 2)});
  const original = harness(t, saved);
  await original.client.select('A');
  original.queue(new Error('connection lost'));
  await assert.rejects(original.client.agent('刷新前的消息'));
  const pending = original.persisted().pendingTurns.A;
  const refreshed = harness(t, saved);
  await refreshed.client.select('A');
  assert.deepEqual(plain(refreshed.client.pendingTurns.A), pending);
  await assert.rejects(refreshed.client.agent('刷新后的新消息'), error => error.code === 'TURN_UNRESOLVED');
  refreshed.queue(response(turnResult('A', 3)));
  await refreshed.client.agent(pending.message);
  assert.deepEqual(refreshed.posts()[0].body, pending);
  assert.equal(refreshed.generatedKeys(), 0);
  assert.equal(refreshed.persisted().pendingTurns.A, undefined);
});

for (const status of [400, 401, 403, 404, 409, 422, 429]) {
  test(`HTTP ${status} clears only this account's pending POST and propagates the server error`, async t => {
    const pendingB = {message: 'B 未决消息', requestKey: 'B-pending'};
    const h = harness(t, storage({}, {B: pendingB}));
    await h.client.select('A');
    h.queue(response({error: {code: 'TURN_REJECTED', message: '本次请求被明确拒绝'}}, status));
    await assert.rejects(h.client.agent('会被拒绝的消息'), error => error.status === status && error.code === 'TURN_REJECTED');
    assert.equal(h.persisted().pendingTurns.A, undefined);
    assert.deepEqual(h.persisted().pendingTurns.B, pendingB);
    h.queue(response(turnResult('A')));
    await h.client.agent('修正后的消息');
    assert.notEqual(h.posts()[0].body.requestKey, h.posts()[1].body.requestKey);
  });
}

for (const status of [500, 502, 503]) {
  test(`HTTP ${status} preserves the original pending POST instead of treating it as rejected`, async t => {
    const h = harness(t);
    await h.client.select('A');
    h.queue(response({error: {code: 'UNAVAILABLE', message: '结果暂时未知'}}, status));
    await assert.rejects(h.client.agent('请核对账单'), error => error.status === status);
    assert.deepEqual(h.persisted().pendingTurns.A, h.posts()[0].body);
    await assert.rejects(h.client.agent('不要重建请求'), error => error.code === 'TURN_UNRESOLVED');
    assert.equal(h.posts().length, 1);
  });
}

test('a malformed success response remains unresolved and retains the original POST', async t => {
  const h = harness(t);
  await h.client.select('A');
  h.queue({ok: true, status: 200, json: async () => {throw new SyntaxError('incomplete JSON');}});
  await assert.rejects(h.client.agent('原消息'), /服务返回格式异常/);
  assert.deepEqual(h.persisted().pendingTurns.A, h.posts()[0].body);
  h.queue(response({snapshot: snapshot('A')}));
  await assert.rejects(h.client.agent('原消息'), /办理记录格式异常/);
  assert.deepEqual(h.posts()[1].body, h.posts()[0].body);
  assert.deepEqual(h.persisted().pendingTurns.A, h.posts()[0].body);
});

test('recoverConversation uses GET only and does not clear or rewrite an unresolved POST', async t => {
  const pending = {message: '原未决消息', requestKey: 'original-key', conversationId: 'chat_A', conversationVersion: 2};
  const h = harness(t, storage({A: conversation('A', 2)}, {A: pending}));
  await h.client.select('A');
  h.queue(response(historyResult('A', 3)));
  const result = await h.client.recoverConversation();
  assert.equal(result.conversation.version, 3);
  assert.deepEqual(h.calls.at(-1), {url: '/api/conversations/chat_A', method: 'GET', token: 'Bearer A-live', body: undefined});
  assert.deepEqual(h.persisted().pendingTurns.A, pending);
  assert.equal(h.posts().length, 0);
  assert.equal(h.generatedKeys(), 0);
  h.queue(response(turnResult('A', 3)));
  await h.client.agent(pending.message);
  assert.deepEqual(h.posts()[0].body, pending, 'GET must not change the version of the unresolved POST');
});

test('recoverConversation without a known ID performs no request and preserves pending state', async t => {
  const pending = {message: '第一轮未知', requestKey: 'first-key'};
  const h = harness(t, storage({}, {A: pending}));
  await h.client.select('A');
  const calls = h.calls.length, persisted = h.saved.get(CONVERSATIONS);
  assert.deepEqual(plain(await h.client.recoverConversation()), {conversation: null, messages: [], currentResult: null});
  assert.equal(h.calls.length, calls);
  assert.equal(h.saved.get(CONVERSATIONS), persisted);
});

test('a late A POST after switching to B cannot install A conversation or change B identity', async t => {
  const h = harness(t, storage({A: conversation('A', 2), B: conversation('B', 9)}));
  await h.client.select('A');
  const gate = deferred();
  h.queue(gate.promise);
  const pending = h.client.agent('A 的续费问题');
  await h.client.select('B');
  const expected = h.saved.get(CONVERSATIONS);
  gate.resolve(response(turnResult('A', 3)));
  await assert.rejects(pending, /不属于当前账户/);
  assert.equal(h.client.persona, 'B');
  assert.equal(h.client.token, 'B-live');
  assert.equal(h.saved.get(CONVERSATIONS), expected);
  assert.equal(h.client.conversations.B.version, 9);
  assert.equal(h.posts()[0].token, 'Bearer A-live');
});

test('a late A history read after switching to B cannot replace B current conversation', async t => {
  const h = harness(t, storage({A: conversation('A', 2), B: conversation('B', 9)}));
  await h.client.select('A');
  const gate = deferred();
  h.queue(gate.promise);
  const pending = h.client.recoverConversation();
  await h.client.select('B');
  const expected = h.saved.get(CONVERSATIONS);
  gate.resolve(response(historyResult('A', 3)));
  await assert.rejects(pending, /账户已变化/);
  assert.equal(h.client.persona, 'B');
  assert.equal(h.client.token, 'B-live');
  assert.equal(h.saved.get(CONVERSATIONS), expected);
});

test('an incorrectly identified agent result is rejected and leaves the original POST unresolved', async t => {
  const h = harness(t, storage({A: conversation('A', 2)}));
  await h.client.select('A');
  h.queue(response(turnResult('B', 99)));
  await assert.rejects(h.client.agent('A 的问题'), /不属于当前账户/);
  assert.equal(h.client.persona, 'A');
  assert.equal(h.client.token, 'A-live');
  assert.equal(h.client.conversations.A.version, 2);
  assert.equal(h.persisted().conversations.B, undefined);
  assert.deepEqual(h.persisted().pendingTurns.A, h.posts()[0].body);
});

test('persistent conversations and unresolved keys are isolated across accounts and refreshes', async t => {
  const aPending = {message: 'A 的原消息', requestKey: 'a-original', conversationId: 'chat_A', conversationVersion: 2};
  const bPending = {message: 'B 的原消息', requestKey: 'b-original', conversationId: 'chat_B', conversationVersion: 5};
  const h = harness(t, storage({A: conversation('A', 2), B: conversation('B', 5)}, {A: aPending, B: bPending}));
  await h.client.select('B');
  h.queue(response(turnResult('B', 6)));
  await h.client.agent(bPending.message);
  assert.deepEqual(h.posts()[0].body, bPending);
  assert.equal(h.posts()[0].token, 'Bearer B-live');
  assert.deepEqual(h.persisted().pendingTurns.A, aPending);
  assert.equal(h.persisted().conversations.A.version, 2);
  const refreshed = harness(t, h.saved);
  await refreshed.client.select('A');
  refreshed.queue(response(turnResult('A', 3)));
  await refreshed.client.agent(aPending.message);
  assert.deepEqual(refreshed.posts()[0].body, aPending);
  assert.equal(refreshed.posts()[0].token, 'Bearer A-live');
  assert.equal(refreshed.persisted().conversations.B.version, 6);
  assert.equal(refreshed.persisted().pendingTurns.B, undefined);
  assert.equal(refreshed.generatedKeys(), 0);
});

test('proposal replies and GET recovery never automatically confirm, cancel, or create another draft', async t => {
  const h = harness(t);
  await h.client.select('A');
  h.queue(response(turnResult('A')));
  const result = await h.client.agent('考虑取消视频会员');
  assert.equal(result.proposal.status, 'pending_confirmation');
  const proposal = {...result.proposal, id: 'original/id ?'};
  h.queue(response({proposal, snapshot: snapshot('A')}));
  await h.client.subscriptionProposal(proposal.id);
  h.queue(response(historyResult('A')));
  await h.client.recoverConversation();
  assert.deepEqual(h.calls.map(call => [call.method, call.url]), [
    ['GET', '/api/state'], ['POST', '/api/agent'],
    ['GET', '/api/subscription-proposals/original%2Fid%20%3F'], ['GET', '/api/conversations/chat_A'],
  ]);
});

test('explicit subscription confirmation uses original proposal ID and exact token; timeout recovers by GET', async t => {
  const h = harness(t);
  await h.client.select('A');
  const proposal = {id: 'original/id ?', confirmationToken: 'original-token'};
  h.queue(response({status: 'UNKNOWN', proposalId: proposal.id}));
  const unknown = await h.client.confirmSubscription(proposal, true);
  assert.deepEqual(h.posts()[0], {
    url: '/api/subscription-proposals/original%2Fid%20%3F/confirm', method: 'POST', token: 'Bearer A-live',
    body: {confirmation: true, confirmationToken: 'original-token', simulate_timeout: true},
  });
  assert.equal(unknown.proposalId, proposal.id);
  h.queue(response({proposal: {...proposal, status: 'executed', receipt: {id: 'receipt-original'}}}));
  const recovered = await h.client.subscriptionProposal(unknown.proposalId);
  assert.equal(recovered.proposal.receipt.id, 'receipt-original');
  assert.equal(h.posts().length, 1, 'UNKNOWN must not automatically re-confirm or rebuild the request');
  h.queue(response({receipt: {id: 'receipt-original'}}));
  await h.client.confirmSubscription(proposal);
  assert.deepEqual(h.posts()[1].body, {confirmation: true, confirmationToken: 'original-token', simulate_timeout: false});
  assert.equal(h.posts()[1].url, h.posts()[0].url);
  assert.equal(h.generatedKeys(), 0);
});

test('explicit revokeSubscription addresses the original ID without calling cancellation or confirmation', async t => {
  const h = harness(t);
  await h.client.select('B');
  h.queue(response({proposal: {id: 'proposal/old', status: 'revoked'}}));
  await h.client.revokeSubscription('proposal/old');
  assert.deepEqual(h.posts(), [{
    url: '/api/subscription-proposals/proposal%2Fold/revoke', method: 'POST',
    token: 'Bearer B-live', body: {confirmation: true},
  }]);
  assert.equal(h.generatedKeys(), 0);
});

test('forgetConversation clears only the selected account and survives a fresh instance', async t => {
  const aPending = {message: 'A 未决', requestKey: 'a-key'};
  const bPending = {message: 'B 未决', requestKey: 'b-key'};
  const h = harness(t, storage({A: conversation('A', 2), B: conversation('B', 5)}, {A: aPending, B: bPending}));
  await h.client.select('A');
  const sessions = h.saved.get(SESSIONS), calls = h.calls.length;
  h.client.forgetConversation();
  assert.deepEqual(h.persisted(), {conversations: {B: conversation('B', 5)}, pendingTurns: {B: bPending}});
  assert.equal(h.saved.get(SESSIONS), sessions);
  assert.equal(h.calls.length, calls);
  assert.equal(h.client.persona, 'A');
  assert.equal(h.client.token, 'A-live');
  const refreshed = harness(t, h.saved);
  await refreshed.client.select('B');
  assert.deepEqual(plain(refreshed.client.pendingTurns.B), bPending);
  refreshed.client.forgetConversation();
  assert.deepEqual(refreshed.persisted(), {conversations: {}, pendingTurns: {}});
});

// Keep these assertions strict: stale responses must not regress current state.
// Failures identify production races, rather than accepting them as a contract.
test('late history GET must not roll back a newer successful conversation turn', async t => {
  const h = harness(t, storage({A: conversation('A', 1)}));
  await h.client.select('A');
  const gate = deferred();
  h.queue(gate.promise);
  const oldRead = h.client.recoverConversation().catch(error => ({error}));
  h.queue(response(turnResult('A', 2)));
  await h.client.agent('更新后的第二轮');
  gate.resolve(response(historyResult('A', 1)));
  await oldRead;
  assert.equal(h.client.conversations.A.version, 2, 'late GET must not replace version 2 with version 1');
  assert.equal(h.persisted().conversations.A.version, 2);
});

test('late duplicate POST must not clear the newer unresolved turn', async t => {
  const h = harness(t);
  await h.client.select('A');
  const firstGate = deferred(), duplicateGate = deferred(), nextGate = deferred();
  h.queue(firstGate.promise);
  const first = h.client.agent('第一轮');
  h.queue(duplicateGate.promise);
  const duplicate = h.client.agent('第一轮').catch(error => ({error}));
  assert.deepEqual(h.posts()[0].body, h.posts()[1].body);
  firstGate.resolve(response(turnResult('A', 1)));
  await first;
  h.queue(nextGate.promise);
  const next = h.client.agent('第二轮');
  const expected = h.posts()[2].body;
  duplicateGate.resolve(response(turnResult('A', 1)));
  await duplicate;
  const actual = h.persisted().pendingTurns.A;
  nextGate.resolve(response(turnResult('A', 2)));
  await next;
  assert.deepEqual(actual, expected, 'completion of the original key must not delete another key');
});

test('a late response after A to B to A must not overwrite the newer A conversation', async t => {
  const h = harness(t, storage({A: conversation('A', 1), B: conversation('B', 5)}));
  await h.client.select('A');
  const gate = deferred();
  h.queue(gate.promise);
  const oldRead = h.client.recoverConversation().catch(error => ({error}));
  await h.client.select('B');
  await h.client.select('A');
  h.queue(response(turnResult('A', 2)));
  await h.client.agent('切回 A 后的新消息');
  gate.resolve(response(historyResult('A', 1)));
  await oldRead;
  assert.equal(h.client.persona, 'A');
  assert.equal(h.client.token, 'A-live');
  assert.equal(h.client.conversations.A.version, 2, 'same cached token does not make an old response current');
  assert.equal(h.persisted().conversations.B.version, 5);
});

test('a late old response must not resurrect a forgotten conversation over its replacement', async t => {
  const h = harness(t, storage({A: conversation('A', 1)}));
  await h.client.select('A');
  const gate = deferred();
  h.queue(gate.promise);
  const oldRead = h.client.recoverConversation().catch(error => ({error}));
  h.client.forgetConversation();
  h.queue(response(turnResult('A', 1, 'replacement_A')));
  await h.client.agent('新会话首轮');
  assert.equal(h.posts()[0].body.conversationId, undefined);
  gate.resolve(response(historyResult('A', 1, 'chat_A')));
  await oldRead;
  assert.equal(h.client.conversations.A.id, 'replacement_A', 'old history must not resurrect the forgotten ID');
  assert.equal(h.persisted().conversations.A.id, 'replacement_A');
});
