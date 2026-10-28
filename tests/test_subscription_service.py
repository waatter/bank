"""Subscription draft safety and SQLite concurrency; standard library only."""
import concurrent.futures
import contextlib
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from backend.insights import build_insights
from backend.service import BankError, BankService, cents
from backend.subscription_service import SubscriptionService


class SubscriptionServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'bank.sqlite'
        self.bank = BankService(self.path)
        self.service = SubscriptionService(self.bank)
        self.a = self.bank.session('A')['snapshot']['user']['id']
        self.c = self.bank.session('C')['snapshot']['user']['id']
        for user in (self.a, self.c):
            profile = self.bank.snapshot(user)['profile']
            self.bank.save_profile(user, dict(profile, readConsent=True))

    def draft(self, sub='s1', user=None, key=None):
        return self.service.propose(user or self.c, sub, key)['proposal']

    def test_public_subscription_id_alias_is_derived_in_every_status(self):
        for state in ('pending_confirmation', 'executed', 'revoked', 'expired', 'stale'):
            with self.subTest(state=state):
                # Fresh draft, same registered subscription. No stored schema or
                # caller-supplied alias becomes authority over the target.
                proposal = self.draft(key='alias-' + state)
                self.assertEqual(proposal['subscriptionId'], proposal['subscription']['id'])
                with self.bank._db() as db:
                    stored = json.loads(db.execute('SELECT content FROM subscription_proposals WHERE id=?',
                                                   (proposal['id'],)).fetchone()[0])
                self.assertNotIn('subscriptionId', stored)
                if state == 'executed':
                    result = self.service.confirm(self.c, proposal['id'], dict(self.approval(proposal), subscriptionId='s2'))
                    self.assertEqual(result['proposal']['subscriptionId'], 's1')
                    self.assertEqual(next(s for s in result['snapshot']['subscriptions'] if s['id'] == 's2')['status'], 'active')
                    self.change_sub(status='active')
                elif state == 'revoked':
                    self.service.revoke(self.c, proposal['id'])
                elif state == 'expired':
                    self.edit_record(proposal, expiresAt=0)
                elif state == 'stale':
                    self.edit_record(proposal, accountRevision=-1)
                current = self.service.get(self.c, proposal['id'])['proposal']
                self.assertEqual((current['status'], current['subscriptionId']), (state, 's1'))
                replay = self.service.propose(self.c, 's1', 'alias-' + state)['proposal']
                self.assertEqual(replay['subscriptionId'], current['subscriptionId'])
                self.assertEqual(replay['id'], proposal['id'])

    @staticmethod
    def approval(proposal, **overrides):
        return dict({'confirmation': True, 'confirmationToken': proposal['confirmationToken']}, **overrides)

    def error(self, status, code, call):
        with self.assertRaises(BankError) as caught:
            call()
        self.assertEqual((caught.exception.status, caught.exception.code), (status, code))
        self.assertTrue(caught.exception.message)

    def count(self, table, where='', values=()):
        with contextlib.closing(sqlite3.connect(self.path)) as db:
            return db.execute('SELECT COUNT(*) FROM ' + table + (' WHERE ' + where if where else ''),
                              values).fetchone()[0]

    def database_rows(self):
        with contextlib.closing(sqlite3.connect(self.path)) as db:
            tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            return {table: db.execute('SELECT * FROM ' + table + ' ORDER BY rowid').fetchall()
                    for table in tables}

    def change_consent(self, user, consent):
        # Directly withdraw consent without revising facts, to isolate the gate.
        with self.bank._db(True) as db:
            profile = self.bank._profile(db, user)
            profile['readConsent'] = consent
            db.execute('UPDATE profiles SET profile=? WHERE user_id=?', (json.dumps(profile), user))

    def edit_record(self, proposal, **updates):
        with self.bank._db(True) as db:
            row = db.execute('SELECT content FROM subscription_proposals WHERE id=?',
                             (proposal['id'],)).fetchone()
            content = dict(json.loads(row[0]), **updates)
            db.execute('UPDATE subscription_proposals SET content=? WHERE id=?',
                       (json.dumps(content), proposal['id']))

    def change_sub(self, **updates):
        with self.bank._db(True) as db:
            _, state = self.bank._user(db, self.c)
            state['subs'][0].update(updates)
            self.bank._save_state(db, self.c, state)

    def test_first_draft_has_no_account_or_authorization_side_effect(self):
        before = self.bank.snapshot(self.c)
        rows = self.database_rows()
        result = self.service.propose(self.c, 's1')
        self.assertEqual(result['snapshot'], before)
        self.assertEqual(self.bank.snapshot(self.c), before)
        after = self.database_rows()
        for table in rows:
            if table != 'subscription_proposals':
                self.assertEqual(after[table], rows[table], table)
        self.assertEqual(len(after['subscription_proposals']), 1)
        proposal = result['proposal']
        self.assertEqual(proposal['status'], 'pending_confirmation')
        self.assertEqual(proposal['accountRevision'], before['user']['revision'])
        self.assertEqual(set(proposal['subscription']), {'id', 'name', 'fee', 'next'})
        self.assertGreaterEqual(len(proposal['confirmationToken']), 32)
        self.assertNotIn('receipt', proposal)

    def test_impact_matches_insights_and_independently_recomputed_cent_path(self):
        for user in (self.a, self.c):
            snapshot = self.bank.snapshot(user)
            insights = build_insights(snapshot)
            for sub in snapshot['subscriptions']:
                with self.subTest(user=user, sub=sub['id']):
                    proposal = self.draft(sub['id'], user)
                    insight_sub = next(item for item in insights['subscriptions'] if item['id'] == sub['id'])
                    self.assertEqual(proposal['baseline'], {key: insights['liquidity'][key]
                                                           for key in ('balance', 'minBalance', 'reserve', 'gap')})
                    self.assertEqual(proposal['impact'], {key: insight_sub['impact'][key]
                                                         for key in ('avoidedOutflow', 'minAfter', 'minImprovement', 'remainingGap')})
                    points = [cents(point) for point in snapshot['cash']['pts']]
                    after = [point + (cents(sub['fee']) if day >= sub['next'] else 0)
                             for day, point in enumerate(points)]
                    self.assertEqual(cents(proposal['impact']['minAfter']), min(after))
                    self.assertEqual(cents(proposal['impact']['minImprovement']), min(after) - min(points))
                    self.assertEqual(cents(proposal['impact']['remainingGap']),
                                     max(0, cents(snapshot['profile']['reserve']) - min(after)))

    def test_cent_precision_and_current_minimum_need_not_improve_by_full_fee(self):
        self.change_sub(fee=21.37, next=4)
        proposal = self.draft()
        self.assertEqual(proposal['impact']['avoidedOutflow'], 21.37)
        before = self.bank.snapshot(self.c)
        result = self.service.confirm(self.c, proposal['id'], self.approval(proposal))
        self.assertEqual(cents(result['snapshot']['cash']['min']), cents(proposal['impact']['minAfter']))
        self.assertEqual(cents(result['snapshot']['cash']['pts'][4]) - cents(before['cash']['pts'][4]), 2137)
        late = self.draft('s3')
        self.assertEqual(late['impact']['avoidedOutflow'], 30)
        self.assertEqual(late['impact']['minImprovement'], 0)

    def test_missing_consent_cannot_propose_or_confirm_or_leak_token_on_get(self):
        proposal = self.draft()
        self.change_consent(self.c, False)
        before = self.database_rows()
        self.error(403, 'CONSENT_REQUIRED', lambda: self.draft('s2'))
        self.error(403, 'CONSENT_REQUIRED', lambda: self.service.confirm(self.c, proposal['id'], self.approval(proposal)))
        result = self.service.get(self.c, proposal['id'])
        self.assertEqual(result['proposal']['status'], 'stale')
        self.assertNotIn('confirmationToken', result['proposal'])
        self.assertEqual(self.database_rows(), before)

    def test_cross_user_get_confirm_revoke_are_isolated(self):
        proposal = self.draft()
        other = self.draft(user=self.a)
        before = self.database_rows()
        for operation in (lambda: self.service.get(self.a, proposal['id']),
                          lambda: self.service.confirm(self.a, proposal['id'], self.approval(proposal)),
                          lambda: self.service.revoke(self.a, proposal['id'])):
            self.error(404, 'PROPOSAL_NOT_FOUND', operation)
        self.error(403, 'INVALID_APPROVAL', lambda: self.service.confirm(self.c, proposal['id'], self.approval(other)))
        self.assertEqual(self.database_rows(), before)

    def test_confirmation_requires_literal_true_even_with_text_or_model_approval(self):
        proposal = self.draft()
        for payload in (None, [], '确认', {'confirmationToken': proposal['confirmationToken']},
                        *[self.approval(proposal, confirmation=value) for value in (False, 1, 'true', '确认', {'approved': True})],
                        {'text': '确认取消', 'model': {'confirmation': True}, 'confirmationToken': proposal['confirmationToken']}):
            with self.subTest(payload=payload):
                self.error(403, 'CONFIRMATION_REQUIRED', lambda: self.service.confirm(self.c, proposal['id'], payload))
        self.assertEqual(self.count('authorizations', "kind='cancel_subscription'"), 0)

    def test_bad_missing_or_other_draft_tokens_are_rejected(self):
        proposal, other = self.draft(), self.draft('s2')
        for token in (None, '', 'wrong', '中文凭证', '\ud800', 1, True, {}, [], other['confirmationToken']):
            with self.subTest(token=token):
                self.error(403, 'INVALID_APPROVAL', lambda: self.service.confirm(self.c, proposal['id'], self.approval(proposal, confirmationToken=token)))
        self.error(403, 'INVALID_APPROVAL', lambda: self.service.confirm(self.c, proposal['id'], {'confirmation': True}))
        self.assertEqual(self.count('authorizations', "kind='cancel_subscription'"), 0)

    def test_profile_or_account_revision_change_invalidates_draft(self):
        for kind in ('profile', 'account'):
            with self.subTest(kind=kind):
                proposal = self.draft()
                if kind == 'profile':
                    profile = self.bank.snapshot(self.c)['profile']
                    self.bank.save_profile(self.c, dict(profile, reserve=profile['reserve'] + 100))
                else:
                    self.bank.event(self.c, {'type': 'bill_added', 'event_key': 'new-bill'})
                before = self.database_rows()
                recovered = self.service.get(self.c, proposal['id'])['proposal']
                self.assertEqual(recovered['status'], 'stale')
                self.assertNotIn('confirmationToken', recovered)
                self.error(409, 'STALE_APPROVAL', lambda: self.service.confirm(self.c, proposal['id'], self.approval(proposal)))
                self.assertEqual(self.database_rows(), before)

    def test_inactive_subscription_without_revision_change_is_still_stale(self):
        proposal = self.draft()
        self.change_sub(status='cancelled')
        self.assertEqual(self.service.get(self.c, proposal['id'])['proposal']['status'], 'stale')
        self.assertNotIn('confirmationToken', self.service.get(self.c, proposal['id'])['proposal'])
        self.error(409, 'STALE_APPROVAL', lambda: self.service.confirm(self.c, proposal['id'], self.approval(proposal)))

    def test_expiry_at_exact_600_seconds_is_read_only_and_cannot_confirm(self):
        with patch('backend.subscription_service.time.time', return_value=1000):
            proposal = self.draft()
            self.assertEqual(proposal['expiresAt'], 1600)
        before = self.database_rows()
        with patch('backend.subscription_service.time.time', return_value=1599.999):
            self.assertIn('confirmationToken', self.service.get(self.c, proposal['id'])['proposal'])
        with patch('backend.subscription_service.time.time', return_value=1600):
            recovered = self.service.get(self.c, proposal['id'])['proposal']
            self.assertEqual(recovered['status'], 'expired')
            self.assertNotIn('confirmationToken', recovered)
            self.error(409, 'APPROVAL_EXPIRED', lambda: self.service.confirm(self.c, proposal['id'], self.approval(proposal)))
        self.assertEqual(self.database_rows(), before)

    def test_revoke_is_idempotent_and_does_not_cancel_or_change_ledger(self):
        proposal = self.draft()
        before = self.bank.snapshot(self.c)
        result = self.service.revoke(self.c, proposal['id'])
        self.assertEqual(result['proposal']['status'], 'revoked')
        self.assertNotIn('confirmationToken', result['proposal'])
        self.assertEqual(result['snapshot'], before)
        self.assertEqual(self.service.revoke(self.c, proposal['id']), result)
        self.assertEqual(self.service.get(self.c, proposal['id']), result)
        self.error(409, 'REVOKED', lambda: self.service.confirm(self.c, proposal['id'], self.approval(proposal)))
        self.assertEqual(self.count('authorizations', "kind='cancel_subscription'"), 0)

    def test_confirm_returns_receipt_without_refund_and_replays_latest_snapshot(self):
        proposal = self.draft()
        before = self.bank.snapshot(self.c)
        first = self.service.confirm(self.c, proposal['id'], self.approval(proposal))
        self.assertEqual(first['proposal']['status'], 'executed')
        self.assertEqual(first['proposal']['receipt'], first['receipt'])
        self.assertNotIn('confirmationToken', first['proposal'])
        self.assertEqual(first['snapshot']['balance'], before['balance'])
        self.assertEqual(first['snapshot']['user']['revision'], before['user']['revision'] + 1)
        self.assertEqual(first['snapshot']['cash']['min'], proposal['impact']['minAfter'])
        self.error(409, 'ALREADY_EXECUTED', lambda: self.service.revoke(self.c, proposal['id']))
        self.bank.event(self.c, {'type': 'bill_added', 'event_key': 'after-cancel'})
        self.edit_record(proposal, expiresAt=0)
        latest = self.bank.snapshot(self.c)
        second = self.service.confirm(self.c, proposal['id'], self.approval(proposal))
        self.assertEqual(second['receipt'], first['receipt'])
        self.assertEqual(second['snapshot'], latest)
        self.assertEqual(second['proposal']['status'], 'executed')
        self.error(403, 'CONFIRMATION_REQUIRED', lambda: self.service.confirm(self.c, proposal['id'], {}))
        self.error(403, 'INVALID_APPROVAL', lambda: self.service.confirm(self.c, proposal['id'], self.approval(proposal, confirmationToken='bad')))
        self.change_consent(self.c, False)
        self.error(403, 'CONSENT_REQUIRED', lambda: self.service.confirm(self.c, proposal['id'], self.approval(proposal)))
        self.assertEqual(self.count('authorizations', "kind='cancel_subscription'"), 1)

    def test_concurrent_confirmations_across_service_instances_authorize_once(self):
        proposal = self.draft()
        other = SubscriptionService(BankService(self.path))
        barrier = threading.Barrier(6)

        def execute(index):
            barrier.wait()
            service = self.service if index % 2 else other
            return service.confirm(self.c, proposal['id'], self.approval(proposal))['receipt']

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            receipts = list(pool.map(execute, range(6)))
        self.assertTrue(all(receipt == receipts[0] for receipt in receipts))
        self.assertEqual(self.count('authorizations', "kind='cancel_subscription'"), 1)
        self.assertEqual(self.bank.snapshot(self.c)['user']['revision'], proposal['accountRevision'] + 1)

    def test_distinct_drafts_cannot_both_pass_original_revision(self):
        proposals = [self.draft(), self.draft('s2')]
        barrier = threading.Barrier(2)

        def execute(proposal):
            barrier.wait()
            try:
                return self.service.confirm(self.c, proposal['id'], self.approval(proposal))
            except BankError as error:
                return error.code

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(execute, proposals))
        self.assertEqual(results.count('STALE_APPROVAL'), 1)
        self.assertEqual(self.count('authorizations', "kind='cancel_subscription'"), 1)

    def test_restart_get_preserves_pending_token_and_executed_receipt_read_only(self):
        pending, executed = self.draft(), self.draft('s2', key='restart-key')
        reopened = SubscriptionService(BankService(self.path))
        before = self.database_rows()
        self.assertEqual(reopened.get(self.c, pending['id'])['proposal'], pending)
        self.assertEqual(self.database_rows(), before)
        receipt = reopened.confirm(self.c, executed['id'], self.approval(executed))['receipt']
        reopened_again = SubscriptionService(BankService(self.path))
        before = self.database_rows()
        result = reopened_again.get(self.c, executed['id'])
        self.assertEqual(result['proposal']['receipt'], receipt)
        self.assertEqual(result['proposal']['status'], 'executed')
        self.assertNotIn('confirmationToken', result['proposal'])
        self.assertEqual(self.database_rows(), before)
        self.assertEqual(reopened_again.propose(self.c, 's2', 'restart-key'), result)

    def test_timeout_is_returned_after_real_commit_and_get_recovers_original_id(self):
        proposal = self.draft(key='timeout-key')
        unknown = self.service.confirm(self.c, proposal['id'], self.approval(proposal, simulate_timeout=True))
        self.assertEqual(unknown, {'status': 'UNKNOWN', 'proposalId': proposal['id']})
        # A separate connection/service can see the commit, with no new proposal.
        recovered = SubscriptionService(BankService(self.path)).get(self.c, unknown['proposalId'])
        self.assertEqual(recovered['proposal']['status'], 'executed')
        self.assertEqual(recovered['snapshot']['subscriptions'][0]['status'], 'cancelled')
        self.assertNotIn('confirmationToken', recovered['proposal'])
        retry = self.service.confirm(self.c, proposal['id'], self.approval(proposal))
        self.assertEqual(retry['receipt'], recovered['proposal']['receipt'])
        self.assertEqual(self.count('subscription_proposals'), 1)
        self.assertEqual(self.count('authorizations', "kind='cancel_subscription'"), 1)

    def test_failure_after_cancel_rolls_back_receipt_authorization_and_state(self):
        proposal = self.draft()
        before = self.database_rows()
        with patch.object(self.service, '_save', side_effect=RuntimeError('模拟保存失败')):
            with self.assertRaises(RuntimeError):
                self.service.confirm(self.c, proposal['id'], self.approval(proposal, simulate_timeout=True))
        self.assertEqual(self.database_rows(), before)
        self.assertEqual(self.service.get(self.c, proposal['id'])['proposal']['status'], 'pending_confirmation')
        self.assertEqual(self.service.confirm(self.c, proposal['id'], self.approval(proposal))['proposal']['status'], 'executed')

    def test_cancellation_uses_caller_transaction_without_opening_another(self):
        proposal = self.draft()
        original_db = self.bank._db
        with patch.object(self.bank, '_db', wraps=original_db) as contexts:
            self.service.confirm(self.c, proposal['id'], self.approval(proposal))
        self.assertEqual(contexts.call_count, 1)
        self.assertEqual(contexts.call_args.args, (True,))

    def test_outside_window_cancellation_never_claims_savings_or_refund(self):
        for day in (0, 31, 365):
            with self.subTest(day=day):
                # Restore only this synthetic row between independent cases.
                self.change_sub(next=day, status='active')
                before = self.bank.snapshot(self.c)
                proposal = self.draft()
                self.assertEqual(proposal['impact'], {
                    'avoidedOutflow': 0, 'minAfter': before['cash']['min'],
                    'minImprovement': 0, 'remainingGap': max(0, before['profile']['reserve'] - before['cash']['min'])})
                result = self.service.confirm(self.c, proposal['id'], self.approval(proposal))
                self.assertEqual(result['snapshot']['cash']['pts'], before['cash']['pts'])
                self.assertEqual(result['snapshot']['balance'], before['balance'])
                self.assertEqual(build_insights(result['snapshot'])['outcomes']['avoidedUpcomingOutflow'], 0)

    def test_request_key_validation_scoping_conflict_and_same_sub_idempotency(self):
        for key in ('', 'x' * 129, 0, True, {}, []):
            with self.subTest(key=key):
                self.error(400, 'INVALID_KEY', lambda: self.draft(key=key))
        first = self.draft(key='请求凭证')
        self.assertEqual(self.draft(key='请求凭证'), first)
        self.error(409, 'KEY_REUSED', lambda: self.draft('s2', key='请求凭证'))
        other = self.draft(user=self.a, key='请求凭证')
        self.assertNotEqual(first['id'], other['id'])
        for key in ('x', 'y' * 128):
            self.assertEqual(self.draft(key=key), self.draft(key=key))
        self.assertNotEqual(self.draft()['id'], self.draft()['id'])

    def test_request_key_retry_never_refreshes_expired_or_revoked_draft(self):
        expired = self.draft(key='expired-key')
        self.edit_record(expired, expiresAt=0)
        result = self.service.propose(self.c, 's1', 'expired-key')['proposal']
        self.assertEqual((result['id'], result['status']), (expired['id'], 'expired'))
        self.assertNotIn('confirmationToken', result)
        revoked = self.draft('s2', key='revoked-key')
        self.service.revoke(self.c, revoked['id'])
        result = self.service.propose(self.c, 's2', 'revoked-key')['proposal']
        self.assertEqual((result['id'], result['status']), (revoked['id'], 'revoked'))
        self.assertNotIn('confirmationToken', result)
        self.assertEqual(self.count('subscription_proposals'), 2)

    def test_concurrent_same_key_creates_one_draft_and_one_token(self):
        barrier = threading.Barrier(4)

        def propose(_):
            barrier.wait()
            return self.draft(key='same-key')

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            proposals = list(pool.map(propose, range(4)))
        self.assertTrue(all(proposal == proposals[0] for proposal in proposals))
        self.assertEqual(self.count('subscription_proposals'), 1)
        self.assertEqual(self.count('subscription_proposal_keys'), 1)

    def test_parallel_direct_cancellation_and_draft_confirm_remain_consistent(self):
        proposal = self.draft()
        before = self.bank.snapshot(self.c)
        barrier = threading.Barrier(2)

        def via_draft():
            barrier.wait()
            try:
                return self.service.confirm(self.c, proposal['id'], self.approval(proposal))
            except BankError as error:
                return error.code

        def direct():
            barrier.wait()
            return self.bank.cancel_subscription(self.c, 's1', {'confirmation': True})

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            draft_future, direct_future = pool.submit(via_draft), pool.submit(direct)
            draft_result, direct_result = draft_future.result(), direct_future.result()
        if isinstance(draft_result, str):
            self.assertEqual(draft_result, 'STALE_APPROVAL')
            self.assertEqual(self.service.get(self.c, proposal['id'])['proposal']['status'], 'stale')
        else:
            self.assertEqual(draft_result['receipt'], direct_result['receipt'])
        after = self.bank.snapshot(self.c)
        self.assertEqual(after['balance'], before['balance'])
        self.assertEqual(after['user']['revision'], before['user']['revision'] + 1)
        self.assertEqual(self.count('authorizations', "kind='cancel_subscription'"), 1)
        self.assertEqual(after['cash']['pts'][30] - before['cash']['pts'][30], proposal['subscription']['fee'])

    def test_direct_cancel_first_invalidates_draft_and_new_draft_is_rejected(self):
        proposal = self.draft()
        self.bank.cancel_subscription(self.c, 's1', {'confirmation': True})
        self.error(409, 'STALE_APPROVAL', lambda: self.service.confirm(self.c, proposal['id'], self.approval(proposal)))
        self.error(409, 'SUBSCRIPTION_NOT_ACTIVE', lambda: self.draft())
        self.error(404, 'SUBSCRIPTION_NOT_FOUND', lambda: self.draft('missing-sub'))
        self.error(404, 'PROPOSAL_NOT_FOUND', lambda: self.service.get(self.c, 'missing-proposal'))
        self.assertEqual(self.count('authorizations', "kind='cancel_subscription'"), 1)


if __name__ == '__main__':
    unittest.main()
