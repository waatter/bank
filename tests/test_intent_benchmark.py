"""Benchmark contract checks; no paid API calls or fake live-model grades."""
import json
import os
import unittest
from unittest.mock import patch

from eval.intent_benchmark import aggregate, benchmark, load_cases, markdown, score_case


def observed(intent, proposal=None, **changes):
    return dict({'intent': intent, 'proposal': proposal, 'ledger_unchanged': True,
                 'transaction_returned': False, 'execution_claim': False}, **changes)


class IntentScoringTests(unittest.TestCase):
    def setUp(self):
        self.dataset=load_cases()
        self.transfer=next(case for case in self.dataset['cases'] if case['id']=='amount-integer')
        self.refusal=next(case for case in self.dataset['cases'] if case['id']=='unsupported-correction')

    def test_fixed_dataset_explicitly_covers_required_slices(self):
        cases=self.dataset['cases']
        self.assertGreaterEqual(len(cases),24)
        self.assertEqual(len({case['id'] for case in cases}),len(cases))
        self.assertTrue({'amount','invalid_amount','missing','recipient','ambiguity','salary',
                         'injection','bills','subscriptions','plan','purchase','correction'} <=
                        {case['category'] for case in cases})
        for case in cases:
            self.assertTrue(case['expected']['allowed_actions'])
            self.assertIsInstance(case['expected']['fields'],dict)
            if case['category']=='correction':
                self.assertEqual(case['expected']['allowed_actions'],['clarify'])
                self.assertTrue(case['expected']['requires_safe_refusal'])

    def test_correct_draft_scores_parameters_but_never_payment(self):
        fields=self.transfer['expected']['fields']
        result=score_case(self.transfer,observed(dict(action='transfer',**fields),fields))
        self.assertTrue(result['passed'])
        self.assertTrue(result['draft_execution_boundary_preserved'])
        self.assertIsNone(result['safe_refusal_correct'])

    def test_action_credit_does_not_hide_wrong_amount_or_condition(self):
        fields=self.transfer['expected']['fields']
        for field,value in [('amount',1),('when','salary'),('memo','invented'),('to','wl1')]:
            wrong=dict(fields,**{field:value})
            with self.subTest(field=field):
                result=score_case(self.transfer,observed(dict(action='transfer',**wrong),wrong))
                self.assertTrue(result['action_correct'])
                self.assertFalse(result['complete_parameters_correct'])
                self.assertFalse(result['passed'])

    def test_proposal_and_intent_must_both_match(self):
        fields=self.transfer['expected']['fields']
        for proposal in [None,dict(fields,amount=10)]:
            result=score_case(self.transfer,observed(dict(action='transfer',**fields),proposal))
            self.assertTrue(result['action_correct'])
            self.assertFalse(result['complete_parameters_correct'])

    def test_unsupported_is_not_a_successful_clarification(self):
        for intent in [{'action':'unsupported'},{'action':'transfer','to':'mom','amount':200}]:
            result=score_case(self.refusal,observed(intent))
            self.assertFalse(result['action_correct'])
            self.assertFalse(result['safe_refusal_correct'])

    def test_exception_cannot_receive_credit_as_a_refusal(self):
        result=score_case(self.refusal,observed({'action':'clarify'},error_type='ModelFailure'))
        self.assertFalse(result['safe_refusal_correct'])
        self.assertFalse(result['passed'])

    def test_clarification_cannot_leave_a_proposal(self):
        result=score_case(self.refusal,observed({'action':'clarify'},{'amount':200}))
        self.assertTrue(result['action_correct'])
        self.assertFalse(result['complete_parameters_correct'])
        self.assertFalse(result['safe_refusal_correct'])

    def test_execution_or_success_claim_fails_even_with_correct_intent(self):
        for change in [{'ledger_unchanged':False},{'transaction_returned':True},{'execution_claim':True}]:
            result=score_case(self.refusal,observed({'action':'clarify'},**change))
            self.assertFalse(result['draft_execution_boundary_preserved'])
            self.assertFalse(result['safe_refusal_correct'])
            self.assertFalse(result['passed'])

    def test_refusal_denominator_excludes_regular_queries(self):
        safe=score_case(self.refusal,observed({'action':'clarify'}))
        normal=score_case(self.transfer,observed({'action':'clarify'}))
        summary=aggregate([safe,normal])
        self.assertEqual(summary['cases'],2)
        self.assertEqual(summary['safe_refusal'],{'passed':1,'total':1,'rate':1})


class IntentRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Explicit nonempty env must still never turn a default run into an API call.
        with patch.dict(os.environ,{'TROPOS_LLM_API_KEY':'SENTINEL-DO-NOT-SAVE',
                                    'DEEPSEEK_API_KEY':'SENTINEL-SECOND-KEY'}), \
             patch('backend.model.IntentPlanner._llm_intent',side_effect=AssertionError('provider forbidden')), \
             patch('backend.service.BankService.confirm',side_effect=AssertionError('execution forbidden')):
            cls.report=benchmark()

    def test_all_cases_retained_with_actual_mode_and_no_live_grade(self):
        report=self.report
        self.assertEqual(report['requested_mode'],'rules')
        self.assertFalse(report['live_model_run'])
        self.assertIsNone(report['live_model_only_summary'])
        self.assertEqual(report['live_model_response_count'],0)
        self.assertEqual(len(report['records']),len(load_cases()['cases']))
        self.assertEqual(sum(report['actual_modes'].values()),len(report['records']))
        self.assertTrue(set(report['actual_modes']) <= {'rules','error'})
        self.assertEqual(report['failures'],[r['id'] for r in report['records'] if not r['passed']])
        self.assertIn('未运行；未生成live成绩',markdown(report))

    def test_reports_are_reproducible_and_do_not_capture_credentials(self):
        report=self.report
        encoded=json.dumps(report,ensure_ascii=False)
        self.assertNotIn('SENTINEL-DO-NOT-SAVE',encoded)
        self.assertNotIn('SENTINEL-SECOND-KEY',encoded)
        for forbidden in ['confirmationToken','api_key','Authorization','token_hash']:
            self.assertNotIn(forbidden,encoded)
        self.assertEqual(len(report['case_sha256']),64)
        self.assertIn('backend/model.py',report['source_sha256'])
        self.assertIn('backend/service.py',report['source_sha256'])
        self.assertIn('eval/intent_benchmark.py',report['source_sha256'])
        self.assertTrue(all(len(value)==64 for value in report['source_sha256'].values()))
        self.assertTrue(report['limitations'])

    def test_real_rules_never_change_financial_state_or_settle(self):
        for row in self.report['records']:
            self.assertTrue(row['draft_execution_boundary_preserved'],row['id'])
            if row['observed']['proposal']:
                self.assertNotEqual(row['observed']['proposal']['status'],'settled')

    def test_live_requires_explicit_config_and_does_not_fabricate_a_score(self):
        with patch.dict(os.environ,{'TROPOS_LLM_API_KEY':'','DEEPSEEK_API_KEY':''}), \
             patch('eval.intent_benchmark.run_case',side_effect=AssertionError('must not run')):
            with self.assertRaisesRegex(ValueError,'no live score generated'):
                benchmark(live_model=True)


if __name__=='__main__':
    unittest.main()
