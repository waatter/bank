"""R2 domain boundaries, persistence/ARGOS integration and complete JS/Python parity.

Uses only the standard library and local Node; no paid API or model calls.
"""
import contextlib
import copy
import itertools
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest

from backend.profiling import FIELDS, assess_profile, validate_assessment
from backend.service import BankError, BankService

ROOT = Path(__file__).resolve().parents[1]
ASSESSMENT = dict(horizon='long', liquidityNeed='low', lossTolerance='high',
                  lossReaction='hold', experience='experienced', knowledge='possible')
PROFILE = dict(goalType='grow', goalName='长期目标', target=100000, reserve=1000,
               incomeStability='stable', style='C', automation='confirm', readConsent=True,
               assessment=ASSESSMENT)
BASE = dict(profile=PROFILE, balance=50000, income=10000, debt=0, daily=100,
            min=30000, free=29000, shocked=False, salaryReceived=False)


def inputs(facts=None, answers=None):
    value = copy.deepcopy(BASE)
    changes = copy.deepcopy(facts or {})
    value['profile'].update(changes.pop('profile', {}))
    value.update(changes)
    value['profile']['assessment'].update(answers or {})
    return value


class AssessmentTests(unittest.TestCase):
    def assess(self, facts=None, answers=None):
        return assess_profile(**inputs(facts, answers))

    def test_all_roles_and_schema(self):
        cases = [('cash', dict(min=999, free=-1), {}),
                 ('debt', dict(debt=6000), {}), ('buffer', dict(profile=dict(incomeStability='mixed')), {}),
                 ('goal', {}, dict(horizon='short')), ('steady', {}, dict(lossTolerance='small')),
                 ('growth', {}, {})]
        for role, facts, answers in cases:
            with self.subTest(role=role):
                result = self.assess(facts, answers)
                self.assertEqual(result['role']['key'], role)
                self.assertTrue(result['complete'])
                self.assertFalse(result['reviewRequired'])
                self.assertEqual(result['version'], 'ETHOS-R2')
                self.assertEqual(set(result), {'version', 'complete', 'reviewRequired', 'role', 'riskBand', 'dimensions', 'checks', 'reasons'})
                self.assertEqual(set(result['role']), {'key', 'name', 'headline', 'description', 'primaryView', 'actionLabel'})
                self.assertIn(result['role']['primaryView'], ['profile', 'actions', 'bills', 'life', 'console'])
                self.assertTrue(all(set(d) == {'key', 'label', 'value', 'detail'} for d in result['dimensions']))
                self.assertTrue(any('本原型设计' in s for s in result['checks']))
                self.assertNotIn('confidence', json.dumps(result))
        self.assertEqual(assess_profile()['role']['key'], 'review')

    def test_legacy_no_answers_invented(self):
        data = inputs()
        del data['profile']['assessment']
        result = assess_profile(**data)
        self.assertFalse(result['complete'])
        self.assertTrue(result['reviewRequired'])
        self.assertEqual(result['role']['key'], 'review')
        self.assertEqual(result['riskBand']['level'], 0)
        self.assertEqual([d['value'] for d in result['dimensions'][:6]], ['待声明'] * 6)
        self.assertNotIn('assessment', data['profile'])

    def test_priority_and_threshold_boundaries(self):
        for value, role, level in [(5999.99, 'growth', 5), (6000, 'debt', 2), (6000.01, 'debt', 2)]:
            with self.subTest(debt=value):
                result = self.assess(dict(debt=value))
                self.assertEqual(result['role']['key'], role)
                self.assertEqual(result['riskBand']['level'], level)
        self.assertEqual(self.assess(dict(min=999.99, free=-.01, debt=9000))['role']['key'], 'cash')
        self.assertEqual(self.assess(dict(min=1000, free=0))['role']['key'], 'buffer')
        self.assertEqual(self.assess(dict(debt=6000, profile=dict(incomeStability='uncertain')), dict(horizon='short'))['role']['key'], 'debt')
        self.assertEqual(self.assess(dict(income=0, debt=1))['role']['key'], 'debt')
        for free, role, level in [(2999.99, 'buffer', 2), (3000, 'growth', 3), (8999.99, 'growth', 3), (9000, 'growth', 5)]:
            result = self.assess(dict(min=free + 1000, free=free))
            self.assertEqual((result['role']['key'], result['riskBand']['level']), (role, level))
        self.assertEqual(self.assess(dict(profile=dict(reserve=20000), min=23999.99, free=3999.99))['role']['key'], 'buffer')
        self.assertEqual(self.assess(dict(balance=-1, min=-1, free=-1001))['role']['key'], 'cash')

    def test_caps_never_exceed_willingness_term_liquidity_or_capacity(self):
        for key, limits in [('horizon', dict(short=2, medium=3, long=5)),
                            ('liquidityNeed', dict(high=2, medium=3, low=5)),
                            ('lossTolerance', dict(none=1, small=2, medium=3, high=5))]:
            for answer, level in limits.items():
                answers = {key: answer}
                if answer == 'none':
                    answers['lossReaction'] = 'review'
                self.assertEqual(self.assess(answers=answers)['riskBand']['level'], level)
        self.assertEqual(self.assess(answers=dict(lossReaction='review'))['riskBand']['level'], 4)
        for answers in [dict(experience='none'), dict(knowledge='impossible'), dict(knowledge='unsure')]:
            result = self.assess(answers=answers)
            self.assertEqual(result['role']['key'], 'steady')
            self.assertEqual(result['riskBand']['level'], 2)
            self.assertTrue(any('简单收支' in s for s in result['reasons']))
        self.assertEqual(self.assess(dict(min=999, free=-1))['riskBand']['level'], 1)

    def test_conflicting_answers_and_no_loss(self):
        for tolerance, reaction in [('medium', 'sell'), ('high', 'sell'), ('none', 'hold')]:
            answers = dict(ASSESSMENT, lossTolerance=tolerance, lossReaction=reaction)
            with self.assertRaises(ValueError):
                validate_assessment(answers)
            unresolved = self.assess(answers=answers)
            self.assertFalse(unresolved['complete'])
            self.assertTrue(unresolved['reviewRequired'])
            self.assertEqual(unresolved['role']['key'], 'review')
            for clarification in ['safety', 'confirmed']:
                result = self.assess(answers=dict(answers, clarification=clarification))
                self.assertTrue(result['complete'])
                self.assertFalse(result['reviewRequired'])
                self.assertEqual(result['riskBand']['level'], 1 if clarification == 'safety' or tolerance == 'none' else 2)
        for reaction in ['sell', 'review', 'hold']:
            for facts in [{}, dict(income=None), dict(shocked=True), dict(debt=9000)]:
                answers = dict(lossTolerance='none', lossReaction=reaction)
                if reaction == 'hold':
                    answers['clarification'] = 'confirmed'
                self.assertEqual(self.assess(facts, answers)['riskBand']['level'], 1)
        # An irrelevant clarification never manufactures extra risk or a new cap.
        self.assertEqual(self.assess(answers=dict(clarification='safety'))['riskBand']['level'], 5)

    def test_unknown_invalid_and_inconsistent_facts(self):
        for key in ['balance', 'income', 'debt', 'daily', 'min', 'free', 'shocked', 'salaryReceived']:
            for value in [None, '', '0', [], {}, True if key not in ['shocked', 'salaryReceived'] else 0]:
                with self.subTest(key=key, value=value):
                    result = self.assess({key: value})
                    self.assertTrue(result['complete'])
                    self.assertTrue(result['reviewRequired'])
                    self.assertEqual(result['role']['key'], 'review')
                    self.assertEqual(result['riskBand']['level'], 0)
        for facts in [dict(income=-1), dict(debt=-1), dict(daily=-1), dict(free=1),
                      dict(min=60000, free=59000), dict(balance=float('inf')), dict(income=float('nan')),
                      dict(balance=10**1000), dict(profile=dict(reserve=-1)), dict(profile=dict(incomeStability=[]))]:
            self.assertEqual(self.assess(facts)['role']['key'], 'review')
        self.assertEqual(self.assess(dict(balance=0, income=0, debt=0, daily=0, min=0, free=0, profile=dict(reserve=0)))['role']['key'], 'buffer')

    def test_preferences_and_style_do_not_mutate_with_cash(self):
        original = copy.deepcopy(BASE)
        before = self.assess()
        after = self.assess(dict(shocked=True))
        self.assertEqual(after['role']['key'], 'buffer')
        self.assertEqual(after['riskBand']['level'], 2)
        without_capacity = lambda r: [d for d in r['dimensions'] if d['key'] != 'capacity']
        self.assertEqual(without_capacity(before), without_capacity(after))
        self.assertEqual(self.assess(dict(shocked=True, salaryReceived=True)), before)
        for style, goal_type in itertools.product(['A', 'B', 'C'], ['protect', 'recover', 'accumulate', 'grow']):
            self.assertEqual(self.assess(dict(profile=dict(style=style, goalType=goal_type, target=1e8))), before)
        self.assertEqual(BASE, original)

    def test_strict_assessment_validation(self):
        clean = validate_assessment(ASSESSMENT)
        self.assertEqual(clean, ASSESSMENT)
        self.assertIsNot(clean, ASSESSMENT)
        for key in ASSESSMENT:
            partial = dict(ASSESSMENT)
            del partial[key]
            with self.assertRaises(ValueError):
                validate_assessment(partial)
        for value in [None, [], {}, 'high', dict(ASSESSMENT, horizon='forever'),
                      dict(ASSESSMENT, knowledge=True), dict(ASSESSMENT, lossTolerance=5),
                      dict(ASSESSMENT, clarification=None), dict(ASSESSMENT, score=99)]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_assessment(value)

    def test_full_json_parity_with_javascript(self):
        self.assertIsNotNone(shutil.which('node'), 'Cross-language contract tests require local Node.')
        keys = [key for key in FIELDS if key != 'clarification']
        vectors = []
        for answers in itertools.product(*(FIELDS[key][1] for key in keys)):
            a = dict(zip(keys, answers))
            vectors.append(inputs(answers=a))
            if ((a['lossTolerance'] in ['medium', 'high'] and a['lossReaction'] == 'sell')
                    or (a['lossTolerance'] == 'none' and a['lossReaction'] == 'hold')):
                for clarification in ['safety', 'confirmed']:
                    vectors.append(inputs(answers=dict(a, clarification=clarification)))
        for key in BASE:
            for value in [None, '', '0', False, [], {}, -1, 0]:
                data = inputs()
                data[key] = value
                vectors.append(data)
        for key in ['reserve', 'incomeStability', 'goalType']:
            for value in [None, '', [], ['stable'], {}, 'toString', -1, 0]:
                vectors.append(inputs(dict(profile={key: value})))
        for field in FIELDS:
            for value in [None, '', [], {}, True, 'toString']:
                vectors.append(inputs(answers={field: value}))
        for facts in [dict(min=999, free=-1), dict(debt=6000), dict(income=0, debt=1), dict(shocked=True),
                      dict(shocked=True, salaryReceived=True), dict(min=4000, free=3000), dict(min=10000, free=9000)]:
            vectors.append(inputs(facts))
        legacy = inputs()
        del legacy['profile']['assessment']
        vectors.extend([legacy, {}, dict(profile={'assessment': None})])
        process = subprocess.run(['node', str(ROOT / 'tests/risk-profile.cjs'), '--assess'],
                                 input=json.dumps(vectors), text=True, encoding='utf-8',
                                 capture_output=True, check=True, timeout=30)
        actual = json.loads(process.stdout)
        self.assertEqual(len(actual), len(vectors))
        for index, (data, js) in enumerate(zip(vectors, actual)):
            with self.subTest(index=index, data=data):
                self.assertEqual(assess_profile(**data), js)


class ProfilePersistenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'bank.sqlite3'
        self.service = BankService(self.path)
        self.snapshot = self.service.session('C')['snapshot']
        self.user_id = self.snapshot['user']['id']
        self.profile = dict(self.snapshot['profile'], reserve=1000, readConsent=True, assessment=dict(ASSESSMENT))

    def tearDown(self):
        self.directory.cleanup()

    def expected(self, snapshot):
        return assess_profile(profile=snapshot['profile'], balance=snapshot['balance'],
                              income=self.service.fixtures['personas']['C']['income'],
                              debt=sum(-b['amount'] for b in snapshot['bills'] if b['kind'] in ['还款', '分期', '贷款']),
                              daily=snapshot['cash']['daily'], min=snapshot['cash']['min'], free=snapshot['cash']['free'],
                              shocked=snapshot['shocked'], salaryReceived=snapshot['salaryReceived'])

    def test_old_profiles_can_save_and_restart_without_invented_assessment(self):
        self.assertEqual(self.snapshot['riskProfile']['role']['key'], 'review')
        legacy = dict(self.profile)
        del legacy['assessment']
        result = self.service.save_profile(self.user_id, legacy)
        self.assertNotIn('assessment', result['profile'])
        self.assertFalse(result['riskProfile']['complete'])
        self.assertEqual(result['policy']['version'], 'ETHOS-R1')
        self.assertEqual(BankService(self.path).snapshot(self.user_id)['riskProfile'], result['riskProfile'])

    def test_assessment_is_saved_and_same_snapshot_is_used_without_recursion(self):
        original_snapshot = self.service._snapshot
        calls = []
        def counted(*args):
            calls.append(1)
            return original_snapshot(*args)
        self.service._snapshot = counted
        result = self.service.save_profile(self.user_id, self.profile)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result['profile']['assessment'], ASSESSMENT)
        self.assertEqual(result['riskProfile'], self.expected(result))
        self.assertEqual(result['balance'], self.snapshot['balance'])
        self.assertEqual(result['transactions'], self.snapshot['transactions'])
        self.assertEqual(BankService(self.path).snapshot(self.user_id)['profile']['assessment'], ASSESSMENT)
        with contextlib.closing(sqlite3.connect(self.path)) as db:
            self.assertEqual([r[0] for r in db.execute('SELECT kind FROM authorizations')], ['read_profile'])

    def test_invalid_assessment_is_rejected_atomically(self):
        self.service.save_profile(self.user_id, self.profile)
        before = self.service.snapshot(self.user_id)
        for value in [None, [], {}, dict(ASSESSMENT, knowledge='yes'), dict(ASSESSMENT, lossReaction='sell'),
                      dict(ASSESSMENT, clarification=False), dict(ASSESSMENT, authorization=True)]:
            with self.subTest(value=value), self.assertRaises(BankError) as error:
                self.service.save_profile(self.user_id, dict(self.profile, assessment=value))
            self.assertEqual(error.exception.code, 'INVALID_PROFILE')
            self.assertEqual(self.service.snapshot(self.user_id), before)
        safe = dict(ASSESSMENT, lossReaction='sell', clarification='safety')
        result = self.service.save_profile(self.user_id, dict(self.profile, assessment=safe))
        self.assertEqual(result['profile']['assessment'], safe)
        self.assertEqual(result['riskProfile']['riskBand']['level'], 1)

    def test_salary_and_bill_events_only_update_facts_and_keep_risk_answers(self):
        before = self.service.save_profile(self.user_id, self.profile)
        for event in ['income_delayed', 'bill_added', 'salary_received']:
            snapshot = self.service.event(self.user_id, dict(type=event, event_key=event))['snapshot']
            self.assertEqual(snapshot['profile'], before['profile'])
            self.assertEqual(snapshot['riskProfile'], self.expected(snapshot))
            self.assertEqual(snapshot['policy']['version'], 'ETHOS-R1')
        self.assertTrue(snapshot['salaryReceived'])
        self.assertEqual(snapshot['balance'], before['balance'] + self.service.fixtures['personas']['C']['income'])
        self.assertEqual(len([t for t in snapshot['transactions'] if t.get('key') == 'salary:current-cycle']), 1)
        replay = self.service.event(self.user_id, dict(type='salary_received', event_key='second-salary-request'))['snapshot']
        self.assertEqual(replay, snapshot)

    def test_risk_role_does_not_bypass_argos_or_confirmation(self):
        result = self.service.save_profile(self.user_id, self.profile)
        proposal = self.service.propose_transfer(self.user_id, dict(to='mom', amount=50000, when='now'))
        self.assertEqual(proposal['proposal']['status'], 'blocked')
        self.assertTrue(any(not check['ok'] for check in proposal['checks']))
        with self.assertRaises(BankError):
            self.service.confirm(self.user_id, proposal['proposal']['id'], dict(confirmation=True))
        self.assertEqual(self.service.snapshot(self.user_id)['balance'], result['balance'])


if __name__ == '__main__':
    unittest.main()
