import json
import unittest
from pathlib import Path

from eval.redteam import CASES, benchmark


class RedTeamRegressionTest(unittest.TestCase):
    """Every developer-authored attack must stay intercepted and every control must stay executable."""

    @classmethod
    def setUpClass(cls):
        cls.report = benchmark()

    def test_cases_are_unique_and_categorized(self):
        dataset = json.loads(CASES.read_text(encoding='utf-8'))
        ids = [case['id'] for case in dataset['cases']]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue({case['category'] for case in dataset['cases']} <= set(dataset['categories']))

    def test_no_regression(self):
        failed = [row['id'] for row in self.report['records'] if not row['passed']]
        self.assertEqual(failed, [])
        summary = self.report['summary']
        self.assertGreaterEqual(summary['conversation_attacks']['total'], 40)
        self.assertGreaterEqual(summary['api_attacks']['total'], 10)
        self.assertGreaterEqual(summary['controls']['total'], 8)

    def test_report_states_its_limits(self):
        self.assertIn('不是留出集', ''.join(self.report['limitations']))
        self.assertEqual(self.report['mode'], 'rules')


if __name__ == '__main__':
    unittest.main()
