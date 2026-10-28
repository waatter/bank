"""Life planning persistence never spends money and commits obligations atomically."""
import concurrent.futures
import contextlib
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from backend.life_service import LifeService
from backend.service import BankError, BankService


class LifeServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'bank.sqlite'
        self.bank = BankService(self.path)
        self.life = LifeService(self.bank)
        self.a = self.bank.session('A')['snapshot']['user']['id']
        self.c = self.bank.session('C')['snapshot']['user']['id']
        for user_id in (self.a, self.c):
            profile = self.bank.snapshot(user_id)['profile']
            profile['readConsent'] = True
            self.bank.save_profile(user_id, profile)

    def tearDown(self):
        self.tmp.cleanup()

    def scene(self, **changes):
        result = dict(title='家电维修', category='living', amount=600,
                    earliestDay=2, preferredDay=4, latestDay=8,
                    importance='important', allowSkip=False,
                    allowAlternative=True, alternativeAmount=300,
                    alternativeLabel='基础维修')
        result.update(changes)
        return result

    def add(self, user_id=None):
        user_id = user_id or self.c
        state = self.life.get(user_id)
        return self.life.add_scene(user_id, {'version': state['version'], 'scene': self.scene()})

    def error(self, code, call):
        with self.assertRaises(BankError) as caught:
            call()
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def approval(self, state):
        return dict(version=state['version'], accountRevision=state['snapshot']['user']['revision'], confirmation=True)

    def test_defaults_and_reads_do_not_persist_or_mutate_ledger(self):
        before = self.bank.snapshot(self.c)
        state = self.life.get(self.c)
        self.assertEqual(state['version'], 0)
        self.assertEqual(state['settings'], dict(illiquidAssets=0, delayDays=15, expenseShock=1200, shockDay=3, goalAmount=500))
        self.assertEqual(state['scenes'], [])
        self.assertEqual(state['snapshot'], before)
        with self.bank._db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM life_state').fetchone()[0], 0)

    def test_hypotheses_are_scoped_and_do_not_change_bank_revision(self):
        before = self.bank.snapshot(self.c)
        state = self.add()
        self.assertEqual(state['version'], 1)
        self.assertEqual(state['scenes'][0]['status'], 'draft')
        self.assertEqual(state['snapshot']['user']['revision'], before['user']['revision'])
        self.assertEqual(state['snapshot']['balance'], before['balance'])
        self.assertEqual(state['snapshot']['cash'], before['cash'])
        self.assertEqual(self.life.get(self.a)['scenes'], [])
        scene_id = state['scenes'][0]['id']
        self.assertEqual(self.error('LIFE_SCENE_NOT_FOUND', lambda: self.life.commit_scene(self.a, scene_id, self.approval(self.life.get(self.a)))).status, 404)
        self.error('LIFE_SCENE_NOT_FOUND', lambda: self.life.cancel_scene(self.a, scene_id, {'version': 0, 'confirmation': True}))

    def test_versions_reject_stale_or_coercible_values(self):
        state = self.add()
        for version in (0, True, 1.0, '1', None):
            with self.subTest(version=version):
                self.error('LIFE_VERSION_CONFLICT', lambda: self.life.save_settings(self.c, {'version': version, 'settings': state['settings']}))
        self.assertEqual(self.life.get(self.c)['version'], 1)

    def test_concurrent_edit_only_one_wins(self):
        def edit(_):
            try:
                return self.life.add_scene(self.c, {'version': 0, 'scene': self.scene()})['version']
            except BankError as exc:
                return exc.code
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(edit, range(4)))
        self.assertEqual(results.count(1), 1)
        self.assertEqual(results.count('LIFE_VERSION_CONFLICT'), 3)
        self.assertEqual(len(self.life.get(self.c)['scenes']), 1)

    def test_commit_adds_future_bill_and_invalidates_old_transfer(self):
        state = self.add()
        before = state['snapshot']
        proposal = self.bank.propose_transfer(self.c, {'amount': 100, 'to': 'goal'})['proposal']
        result = self.life.commit_scene(self.c, state['scenes'][0]['id'], self.approval(state))
        scene = result['scenes'][0]
        bill = next(b for b in result['snapshot']['bills'] if b.get('id') == scene['billId'])
        self.assertEqual(scene['status'], 'committed')
        self.assertEqual(bill['amount'], -600)
        self.assertEqual(bill['day'], 4)
        self.assertEqual(bill['source'], 'life_scene')
        self.assertEqual(result['snapshot']['balance'], before['balance'])
        self.assertEqual(result['snapshot']['goalFunded'], before['goalFunded'])
        self.assertEqual(result['snapshot']['user']['revision'], before['user']['revision'] + 1)
        self.assertEqual(result['version'], 2)
        self.assertEqual(result['snapshot']['cash']['pts'][4], before['cash']['pts'][4] - 600)
        self.error('STALE_APPROVAL', lambda: self.bank.confirm(self.c, proposal['id'], {'confirmation': True, 'confirmationToken': proposal['confirmationToken']}))
        self.assertEqual(self.life.commit_scene(self.c, scene['id'], self.approval(state)), result)
        self.error('LIFE_SCENE_COMMITTED', lambda: self.life.cancel_scene(self.c, scene['id'], {'version': 2, 'confirmation': True}))

    def test_commit_requires_confirmation_and_fresh_account_revision(self):
        state = self.add()
        scene_id = state['scenes'][0]['id']
        self.error('CONFIRMATION_REQUIRED', lambda: self.life.commit_scene(self.c, scene_id, {'version': 1, 'accountRevision': state['snapshot']['user']['revision']}))
        self.bank.event(self.c, {'type': 'income_delayed', 'event_key': 'new-facts'})
        self.error('STALE_APPROVAL', lambda: self.life.commit_scene(self.c, scene_id, self.approval(state)))
        self.assertEqual(self.life.get(self.c)['scenes'][0]['status'], 'draft')

    def test_concurrent_commit_retries_add_exactly_one_obligation(self):
        state = self.add()
        scene_id = state['scenes'][0]['id']
        def commit(_):
            return self.life.commit_scene(self.c, scene_id, self.approval(state))
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(commit, range(4)))
        self.assertTrue(all(result == results[0] for result in results))
        result = results[0]
        self.assertEqual(result['version'], 2)
        self.assertEqual(sum(b.get('source') == 'life_scene' for b in result['snapshot']['bills']), 1)
        self.assertEqual(result['snapshot']['user']['revision'], state['snapshot']['user']['revision'] + 1)

    def test_invalid_settings_do_not_advance_or_write_audit(self):
        state = self.life.get(self.c)
        for changes in ({'expenseShock': float('nan')}, {'delayDays': True}, {'shockDay': 0}, {'unexpected': 1}):
            with self.subTest(changes=changes):
                settings = {**state['settings'], **changes}
                self.error('INVALID_LIFE_SETTINGS', lambda: self.life.save_settings(self.c, {'version': 0, 'settings': settings}))
        self.assertEqual(self.life.get(self.c), state)

    def test_cancelled_scene_does_not_count_toward_limit(self):
        for _ in range(10):
            state = self.add()
        self.error('LIFE_SCENE_LIMIT', lambda: self.add())
        state = self.life.cancel_scene(self.c, state['scenes'][0]['id'], {'version': state['version'], 'confirmation': True})
        self.assertEqual(state['scenes'][0]['status'], 'cancelled')
        state = self.add()
        self.assertEqual(len(state['scenes']), 11)
        self.error('LIFE_SCENE_CANCELLED', lambda: self.life.commit_scene(self.c, state['scenes'][0]['id'], self.approval(state)))

    def test_restart_preserves_settings_scenes_and_commit(self):
        state = self.add()
        state['settings']['goalAmount'] = 800
        state = self.life.save_settings(self.c, {'version': state['version'], 'settings': state['settings']})
        state = self.life.commit_scene(self.c, state['scenes'][0]['id'], self.approval(state))
        reopened = LifeService(BankService(self.path))
        self.assertEqual(reopened.get(self.c), state)

    def test_analyze_is_read_only_with_consistent_versions(self):
        self.add()
        before = self.life.get(self.c)
        with contextlib.closing(sqlite3.connect(self.path)) as db:
            counts_before = {name: db.execute('SELECT COUNT(*) FROM ' + name).fetchone()[0] for name in ('life_state', 'life_scenes', 'audit', 'profile_updates')}
        result = self.life.analyze(self.c)
        self.assertEqual(result['lifeVersion'], before['version'])
        self.assertEqual(result['accountRevision'], before['snapshot']['user']['revision'])
        self.assertEqual(self.life.get(self.c), before)
        with contextlib.closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(counts_before, {name: db.execute('SELECT COUNT(*) FROM ' + name).fetchone()[0] for name in counts_before})

    def test_amount_and_boolean_validation_rejects_invalid_scene(self):
        for amount in (True, 0, -1, float('nan'), float('inf'), 1.001, '600'):
            scene = self.scene()
            scene['amount'] = amount
            with self.subTest(amount=amount):
                self.error('INVALID_LIFE_SCENE', lambda: self.life.add_scene(self.c, {'version': 0, 'scene': scene}))
        self.assertEqual(self.life.get(self.c)['version'], 0)

    def plan_approval(self, state, policy='buffer', key='plan-request-0001'):
        return {**self.approval(state), 'policyId': policy, 'requestKey': key}

    def test_adopted_plan_commits_server_selected_amount_and_day(self):
        state = self.add()
        expected = next(policy for policy in self.life.analyze(self.c)['policies'] if policy['id'] == 'buffer')['allocations']
        proposal = self.bank.propose_transfer(self.c, {'amount': 100, 'to': 'goal'})['proposal']
        result = self.life.commit_plan(self.c, self.plan_approval(state))
        allocation = expected[0]
        scene = result['scenes'][0]
        self.assertEqual(result['receipt']['allocations'], expected)
        self.assertEqual(scene['committedAmount'], allocation['amount'])
        self.assertEqual(scene['committedDay'], allocation['day'])
        self.assertEqual(scene['committedMode'], 'alternative')
        bill = next(b for b in result['snapshot']['bills'] if b.get('id') == scene['billId'])
        self.assertEqual((bill['amount'], bill['day']), (-300, 8))
        self.assertEqual(result['snapshot']['balance'], state['snapshot']['balance'])
        self.assertEqual(result['version'], state['version'] + 1)
        self.assertEqual(result['snapshot']['user']['revision'], state['snapshot']['user']['revision'] + 1)
        self.error('STALE_APPROVAL', lambda: self.bank.confirm(self.c, proposal['id'], {'confirmation': True, 'confirmationToken': proposal['confirmationToken']}))
        self.assertEqual(self.life.analyze(self.c)['policies'][0]['allocations'], [])

    def test_plan_retries_are_scoped_persistent_and_fingerprint_checked(self):
        state = self.add()
        payload = self.plan_approval(state)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.life.commit_plan(self.c, payload), range(4)))
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(len([b for b in results[0]['snapshot']['bills'] if b.get('source') == 'life_scene']), 1)
        self.error('KEY_REUSED', lambda: self.life.commit_plan(self.c, {**payload, 'policyId': 'balanced'}))
        self.bank.event(self.c, {'type': 'income_delayed', 'event_key': 'after-plan'})
        reopened = LifeService(BankService(self.path))
        retry = reopened.commit_plan(self.c, payload)
        self.assertEqual(retry['receipt'], results[0]['receipt'])
        self.assertEqual(retry['snapshot'], self.bank.snapshot(self.c))
        other = self.add(self.a)
        other_result = self.life.commit_plan(self.a, self.plan_approval(other))
        self.assertNotEqual(other_result['receipt']['id'], retry['receipt']['id'])
        with self.bank._db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM life_plan_receipts').fetchone()[0], 2)

    def test_adopting_new_drafts_preserves_already_committed_obligation_once(self):
        first = self.add()
        first = self.life.commit_scene(self.c, first['scenes'][0]['id'], self.approval(first))
        state = self.add()
        result = self.life.commit_plan(self.c, self.plan_approval(state))
        bills = [bill for bill in result['snapshot']['bills'] if bill.get('source') == 'life_scene']
        self.assertEqual(sorted(bill['amount'] for bill in bills), [-600, -300])
        self.assertEqual(len(result['receipt']['allocations']), 1)
        self.assertEqual(result['scenes'][0], first['scenes'][0])

    def test_adoption_rolls_back_scenes_bills_and_receipt_on_failure(self):
        state = self.add()
        with patch.object(self.bank, '_changed', side_effect=RuntimeError('simulated persistence failure')):
            with self.assertRaises(RuntimeError):
                self.life.commit_plan(self.c, self.plan_approval(state))
        self.assertEqual(self.life.get(self.c), state)
        with self.bank._db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM life_plan_receipts').fetchone()[0], 0)
        result = self.life.commit_plan(self.c, self.plan_approval(state))
        self.assertEqual(result['version'], state['version'] + 1)

    def test_plan_requires_current_versions_confirmation_and_strict_payload(self):
        state = self.add()
        payload = self.plan_approval(state)
        self.error('CONFIRMATION_REQUIRED', lambda: self.life.commit_plan(self.c, {**payload, 'confirmation': False}))
        self.error('INVALID_LIFE_PLAN', lambda: self.life.commit_plan(self.c, {**payload, 'allocations': []}))
        self.error('INVALID_LIFE_PLAN', lambda: self.life.commit_plan(self.c, {**payload, 'requestKey': 'x'}))
        self.error('INVALID_LIFE_PLAN', lambda: self.life.commit_plan(self.c, {**payload, 'policyId': 'forged'}))
        self.error('LIFE_VERSION_CONFLICT', lambda: self.life.commit_plan(self.c, {**payload, 'version': 0}))
        self.error('STALE_APPROVAL', lambda: self.life.commit_plan(self.c, {**payload, 'accountRevision': True}))
        self.bank.event(self.c, {'type': 'income_delayed', 'event_key': 'before-plan'})
        self.error('STALE_APPROVAL', lambda: self.life.commit_plan(self.c, payload))
        self.assertEqual(self.life.get(self.c)['scenes'][0]['status'], 'draft')
        self.assertEqual(self.life.get(self.c)['version'], state['version'])

    def test_adopted_skip_plan_only_cancels_user_permitted_draft(self):
        state = self.life.add_scene(self.c, {'version': 0, 'scene': self.scene(allowSkip=True)})
        before_bills = state['snapshot']['bills']
        result = self.life.commit_plan(self.c, self.plan_approval(state))
        self.assertEqual(result['scenes'][0]['status'], 'cancelled')
        self.assertEqual(result['scenes'][0]['committedMode'], 'skipped')
        self.assertEqual(result['snapshot']['bills'], before_bills)
        self.assertEqual(result['snapshot']['balance'], state['snapshot']['balance'])
        self.assertEqual(result['snapshot']['user']['revision'], state['snapshot']['user']['revision'] + 1)
        self.assertEqual(result['receipt']['allocations'][0]['amount'], 0)
        self.error('LIFE_PLAN_EMPTY', lambda: self.life.commit_plan(self.c, self.plan_approval(result, key='plan-request-0002')))

    def goal_payload(self, user_id=None, amount=100):
        current = self.life.get(user_id or self.c)
        return {'version': current['version'], 'accountRevision': current['snapshot']['user']['revision'], 'amount': amount}

    def test_goal_proposal_is_scoped_versioned_and_does_not_spend(self):
        state = self.life.get(self.c)
        result = self.life.propose_goal(self.c, self.goal_payload())
        proposal = result['proposal']
        self.assertEqual(proposal['lifeVersion'], 0)
        self.assertEqual(proposal['to'], 'goal')
        self.assertEqual(proposal['when'], 'now')
        self.assertEqual(proposal['amount'], 100)
        self.assertEqual(result['snapshot']['balance'], state['snapshot']['balance'])
        self.assertEqual(result['snapshot']['goalFunded'], state['snapshot']['goalFunded'])
        self.assertEqual(result['snapshot']['cash'], state['snapshot']['cash'])
        self.assertEqual(result['snapshot']['user']['revision'], state['snapshot']['user']['revision'])
        self.assertEqual(self.life.get(self.c)['version'], 0)
        self.error('PROPOSAL_NOT_FOUND', lambda: self.bank.confirm(self.a, proposal['id'], {'confirmation': True, 'confirmationToken': proposal['confirmationToken']}))

    def test_goal_proposal_uses_fresh_server_ceiling_and_rejects_invalid_amounts(self):
        before = self.life.get(self.c)
        for amount in (True, '100', None, 0, -1, float('inf'), float('nan'), 1.001):
            with self.subTest(amount=amount):
                self.error('INVALID_AMOUNT', lambda: self.life.propose_goal(self.c, self.goal_payload(amount=amount)))
        ceiling = next(policy for policy in self.life.analyze(self.c)['policies'] if policy['id'] == 'balanced')['metrics']['goalAmount']
        self.error('LIFE_GOAL_LIMIT', lambda: self.life.propose_goal(self.c, self.goal_payload(amount=ceiling + .01)))
        for change in ({'to': 'external'}, {'amount': 100, 'allocations': []}):
            self.error('INVALID_LIFE_GOAL', lambda: self.life.propose_goal(self.c, {**self.goal_payload(), **change}))
        self.assertEqual(self.life.get(self.c), before)
        # Only settings change; bank revision alone cannot guard this ceiling.
        state = self.life.save_settings(self.c, {'version': 0, 'settings': {**before['settings'], 'goalAmount': 50}})
        self.assertEqual(state['snapshot']['user']['revision'], before['snapshot']['user']['revision'])
        self.error('LIFE_GOAL_LIMIT', lambda: self.life.propose_goal(self.c, self.goal_payload(amount=100)))
        self.assertEqual(self.life.propose_goal(self.c, self.goal_payload(amount=50))['proposal']['amount'], 50)

    def test_goal_proposal_requires_no_draft_and_current_life_and_bank_versions(self):
        payload = self.goal_payload()
        state = self.add()
        self.error('LIFE_VERSION_CONFLICT', lambda: self.life.propose_goal(self.c, payload))
        self.error('LIFE_DRAFTS_PENDING', lambda: self.life.propose_goal(self.c, self.goal_payload()))
        self.life.cancel_scene(self.c, state['scenes'][0]['id'], {'version': state['version'], 'confirmation': True})
        payload = self.goal_payload()
        self.bank.event(self.c, {'type': 'income_delayed', 'event_key': 'before-life-goal'})
        self.error('STALE_APPROVAL', lambda: self.life.propose_goal(self.c, payload))
        self.error('STALE_APPROVAL', lambda: self.life.propose_goal(self.c, {**self.goal_payload(), 'accountRevision': True}))

    def test_life_edit_invalidates_existing_life_goal_proposal(self):
        result = self.life.propose_goal(self.c, self.goal_payload())
        proposal = result['proposal']
        state = self.add()
        self.assertEqual(state['snapshot']['user']['revision'], result['snapshot']['user']['revision'])
        self.error('STALE_LIFE_PLAN', lambda: self.bank.confirm(self.c, proposal['id'], {'confirmation': True, 'confirmationToken': proposal['confirmationToken']}))
        recovered = next(item for item in self.bank.proposals(self.c)['proposals'] if item['id'] == proposal['id'])
        self.assertEqual(recovered['status'], 'stale')
        self.assertNotIn('confirmationToken', recovered)

    def test_life_settings_edit_invalidates_goal_but_generic_goal_remains_compatible(self):
        state = self.life.get(self.c)
        life_proposal = self.life.propose_goal(self.c, self.goal_payload())['proposal']
        generic = self.bank.propose_transfer(self.c, {'to': 'goal', 'amount': 100})['proposal']
        self.assertNotIn('lifeVersion', generic)
        self.life.save_settings(self.c, {'version': 0, 'settings': {**state['settings'], 'goalAmount': 200}})
        self.error('STALE_LIFE_PLAN', lambda: self.bank.confirm(self.c, life_proposal['id'], {'confirmation': True, 'confirmationToken': life_proposal['confirmationToken']}))
        recovered = next(item for item in self.bank.proposals(self.c)['proposals'] if item['id'] == life_proposal['id'])
        self.assertEqual(recovered['status'], 'stale')
        self.assertNotIn('confirmationToken', recovered)
        self.add()
        result = self.bank.confirm(self.c, generic['id'], {'confirmation': True, 'confirmationToken': generic['confirmationToken']})
        self.assertEqual(result['snapshot']['balance'], state['snapshot']['balance'] - 100)

    def test_life_goal_expiration_still_hides_token_and_never_spends(self):
        proposal = self.life.propose_goal(self.c, self.goal_payload())['proposal']
        with self.bank._db(True) as db:
            expired = {**proposal, 'expiresAt': 0}
            db.execute('UPDATE proposals SET content=? WHERE user_id=? AND id=?', (json.dumps(expired), self.c, proposal['id']))
        before = self.bank.snapshot(self.c)['balance']
        self.error('APPROVAL_EXPIRED', lambda: self.bank.confirm(self.c, proposal['id'], {'confirmation': True, 'confirmationToken': proposal['confirmationToken']}))
        recovered = next(item for item in self.bank.proposals(self.c)['proposals'] if item['id'] == proposal['id'])
        self.assertEqual(recovered['status'], 'expired')
        self.assertNotIn('confirmationToken', recovered)
        self.assertEqual(self.bank.snapshot(self.c)['balance'], before)

    def test_concurrent_scene_add_and_goal_proposal_never_leave_actionable_goal(self):
        payload = self.goal_payload()
        barrier = threading.Barrier(2)
        def propose():
            barrier.wait()
            try:
                return self.life.propose_goal(self.c, payload)
            except BankError as exc:
                return exc.code
        def add():
            barrier.wait()
            return self.life.add_scene(self.c, {'version': 0, 'scene': self.scene()})
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            proposed, added = pool.submit(propose), pool.submit(add)
            result, state = proposed.result(), added.result()
        self.assertEqual(state['version'], 1)
        if isinstance(result, str):
            self.assertEqual(result, 'LIFE_VERSION_CONFLICT')
        else:
            proposal = result['proposal']
            self.error('STALE_LIFE_PLAN', lambda: self.bank.confirm(self.c, proposal['id'], {'confirmation': True, 'confirmationToken': proposal['confirmationToken']}))
        self.assertTrue(all(not proposal['canConfirm'] for proposal in self.bank.proposals(self.c)['proposals']))

    def test_read_consent_required_for_all_life_access(self):
        profile = self.bank.snapshot(self.c)['profile']
        profile['readConsent'] = False
        with self.bank._db(True) as db:
            db.execute('UPDATE profiles SET profile=? WHERE user_id=?', (json.dumps(profile), self.c))
        self.error('CONSENT_REQUIRED', lambda: self.life.get(self.c))
        self.error('CONSENT_REQUIRED', lambda: self.life.analyze(self.c))
        self.error('CONSENT_REQUIRED', lambda: self.life.commit_plan(self.c, dict(version=0, accountRevision=1, policyId='original', requestKey='plan-request-0001', confirmation=True)))
        self.error('CONSENT_REQUIRED', lambda: self.add())
        self.error('CONSENT_REQUIRED', lambda: self.life.propose_goal(self.c, dict(version=0, accountRevision=1, amount=100)))


if __name__ == '__main__':
    unittest.main()
