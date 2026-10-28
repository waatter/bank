"""Persistent subscription drafts; cancellation requires an explicit scoped approval.

Amounts in the public contract are yuan, as in build_insights. That builder does
the counterfactual arithmetic in integer cents. Creating/revoking drafts never
changes account facts, and cancelling a renewal never credits today's balance.
"""
import json
import secrets
import time

from .insights import build_insights
from .service import BankError, uid


class SubscriptionService:
    """Share the bank's SQLite database and BEGIN IMMEDIATE write boundary.

    propose/get/revoke return {proposal, snapshot}. confirm also returns receipt,
    except simulate_timeout=True returns {status: 'UNKNOWN', proposalId} *after*
    commit. Recover that outcome with get(user_id, proposalId), never a new draft.
    Confirmation payload: {confirmation: True, confirmationToken: <original>}.
    expiresAt is Unix seconds; approval lifetime is 600 seconds.
    """

    def __init__(self, bank):
        self.bank = bank
        with bank._db(True) as db:
            db.execute('CREATE TABLE IF NOT EXISTS subscription_proposals('
                       'id TEXT PRIMARY KEY, user_id TEXT NOT NULL, content TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS subscription_proposal_keys('
                       'user_id TEXT NOT NULL, request_key TEXT NOT NULL, '
                       'sub_id TEXT NOT NULL, proposal_id TEXT NOT NULL, '
                       'PRIMARY KEY(user_id, request_key))')

    @staticmethod
    def _load(db, user_id, proposal_id):
        row = None
        if isinstance(proposal_id, str):
            row = db.execute('SELECT content FROM subscription_proposals WHERE user_id=? AND id=?',
                             (user_id, proposal_id)).fetchone()
        if row is None:
            raise BankError(404, 'PROPOSAL_NOT_FOUND', '没有找到本人的订阅取消草案。')
        return json.loads(row['content'])

    @staticmethod
    def _save(db, user_id, proposal):
        db.execute('UPDATE subscription_proposals SET content=? WHERE user_id=? AND id=?',
                   (json.dumps(proposal, ensure_ascii=False), user_id, proposal['id']))

    @staticmethod
    def _status(proposal, snapshot):
        # A committed receipt or revocation stays authoritative after expiry and
        # later account changes. Other statuses are derived without writing.
        if proposal['status'] in ('executed', 'revoked'):
            return proposal['status']
        if time.time() >= proposal['expiresAt']:
            return 'expired'
        active = any(sub['id'] == proposal['subscription']['id'] and sub['status'] == 'active'
                     for sub in snapshot['subscriptions'])
        if (snapshot['profile'].get('readConsent') is not True
                or snapshot['user']['revision'] != proposal['accountRevision'] or not active):
            return 'stale'
        return 'pending_confirmation'

    def _result(self, proposal, snapshot):
        public = dict(proposal, status=self._status(proposal, snapshot),
                      subscriptionId=proposal['subscription']['id'])
        # Keep the stored credential to authenticate executed replays, but never
        # return it for a terminal, expired, or otherwise non-actionable draft.
        if public['status'] != 'pending_confirmation':
            public.pop('confirmationToken', None)
        return {'proposal': public, 'snapshot': snapshot}

    def propose(self, user_id, sub_id, request_key=None):
        if request_key is not None and (not isinstance(request_key, str)
                                        or not 1 <= len(request_key) <= 128):
            raise BankError(400, 'INVALID_KEY', '订阅草案请求凭证须为 1–128 个字符。')
        if not isinstance(sub_id, str):
            raise BankError(404, 'SUBSCRIPTION_NOT_FOUND', '订阅不存在。')
        with self.bank._db(True) as db:
            self.bank._consent(db, user_id)
            if request_key is not None:
                saved = db.execute('SELECT sub_id,proposal_id FROM subscription_proposal_keys '
                                   'WHERE user_id=? AND request_key=?',
                                   (user_id, request_key)).fetchone()
                if saved:
                    if saved['sub_id'] != sub_id:
                        raise BankError(409, 'KEY_REUSED', '此请求凭证已用于另一项订阅，请勿混用。')
                    return self._result(self._load(db, user_id, saved['proposal_id']),
                                        self.bank._snapshot(db, user_id))
            snapshot = self.bank._snapshot(db, user_id)
            insights = build_insights(snapshot)
            sub = next((item for item in insights['subscriptions'] if item['id'] == sub_id), None)
            if sub is None:
                raise BankError(404, 'SUBSCRIPTION_NOT_FOUND', '订阅不存在。')
            if sub['status'] != 'active':
                raise BankError(409, 'SUBSCRIPTION_NOT_ACTIVE', '订阅已停止续费，无需再次生成取消草案。')
            proposal = {
                'id': uid('subscription_proposal'), 'status': 'pending_confirmation',
                'subscription': {key: sub[key] for key in ('id', 'name', 'fee', 'next')},
                'accountRevision': snapshot['user']['revision'], 'expiresAt': time.time() + 600,
                'confirmationToken': secrets.token_urlsafe(32),
                'baseline': {key: insights['liquidity'][key]
                             for key in ('balance', 'minBalance', 'reserve', 'gap')},
                'impact': {key: sub['impact'][key] for key in
                           ('avoidedOutflow', 'minAfter', 'minImprovement', 'remainingGap')},
            }
            db.execute('INSERT INTO subscription_proposals(id,user_id,content) VALUES(?,?,?)',
                       (proposal['id'], user_id, json.dumps(proposal, ensure_ascii=False)))
            if request_key is not None:
                db.execute('INSERT INTO subscription_proposal_keys(user_id,request_key,sub_id,proposal_id) '
                           'VALUES(?,?,?,?)', (user_id, request_key, sub_id, proposal['id']))
            return self._result(proposal, snapshot)

    def get(self, user_id, proposal_id):
        """Read one user's draft, deriving validity without updating any row."""
        with self.bank._db() as db:
            proposal = self._load(db, user_id, proposal_id)
            return self._result(proposal, self.bank._snapshot(db, user_id))

    def confirm(self, user_id, proposal_id, payload):
        self.bank._confirm(payload)  # Strict True; text or a model's answer cannot approve.
        with self.bank._db(True) as db:
            proposal = self._load(db, user_id, proposal_id)
            self.bank._consent(db, user_id)
            token = payload.get('confirmationToken')
            # Compare bytes so malformed/non-ASCII input is a stable BankError,
            # rather than a TypeError from compare_digest's ASCII-only str path.
            if (not isinstance(token, str)
                    or not secrets.compare_digest(token.encode('utf-8', errors='surrogatepass'),
                                                  proposal['confirmationToken'].encode('utf-8'))):
                raise BankError(403, 'INVALID_APPROVAL', '确认凭证与本次订阅取消草案不匹配。')
            snapshot = self.bank._snapshot(db, user_id)
            status = self._status(proposal, snapshot)
            if status == 'revoked':
                raise BankError(409, 'REVOKED', '此订阅取消草案已撤销，未执行取消。')
            if status == 'expired':
                raise BankError(409, 'APPROVAL_EXPIRED', '订阅取消草案已过期，请重新核验后确认。')
            if status == 'stale':
                raise BankError(409, 'STALE_APPROVAL', '账户、画像或订阅已变化，请重新核验后确认。')
            if status != 'executed':
                cancelled = self.bank._cancel_subscription(db, user_id, proposal['subscription']['id'], payload)
                proposal.update(status='executed', receipt=cancelled['receipt'])
                self._save(db, user_id, proposal)
                snapshot = cancelled['snapshot']
            result = dict(self._result(proposal, snapshot), receipt=proposal['receipt'])
        # Deliberately outside the context: UNKNOWN must describe a committed
        # operation, including when the original approval is retried.
        if payload.get('simulate_timeout') is True:
            return {'status': 'UNKNOWN', 'proposalId': proposal_id}
        return result

    def revoke(self, user_id, proposal_id):
        with self.bank._db(True) as db:
            proposal = self._load(db, user_id, proposal_id)
            if proposal['status'] == 'executed':
                raise BankError(409, 'ALREADY_EXECUTED', '已取消续费并取得回执，不能撤销为未执行草案。')
            proposal['status'] = 'revoked'
            self._save(db, user_id, proposal)
            return self._result(proposal, self.bank._snapshot(db, user_id))
