"""Independent arithmetic and matched-environment evaluation checks."""
import json
import unittest
from unittest.mock import patch

from eval.benchmark import (FIXTURES, POLICIES, benchmark, cents,
                            reference_account, reference_cash, reference_event,
                            run_case)


class ReferenceCashTests(unittest.TestCase):
    def setUp(self):
        self.persona = {"balance": 100, "reserve": 20, "daily": 1, "income": 50,
            "salaryDay": 2, "bills": [{"day": 1, "amount": -10}],
            "subs": [{"next": 2, "fee": 5}],
            "plans": [{"day": 1, "amount": -30, "dep": True}]}

    def test_reference_hand_calculated_and_unconfirmed_plans_not_spent(self):
        account = reference_account(self.persona)
        result = reference_cash(account, horizon=2)
        self.assertEqual(result["points_cents"], [10000, 8900, 13300])
        self.assertEqual((result["minimum_cents"], result["minimum_day"], result["free_cents"]),
                         (8900, 1, 6900))

    def test_income_receipt_replaces_forecast_instead_of_double_counting(self):
        account = reference_account(self.persona)
        reference_event(account, {"type": "salary_received"})
        reference_event(account, {"type": "salary_received"})
        self.assertEqual(reference_cash(account, horizon=2)["points_cents"], [15000, 13900, 13300])

    def test_delay_preserves_daily_budget_and_all_bills_subscriptions(self):
        account = reference_account(self.persona)
        reference_event(account, {"type": "income_delayed"})
        self.assertEqual(reference_cash(account, horizon=2)["points_cents"], [10000, 8900, 8300])

    def test_authorised_schedule_would_reserve_cash(self):
        account = reference_account(self.persona)
        account["plans"][0]["status"] = "scheduled"
        self.assertEqual(reference_cash(account, horizon=2)["points_cents"], [10000, 5900, 10300])

    def test_fractional_fen_rejected(self):
        self.assertEqual(cents("0.01"), 1)
        with self.assertRaises(ValueError):
            cents("0.001")


class EvaluationIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = benchmark()

    def test_report_is_complete_matched_grid_not_selected_successes(self):
        report = self.report
        self.assertEqual(report["scenario_count"], 48)
        self.assertEqual(report["strategy_run_count"], 144)
        scenarios = {}
        for row in report["records"]:
            scenarios.setdefault(row["scenario"], []).append(row)
        self.assertEqual(len(scenarios), 48)
        for rows in scenarios.values():
            self.assertEqual({row["policy"] for row in rows}, set(POLICIES))
            self.assertEqual(len({row["oracle_capacity_cents"] for row in rows}), 1)
        self.assertEqual(len(report["fixture_sha256"]), 64)
        self.assertTrue(report["limitations"])

    def test_service_matches_independent_daily_oracle_in_every_run(self):
        self.assertEqual(self.report["failures"], [])
        for row in self.report["records"]:
            self.assertEqual(row["consistency_errors"], [], row["scenario"])
            self.assertLessEqual(row["executed_cents"], row["oracle_capacity_cents"])
            self.assertEqual(row["unauthorised_executions"], 0)
            self.assertEqual(row["duplicate_postings"], 0)

    def test_existing_deficit_is_reported_and_not_called_fixed(self):
        rows = [row for row in self.report["records"] if row["persona"] == "A"
                and row["event"] == "income_delayed" and row["policy"] == "dynamic_profile"]
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row["executed_cents"], 0)
            self.assertFalse(row["reserve_met_after_execution"])
            self.assertTrue(row["safe_target_attained"])

    def test_unexpected_service_failure_is_retained_not_silently_skipped(self):
        persona = json.loads(FIXTURES.read_text())["personas"]["A"]
        with patch("backend.service.BankService.session", side_effect=RuntimeError("injected outage")):
            row = run_case("A", persona, "normal", 100, "dynamic_profile")
        self.assertTrue(row["incomplete"])
        self.assertEqual(row["errors"][0]["type"], "RuntimeError")
        self.assertFalse(row["safe_target_attained"])


if __name__ == "__main__":
    unittest.main()
