"""Domain invariants for the persistent synthetic bank, no external dependencies."""
import concurrent.futures
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from backend.service import BankService, BankError


class BankServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'bank.sqlite'
        self.svc = BankService(self.path)
        self.a = self.svc.session('A')['snapshot']['user']['id']
        self.c = self.svc.session('C')['snapshot']['user']['id']

    def tearDown(self):
        self.tmp.cleanup()

    def consent(self, user):
        p = self.svc.snapshot(user)['profile']
        p['readConsent'] = True
        return self.svc.save_profile(user, p)

    def proposal(self, amount=100, user=None, to='goal', when='now'):
        return self.svc.propose_transfer(user or self.c, {'to': to, 'amount': amount, 'when': when})['proposal']

    def approve(self, proposal, **overrides):
        return dict(confirmation=True, confirmationToken=proposal['confirmationToken'], **overrides)

    def error(self, code, call):
        with self.assertRaises(BankError) as err:
            call()
        self.assertEqual(err.exception.code, code)

    def test_without_account_read_consent_no_proposal(self):
        self.error('CONSENT_REQUIRED', lambda: self.proposal())
        self.assertEqual(self.svc.snapshot(self.c)['balance'], 20000)

    def test_explicit_action_consent_and_content_token_required(self):
        self.consent(self.c)
        p = self.proposal()
        self.error('CONFIRMATION_REQUIRED', lambda: self.svc.confirm(self.c, p['id'], {}))
        self.error('INVALID_APPROVAL', lambda: self.svc.confirm(self.c, p['id'], {'confirmation': True}))
        self.assertEqual(self.svc.snapshot(self.c)['balance'], 20000)

    def test_cross_user_records_and_tokens_are_isolated(self):
        self.consent(self.a)
        self.consent(self.c)
        p = self.proposal()
        self.error('PROPOSAL_NOT_FOUND', lambda: self.svc.confirm(self.a, p['id'], self.approve(p)))
        result = self.svc.confirm(self.c, p['id'], self.approve(p, key='my-transfer'))
        self.error('TRANSACTION_NOT_FOUND', lambda: self.svc.transaction(self.a, 'my-transfer'))
        self.error('TRANSACTION_NOT_FOUND', lambda: self.svc.transaction(self.a, result['transaction']['id']))

    def test_target_transfer_checks_current_and_forecast_cash(self):
        self.consent(self.c)
        p = self.proposal(15000)
        self.error('TRANSFER_BLOCKED', lambda: self.svc.confirm(self.c, p['id'], self.approve(p)))
        self.assertEqual(self.svc.snapshot(self.c)['balance'], 20000)
        self.assertEqual(self.svc.snapshot(self.c)['goalFunded'], 3000)

    def test_profile_change_invalidates_previous_approval(self):
        self.consent(self.c)
        p = self.proposal()
        profile = self.svc.snapshot(self.c)['profile']
        profile['reserve'] += 100
        self.svc.save_profile(self.c, profile)
        self.error('STALE_APPROVAL', lambda: self.svc.confirm(self.c, p['id'], self.approve(p)))

    def test_account_event_invalidates_previous_approval(self):
        self.consent(self.c)
        p = self.proposal()
        self.svc.event(self.c, {'type':'bill_added', 'event_key':'new-bill'})
        self.error('STALE_APPROVAL', lambda: self.svc.confirm(self.c, p['id'], self.approve(p)))

    def test_concurrent_retries_post_exactly_once(self):
        self.consent(self.c)
        p = self.proposal(100)
        def execute(_):
            return self.svc.confirm(self.c, p['id'], self.approve(p, key='same-request'))['transaction']['id']
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            ids = list(pool.map(execute, range(8)))
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(self.svc.snapshot(self.c)['balance'], 19900)
        self.assertEqual(self.svc.snapshot(self.c)['goalFunded'], 3100)
        self.assertEqual(self.svc.transaction(self.c, 'same-request')['transaction']['id'], ids[0])
        self.assertEqual(self.svc.confirm(self.c, p['id'], self.approve(p, key='different-request'))['transaction']['id'], ids[0])

    def test_double_entry_balances_every_posted_transaction(self):
        self.consent(self.c)
        p = self.proposal(321.45)
        self.svc.confirm(self.c, p['id'], self.approve(p))
        self.svc.event(self.c, {'type':'salary_received', 'event_key':'pay'})
        with sqlite3.connect(self.path) as db:
            rows = db.execute('SELECT transaction_id,SUM(amount),COUNT(*) FROM entries GROUP BY transaction_id').fetchall()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(total == 0 and count == 2 for _, total, count in rows))

    def test_reopen_preserves_balance_receipt_and_session(self):
        self.consent(self.c)
        token = self.svc.session('C')['token']
        p = self.proposal()
        tx = self.svc.confirm(self.c, p['id'], self.approve(p))['transaction']
        reopened = BankService(self.path)
        self.assertEqual(reopened.authenticate(token), self.c)
        self.assertEqual(reopened.snapshot(self.c)['balance'], 19900)
        self.assertEqual(reopened.transaction(self.c, p['id'])['transaction']['receipt'], tx['receipt'])

    def test_salary_dedupes_different_keys_removes_forecast_and_waits_confirmation(self):
        self.consent(self.c)
        self.svc.event(self.c, {'type':'income_delayed', 'event_key':'delay'})
        self.assertEqual(self.svc.snapshot(self.c)['cash']['salaryDay'], 25)
        self.assertEqual(self.svc.snapshot(self.c)['cash']['daily'], 90)
        self.assertTrue(all(p['status']=='waiting_condition' for p in self.svc.snapshot(self.c)['plans']))
        first = self.svc.event(self.c, {'type':'salary_received', 'event_key':'income-one'})
        second = self.svc.event(self.c, {'type':'salary_received', 'event_key':'income-two'})
        self.assertEqual(first['event']['id'], second['event']['id'])
        s = second['snapshot']
        self.assertEqual(s['balance'], 34000)
        self.assertEqual(s['goalFunded'], 3000)
        self.assertTrue(all(p['status']=='pending_confirmation' for p in s['plans']))
        self.assertFalse(any(e.get('type')=='salary' for e in s['cash']['events']))
        self.assertEqual(s['cash']['pts'][10], 34000 - 90*10 - 3200 - 260 - 21)

    def test_cancel_subscription_updates_future_not_present_balance(self):
        self.consent(self.c)
        before = self.svc.snapshot(self.c)
        self.error('CONFIRMATION_REQUIRED', lambda: self.svc.cancel_subscription(self.c, 's1', {}))
        result = self.svc.cancel_subscription(self.c, 's1', {'confirmation': True})
        after = result['snapshot']
        self.assertEqual(after['balance'], before['balance'])
        self.assertEqual(after['cash']['pts'][4] - before['cash']['pts'][4], 21)
        self.assertEqual(after['cash']['pts'][30] - before['cash']['pts'][30], 21)
        self.assertEqual(result['receipt'], self.svc.cancel_subscription(self.c, 's1', {'confirmation': True})['receipt'])

    def test_money_parser_rejects_nonfinite_negative_and_overprecision(self):
        self.consent(self.c)
        for amount in ['NaN', 'Infinity', '-1', '1.001', True, None, {}, 0]:
            with self.subTest(amount=amount):
                self.error('INVALID_AMOUNT', lambda: self.proposal(amount))

    def test_expected_income_cannot_fund_current_payment(self):
        self.consent(self.c)
        profile = self.svc.snapshot(self.c)['profile']
        profile['reserve'] = 0
        self.svc.save_profile(self.c, profile)
        p = self.proposal(21000)
        self.error('TRANSFER_BLOCKED', lambda: self.svc.confirm(self.c, p['id'], self.approve(p)))

    def test_revocation_prevents_posting(self):
        self.consent(self.c)
        p = self.proposal()
        self.svc.revoke(self.c, p['id'])
        self.error('REVOKED', lambda: self.svc.confirm(self.c, p['id'], self.approve(p)))

    def test_event_idempotency_is_scoped_and_fingerprint_checked(self):
        self.consent(self.c)
        self.consent(self.a)
        payload = {'type':'bill_added','event_key':'bill1'}
        first = self.svc.event(self.c, payload)
        self.assertEqual(first['event']['id'], self.svc.event(self.c, payload)['event']['id'])
        self.assertEqual(len(self.svc.snapshot(self.c)['bills']), 4)
        self.assertEqual(len(self.svc.event(self.a, payload)['snapshot']['bills']), 6)
        self.error('KEY_REUSED', lambda: self.svc.event(self.c, {'type':'salary_received','event_key':'bill1'}))

    def test_same_key_cannot_authorize_a_different_proposal(self):
        self.consent(self.c)
        first = self.proposal()
        self.svc.confirm(self.c, first['id'], self.approve(first, key='one-key'))
        second = self.proposal(200)
        self.error('KEY_REUSED', lambda: self.svc.confirm(self.c, second['id'], self.approve(second, key='one-key')))
        self.assertEqual(self.svc.snapshot(self.c)['balance'], 19900)

    def test_two_distinct_approved_proposals_cannot_race_current_revision(self):
        self.consent(self.c)
        proposals = [self.proposal(100), self.proposal(200)]
        def execute(p):
            try:
                return self.svc.confirm(self.c, p['id'], self.approve(p))['transaction']['id']
            except BankError as exc:
                return exc.code
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(execute, proposals))
        self.assertEqual(results.count('STALE_APPROVAL'), 1)
        self.assertIn(self.svc.snapshot(self.c)['balance'], [19800, 19900])

    def test_confirmation_token_is_bound_to_exact_proposal(self):
        self.consent(self.c)
        first, second = self.proposal(100), self.proposal(200)
        self.error('INVALID_APPROVAL', lambda: self.svc.confirm(self.c, second['id'], self.approve(first)))
        self.assertEqual(self.svc.snapshot(self.c)['balance'], 20000)

    def test_delay_does_not_pretend_user_reduced_daily_spending(self):
        self.consent(self.a)
        before = self.svc.snapshot(self.a)
        after = self.svc.event(self.a, {'type':'income_delayed','event_key':'delay'})['snapshot']
        self.assertFalse(after['replan'])
        self.assertEqual(after['cash']['daily'], before['cash']['daily'])
        self.assertEqual(after['cash']['pts'][20], before['cash']['pts'][20] - 6500)

    def test_expired_proposal_cannot_execute(self):
        self.consent(self.c)
        p = self.proposal()
        p['expiresAt'] = 0
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE proposals SET content=? WHERE id=?', (json.dumps(p), p['id']))
        self.error('APPROVAL_EXPIRED', lambda: self.svc.confirm(self.c, p['id'], self.approve(p)))

    def test_proposal_recovery_is_read_only_scoped_and_survives_restart(self):
        self.consent(self.c)
        self.consent(self.a)
        proposal = self.proposal()
        other = self.proposal(user=self.a)
        before = self.svc.snapshot(self.c)
        with sqlite3.connect(self.path) as db:
            stored = db.execute('SELECT content FROM proposals ORDER BY rowid').fetchall()
        recovered = BankService(self.path).proposals(self.c)
        self.assertEqual(recovered['snapshot'], before)
        self.assertEqual([p['id'] for p in recovered['proposals']], [proposal['id']])
        self.assertNotIn(other['id'], json.dumps(recovered))
        current = recovered['proposals'][0]
        self.assertEqual(current['status'], 'pending_confirmation')
        self.assertTrue(current['canConfirm'])
        self.assertEqual(current['confirmationToken'], proposal['confirmationToken'])
        self.assertTrue(all(check['ok'] for check in current['checks']))
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT content FROM proposals ORDER BY rowid').fetchall(), stored)
        self.assertEqual(self.svc.confirm(self.c, current['id'], self.approve(current))['snapshot']['balance'], 19900)

    def test_proposal_recovery_withholds_tokens_for_waiting_blocked_and_expired(self):
        self.consent(self.c)
        pending = self.proposal()
        waiting = self.proposal(when='salary')
        blocked = self.proposal(15000)
        expired = self.proposal()
        expired['expiresAt'] = 0
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE proposals SET content=? WHERE id=?', (json.dumps(expired), expired['id']))
        recovered = {p['id']: p for p in self.svc.proposals(self.c)['proposals']}
        for proposal, status in [(waiting, 'waiting_condition'), (blocked, 'blocked'), (expired, 'expired')]:
            with self.subTest(status=status):
                current = recovered[proposal['id']]
                self.assertEqual(current['status'], status)
                self.assertFalse(current['canConfirm'])
                self.assertNotIn('confirmationToken', current)
        self.assertTrue(recovered[pending['id']]['canConfirm'])
        self.assertEqual(recovered[expired['id']]['invalidReason'], 'APPROVAL_EXPIRED')

    def test_recovered_stale_proposal_stays_stale_when_salary_arrives(self):
        self.consent(self.c)
        proposal = self.proposal(when='salary')
        self.svc.event(self.c, {'type': 'salary_received', 'event_key': 'recover-salary'})
        recovered = self.svc.proposals(self.c)['proposals'][0]
        self.assertEqual(recovered['status'], 'stale')
        self.assertEqual(recovered['invalidReason'], 'STALE_APPROVAL')
        self.assertFalse(recovered['canConfirm'])
        self.assertNotIn('confirmationToken', recovered)
        self.error('STALE_APPROVAL', lambda: self.svc.confirm(self.c, proposal['id'], self.approve(proposal)))

    def test_recovered_terminal_statuses_and_receipts_survive_account_changes(self):
        self.consent(self.c)
        executed, revoked = self.proposal(), self.proposal()
        self.svc.revoke(self.c, revoked['id'])
        transaction = self.svc.confirm(self.c, executed['id'], self.approve(executed))['transaction']
        with sqlite3.connect(self.path) as db:
            content = json.loads(db.execute('SELECT content FROM proposals WHERE id=?', (executed['id'],)).fetchone()[0])
            content['expiresAt'] = 0
            db.execute('UPDATE proposals SET content=? WHERE id=?', (json.dumps(content), executed['id']))
        self.svc.event(self.c, {'type': 'bill_added', 'event_key': 'after-execution'})
        reopened = BankService(self.path)
        recovered = {p['id']: p for p in reopened.proposals(self.c)['proposals']}
        self.assertEqual(recovered[revoked['id']]['status'], 'revoked')
        self.assertEqual(recovered[executed['id']]['status'], 'executed')
        self.assertEqual(recovered[executed['id']]['transaction'], transaction)
        self.assertEqual(recovered[executed['id']]['transactionId'], transaction['id'])
        for proposal in recovered.values():
            self.assertFalse(proposal['canConfirm'])
            self.assertNotIn('confirmationToken', proposal)
        self.assertEqual(reopened.transaction(self.c, executed['id'])['transaction'], transaction)
        # Original confirmation retries must still return the original receipt.
        replay = reopened.confirm(self.c, executed['id'], self.approve(executed))
        self.assertEqual(replay['transaction'], transaction)
        self.assertEqual(replay['snapshot']['balance'], 19900)

    def test_proposal_recovery_has_a_hard_limit_and_newest_first(self):
        self.consent(self.c)
        proposals = [self.proposal() for _ in range(53)]
        recovered = self.svc.proposals(self.c)['proposals']
        self.assertEqual([p['id'] for p in recovered], [p['id'] for p in reversed(proposals[-50:])])
        self.assertEqual(len(self.svc.proposals(self.c, limit=2)['proposals']), 2)
        for limit in [0, 51, True, '2']:
            self.error('INVALID_LIMIT', lambda: self.svc.proposals(self.c, limit=limit))

    def test_proposal_recovery_withholds_token_after_read_consent_revoked(self):
        self.consent(self.c)
        self.proposal()
        profile = self.svc.snapshot(self.c)['profile']
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE profiles SET profile=? WHERE user_id=?',
                       (json.dumps(dict(profile, readConsent=False)), self.c))
        proposal = self.svc.proposals(self.c)['proposals'][0]
        self.assertEqual(proposal['status'], 'blocked')
        self.assertEqual(proposal['invalidReason'], 'CONSENT_REQUIRED')
        self.assertFalse(proposal['canConfirm'])
        self.assertNotIn('confirmationToken', proposal)

    def test_reset_only_resets_current_synthetic_account(self):
        self.consent(self.c)
        self.consent(self.a)
        self.svc.event(self.a, {'type':'bill_added','event_key':'a1'})
        p = self.proposal()
        self.svc.confirm(self.c, p['id'], self.approve(p))
        self.svc.reset(self.c)
        self.assertEqual(self.svc.snapshot(self.c)['balance'], 20000)
        self.assertFalse(self.svc.snapshot(self.c)['profile']['readConsent'])
        self.assertEqual(len(self.svc.snapshot(self.a)['bills']), 6)


if __name__ == '__main__':
    unittest.main()
