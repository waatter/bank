"""Independent cash identities and economic constraints, no paid API or ML claims."""
import copy
import math
import tempfile
import unittest
from pathlib import Path

from backend.life_planner import analyze, validate_scene, validate_settings, DEFAULT_SETTINGS
from backend.service import BankService


def scene(**changes):
    value = dict(id='scene_1', title='工作设备维修', category='work', amount=1200, earliestDay=1,
                 preferredDay=2, latestDay=15, importance='important', allowSkip=False,
                 allowAlternative=True, alternativeAmount=500, alternativeLabel='用户选择基础维修', status='draft')
    value.update(changes)
    return value


class LifePlannerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.bank = BankService(Path(self.directory.name) / 'bank.sqlite')
        result = self.bank.session('A')
        self.uid = result['snapshot']['user']['id']
        self.bank.save_profile(self.uid, {**result['snapshot']['profile'], 'readConsent': True})
        self.snapshot = self.bank.snapshot(self.uid)

    def tearDown(self):
        self.directory.cleanup()

    def test_analysis_is_read_only_and_retains_user_floor(self):
        snap, settings, scenes = copy.deepcopy(self.snapshot), dict(DEFAULT_SETTINGS), [scene()]
        originals = copy.deepcopy((snap, settings, scenes))
        report = analyze(snap, scenes, settings)
        self.assertEqual((snap, settings, scenes), originals)
        self.assertEqual(report['financial']['reserve'], self.snapshot['profile']['reserve'])
        self.assertEqual(self.bank.snapshot(self.uid), self.snapshot)

    def test_no_consent_no_plan(self):
        self.snapshot['profile']['readConsent'] = False
        with self.assertRaises(ValueError):
            analyze(self.snapshot, [], DEFAULT_SETTINGS)

    def test_illiquid_assets_do_not_become_spendable_cash(self):
        first = analyze(self.snapshot, [scene()], DEFAULT_SETTINGS)
        second = analyze(self.snapshot, [scene()], {**DEFAULT_SETTINGS, 'illiquidAssets': 800000})
        self.assertEqual(first['policies'], second['policies'])
        self.assertEqual(first['sensitivity'], second['sensitivity'])
        self.assertEqual(second['financial']['illiquidAssets'], 800000)

    def test_baseline_projection_matches_independent_integer_cash_identity(self):
        item = scene(amount=100.49, preferredDay=3, earliestDay=3, latestDay=3, allowAlternative=False)
        report = analyze(self.snapshot, [item], {**DEFAULT_SETTINGS, 'goalAmount': 0})
        path = report['policies'][0]['projections'][0]['pts']
        expected = [round(value - (100.49 if day >= 3 else 0), 2) for day, value in enumerate(self.snapshot['cash']['pts'])]
        self.assertEqual(path, expected)

    def test_income_delay_removes_original_forecast_before_adding_new_one(self):
        report = analyze(self.snapshot, [], {**DEFAULT_SETTINGS, 'delayDays': 15, 'expenseShock': 0, 'goalAmount': 0})
        salary = next(e for e in self.snapshot['cash']['events'] if e.get('type') == 'salary')
        projected = report['policies'][0]['projections'][1]['pts']
        expected = []
        for day, value in enumerate(self.snapshot['cash']['pts']):
            amount = value - (salary['amount'] if day >= salary['day'] else 0)
            amount += salary['amount'] if day >= salary['day'] + 15 else 0
            expected.append(round(amount, 2))
        self.assertEqual(projected, expected)

    def test_salary_received_is_not_recreated_by_pressure_test(self):
        self.bank.event(self.uid, {'type': 'salary_received', 'event_key': 'salary-once'})
        report = analyze(self.bank.snapshot(self.uid), [], {**DEFAULT_SETTINGS, 'expenseShock': 0, 'goalAmount': 0})
        self.assertEqual(report['policies'][0]['projections'][0]['pts'], report['policies'][0]['projections'][1]['pts'])
        self.assertIsNone(report['breakpoint']['extraDelayDays'])

    def test_essential_payment_never_disappears_or_uses_unapproved_alternative(self):
        item = scene(importance='essential', allowSkip=False, allowAlternative=False, earliestDay=2, latestDay=2)
        report = analyze(self.snapshot, [item], DEFAULT_SETTINGS)
        for policy in report['policies']:
            allocation = policy['allocations'][0]
            self.assertEqual(allocation['amount'], 1200)
            self.assertEqual(allocation['day'], 2)
            self.assertEqual(allocation['mode'], 'original')

    def test_all_choices_respect_user_permission_and_latest_date(self):
        items = [scene(), scene(id='scene2', amount=200, alternativeAmount=100, category='travel', importance='flexible', allowSkip=True)]
        report = analyze(self.snapshot, items, DEFAULT_SETTINGS)
        for policy in report['policies']:
            for allocation in policy['allocations']:
                item = next(s for s in items if s['id'] == allocation['sceneId'])
                self.assertLessEqual(allocation['day'], item['latestDay'])
                self.assertGreaterEqual(allocation['day'], item['earliestDay'])
                if allocation['mode'] == 'skipped': self.assertTrue(item['allowSkip'])
                if allocation['mode'] == 'alternative': self.assertTrue(item['allowAlternative'])

    def test_recorded_scenes_are_not_counted_twice(self):
        report = analyze(self.snapshot, [scene(status='committed')], {**DEFAULT_SETTINGS, 'goalAmount': 0})
        self.assertEqual(report['policies'][0]['allocations'], [])
        self.assertEqual(report['policies'][0]['projections'][0]['pts'], self.snapshot['cash']['pts'])

    def test_existing_gaps_remain_visible_and_disable_goal_allocation(self):
        self.bank.event(self.uid, {'type': 'income_delayed', 'event_key': 'delay-once'})
        report = analyze(self.bank.snapshot(self.uid), [], DEFAULT_SETTINGS)
        for policy in report['policies']:
            self.assertGreater(policy['metrics']['worstGap'], 0)
            self.assertEqual(policy['metrics']['goalAmount'], 0)

    def test_goal_is_robustly_feasible_and_not_real_money_movement(self):
        snapshot = copy.deepcopy(self.snapshot)
        snapshot['profile']['reserve'] = 100
        report = analyze(snapshot, [], {**DEFAULT_SETTINGS, 'expenseShock': 0, 'delayDays': 0})
        for policy in report['policies']:
            self.assertEqual(policy['metrics']['goalAmount'], 500)
            self.assertTrue(all(p['min'] >= 100 for p in policy['projections']))
        self.assertEqual(self.bank.snapshot(self.uid)['balance'], self.snapshot['balance'])

    def test_more_hypothetical_cash_cannot_worsen_fixed_plan_sensitivity(self):
        report = analyze(self.snapshot, [scene()], DEFAULT_SETTINGS)
        rows = report['sensitivity']
        self.assertTrue(all(a['worstGap'] >= b['worstGap'] and a['breachDays'] >= b['breachDays'] for a,b in zip(rows,rows[1:])))
        self.assertFalse(report['search']['globalOptimalityClaimed'])
        self.assertTrue(all(s['probability'] is None for s in report['stressScenarios']))

    def test_exact_money_and_strict_types(self):
        for amount in [True, '100', 0, -1, 1.001, math.nan, math.inf]:
            bad = scene(amount=amount)
            with self.assertRaises(ValueError):validate_scene({k:v for k,v in bad.items() if k not in {'id','status'}})
        for key,value in [('allowSkip',1),('latestDay',True),('category','unknown')]:
            bad = scene(**{key:value})
            with self.assertRaises(ValueError):validate_scene({k:v for k,v in bad.items() if k not in {'id','status'}})
        with self.assertRaises(ValueError):validate_settings({'delayDays':True})
        with self.assertRaises(ValueError):validate_settings({'goalAmount':1_000_001})

    def test_essential_skip_and_unexplained_alternative_rejected(self):
        for item in [scene(importance='essential', allowSkip=True),scene(alternativeLabel='')]:
            with self.assertRaises(ValueError):validate_scene({k:v for k,v in item.items() if k not in {'id','status'}})

    def test_horizon_and_amount_limits(self):
        for item in [scene(preferredDay=31,latestDay=31),scene(earliestDay=5,preferredDay=2),scene(amount=1000001)]:
            with self.assertRaises(ValueError):validate_scene({k:v for k,v in item.items() if k not in {'id','status'}})
        with self.assertRaises(ValueError):analyze(self.snapshot, [scene(id=str(n)) for n in range(11)], DEFAULT_SETTINGS)
