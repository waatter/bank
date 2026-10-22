"""Independent hand arithmetic, fault detection and complete synthetic-grid checks."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from backend.life_planner import analyze
from eval.life_benchmark import (CATALOGUE, POLICIES, SETTINGS, STRESSES, benchmark,
                                 cents, oracle_path, permission_errors, run_case,
                                 verify_policy, verify_sensitivity)


def small_snapshot():
    return {'balance': 100, 'goalFunded': 9000, 'user': {'revision': 1},
            'profile': {'readConsent': True, 'reserve': 20},
            'cash': {'pts': [100 - day + (50 if day >= 2 else 0) for day in range(31)],
                     'events': [{'type': 'salary', 'day': 2, 'amount': 50}]}}


def small_scene(**changes):
    result = {'id': 'known-scene', 'title': '额外维修', 'category': 'work', 'amount': 10,
              'earliestDay': 1, 'preferredDay': 1, 'latestDay': 5,
              'importance': 'important', 'allowSkip': False, 'allowAlternative': True,
              'alternativeAmount': 5, 'alternativeLabel': '用户允许的基础维修', 'status': 'draft'}
    result.update(changes)
    return result


class CashOracleTests(unittest.TestCase):
    def test_hand_calculated_combined_path_and_no_planner_helpers(self):
        allocation = {'sceneId': 'known-scene', 'day': 1, 'amount': 10, 'mode': 'original'}
        settings = {**SETTINGS, 'delayDays': 3, 'expenseShock': 7, 'shockDay': 3}
        with patch('backend.life_planner._paths', side_effect=AssertionError('not independent')), \
             patch('backend.life_planner._stress_bases', side_effect=AssertionError('not independent')):
            points = oracle_path(small_snapshot(), [allocation], settings, 'combined', goal_amount=5)
        self.assertEqual(points[:6], [9500, 8400, 8300, 7500, 7400, 12300])

    def test_salary_outside_horizon_is_removed_not_credited_today(self):
        snapshot = small_snapshot()
        snapshot['cash'] = {'pts': [100 + (50 if day >= 29 else 0) for day in range(31)],
                            'events': [{'type': 'salary', 'day': 29, 'amount': 50}]}
        settings = {**SETTINGS, 'delayDays': 3, 'expenseShock': 0}
        points = oracle_path(snapshot, [], settings, 'income_delay')
        self.assertEqual(points, [10000] * 31)
        self.assertEqual(snapshot['balance'], 100)

    def test_received_salary_is_not_added_again(self):
        snapshot = small_snapshot()
        snapshot['cash']['events'] = []
        self.assertEqual(oracle_path(snapshot, [], SETTINGS, 'baseline'),
                         oracle_path(snapshot, [], SETTINGS, 'income_delay'))

    def test_extra_cash_is_only_a_sensitivity_assumption(self):
        snapshot = small_snapshot()
        original = deepcopy(snapshot)
        baseline = oracle_path(snapshot, [], SETTINGS, 'combined')
        hypothetical = oracle_path(snapshot, [], SETTINGS, 'combined', extra_cash=500)
        self.assertEqual(hypothetical, [value + 50000 for value in baseline])
        self.assertEqual(snapshot, original)

    def test_strict_fen_conversion(self):
        self.assertEqual(cents(0.01), 1)
        for value in (True, 0.001, float('nan'), float('inf')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                cents(value)


class FaultDetectionTests(unittest.TestCase):
    def test_oracle_catches_changed_daily_point_and_aggregate_metric(self):
        snapshot, scenes = small_snapshot(), [small_scene()]
        settings = {**SETTINGS, 'goalAmount': 0, 'expenseShock': 0}
        policy = analyze(snapshot, scenes, settings)['policies'][0]
        self.assertEqual(verify_policy(snapshot, scenes, settings, policy)['errors'], [])
        corrupted = deepcopy(policy)
        corrupted['projections'][0]['pts'][2] += 0.01
        corrupted['metrics']['worstGap'] += 1
        fields = {error['field'] for error in verify_policy(snapshot, scenes, settings, corrupted)['errors']}
        self.assertIn('daily_cash', fields)
        self.assertIn('worstGap', fields)

    def test_permission_oracle_catches_skipped_essential_and_unapproved_alternative(self):
        essential = small_scene(importance='essential', allowSkip=False, allowAlternative=False)
        for allocation in ({'sceneId': essential['id'], 'day': 1, 'amount': 0, 'mode': 'skipped'},
                           {'sceneId': essential['id'], 'day': 1, 'amount': 5, 'mode': 'alternative'},
                           {'sceneId': essential['id'], 'day': 6, 'amount': 10, 'mode': 'delayed'}):
            self.assertTrue(permission_errors([essential], [allocation]))

    def test_missing_or_duplicate_scene_cannot_silently_pass(self):
        scene = small_scene()
        allocation = {'sceneId': scene['id'], 'day': 1, 'amount': 10, 'mode': 'original'}
        self.assertTrue(permission_errors([scene], []))
        self.assertTrue(permission_errors([scene], [allocation, allocation]))
        self.assertEqual(permission_errors([small_scene(status='committed')], []), [])

    def test_oracle_catches_fabricated_sensitivity_improvement(self):
        snapshot, scenes = small_snapshot(), [small_scene()]
        result = analyze(snapshot, scenes, SETTINGS)
        policy = next(row for row in result['policies'] if row['id'] == 'balanced')
        self.assertEqual(verify_sensitivity(snapshot, SETTINGS, policy, result['sensitivity']), [])
        changed = deepcopy(result['sensitivity'])
        changed[0]['min'] += 500
        self.assertTrue(verify_sensitivity(snapshot, SETTINGS, policy, changed))

    def test_known_gap_does_not_disappear_or_fund_goal_with_future_salary(self):
        snapshot = {'balance': 100, 'user': {'revision': 1},
                    'profile': {'readConsent': True, 'reserve': 50},
                    'cash': {'pts': [100] * 4 + [300] * 27,
                             'events': [{'type': 'salary', 'day': 4, 'amount': 200}]}}
        scenes = [small_scene(amount=60, importance='essential', earliestDay=2,
                              preferredDay=2, latestDay=2, allowAlternative=False)]
        settings = {**SETTINGS, 'goalAmount': 50, 'expenseShock': 0, 'delayDays': 3}
        for policy in analyze(snapshot, scenes, settings)['policies']:
            self.assertEqual(policy['allocations'][0]['amount'], 60)
            self.assertEqual(policy['metrics']['goalAmount'], 0)
            self.assertEqual(policy['metrics']['worstGap'], 10)
            self.assertEqual(verify_policy(snapshot, scenes, settings, policy)['errors'], [])

    def test_unexpected_failure_is_retained_as_incomplete(self):
        template = json.loads(CATALOGUE.read_text())[0]
        with patch('eval.life_benchmark.analyze', side_effect=RuntimeError('injected outage')):
            result = run_case('A', 'injected', [template])
        self.assertTrue(result['incomplete'])
        self.assertEqual(result['checked_paths'], 0)
        self.assertEqual(result['errors'][0]['type'], 'RuntimeError')


class CompleteGridTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = benchmark()

    def test_all_known_inputs_and_paths_are_retained(self):
        report = self.report
        self.assertEqual(report['input_count'], 30)
        self.assertEqual(report['checked_path_count'], 360)
        self.assertEqual(report['checked_daily_point_count'], 11160)
        self.assertEqual(len(report['records']), 30)
        for persona in ('A', 'B', 'C'):
            rows = [row for row in report['records'] if row['persona'] == persona]
            self.assertEqual(len(rows), 10)
            self.assertEqual(sum(len(row['input']['scenes']) == 1 for row in rows), 8)
        for record in report['records']:
            self.assertEqual(tuple(row['id'] for row in record['policies']), POLICIES)
            self.assertTrue(record['bank_snapshot_unchanged'])
            self.assertEqual(record['checked_paths'], 12)
            for policy in record['policies']:
                self.assertEqual({row['scenarioId'] for row in policy['projections']}, set(STRESSES))

    def test_every_known_case_passes_independent_cash_and_permission_checks(self):
        self.assertEqual(self.report['failures'], [])
        self.assertTrue(all(not row['incomplete'] and row['errors'] == [] for row in self.report['records']))
        # Existing gaps must remain visible; zero arithmetic failures is not
        # equivalent to claiming all generated arrangements are feasible.
        self.assertTrue(any(policy['metrics']['worstGap'] > 0
                            for row in self.report['records'] for policy in row['policies']))

    def test_provenance_and_non_blind_boundaries_are_explicit(self):
        report = self.report
        self.assertFalse(report['random_sampling'])
        self.assertEqual(len(report['source_sha256']), 64)
        self.assertEqual(len(report['catalogue_sha256']), 64)
        self.assertTrue(report['source_files_sha256'])
        self.assertIn('revision', report['git'])
        self.assertIn('dirty', report['git'])
        self.assertTrue(any('不是盲测' in item for item in report['limitations']))
        self.assertTrue(any('不表示界面可以自动' in item for item in report['limitations']))


if __name__ == '__main__':
    unittest.main()
