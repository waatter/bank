"""Only invented cases and fake provider traffic; never opens sealed suites."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from eval import v3_compare as compare


PROFILE = {"goalName": "自造测试目标", "reserve": 50, "incomeStability": "stable",
           "goalType": "protect", "readConsent": True,
           "assessment": {"horizon": "short", "lossTolerance": "none", "knowledge": "possible"}}


def invented_case(identifier="invented-1", category="synthetic", says=1):
    return {"id": identifier, "category": category, "persona": "A", "profile": deepcopy(PROFILE),
            "steps": [{"type": "say", "text": f"自造消息 {i}",
                       "expect": {"eq": {"intent.action": "bills"}, "ledger": "unchanged"}}
                      for i in range(says)]}


class FixtureSession:
    created = []

    def __init__(self, root, case):
        self.case = deepcopy(case)
        self.current = {"balance": 100, "profile": deepcopy(case.get("profile", PROFILE)),
                        "income": 300, "cash": {"min": 80, "free": 30},
                        "transactions": [], "bills": [], "subscriptions": [], "plans": [],
                        "salaryReceived": False, "goalFunded": 0, "shocked": False,
                        "riskProfile": {"version": "R2", "role": {"key": "steady"}, "reasons": ["当前事实"]},
                        "policy": {"priorities": ["derived_should_not_leak"]},
                        "confirmationToken": "opaque-controller-only"}
        self.history = []
        self.tools = []
        self.inside = False
        self.provenance = {"source_root": str(root), "actual_source_sha256": "invented"}

    def __enter__(self):
        self.created.append(self)
        return self

    def __exit__(self, *args):
        pass

    def snapshot(self):
        return deepcopy(self.current)

    def prompt_state(self):
        return {"snapshot": self.snapshot(), "history": deepcopy(self.history),
                "declarations": PROFILE, "income": 300, "payees": [{"id": "invented_payee"}]}

    def tool(self, action, arguments):
        if not self.inside:
            raise RuntimeError("tools must be inside say_fn controller scope")
        self.tools.append((action, deepcopy(arguments)))
        result = {"intent": {"action": action}, "snapshot": self.snapshot()}
        self.history.append({"type": "say", "result": deepcopy(result)})
        if action == "transfer":
            result["proposal"] = {"to": arguments.get("to"), "amount": arguments.get("amount"),
                                  "confirmationToken": "opaque-controller-only"}
        return result

    def run_step(self, step, say_fn=None):
        if step["type"] == "say":
            if say_fn is None:
                raise RuntimeError("must not invoke rule selector")
            self.inside = True
            try:
                return say_fn(self, step["text"])
            finally:
                self.inside = False
        if step["type"] == "event":
            self.current["shocked"] = True
            self.current["balance"] = 75
            self.current["riskProfile"]["role"]["key"] = "buffer"
        if step["type"] == "confirm_transfer":
            raise ValueError("no matching original draft")
        result = {"snapshot": self.snapshot()}
        self.history.append({"type": step["type"], "result": result})
        return result


def fixture_score(expect, result, before, after, step_type):
    errors = []
    for path, wanted in expect.get("eq", {}).items():
        got = result
        for key in path.split("."):
            got = got.get(key) if isinstance(got, dict) else None
        if wanted != got:
            errors.append({"path": path, "actual": got})
    unsafe = step_type == "say" and before["balance"] != after["balance"]
    return {"passed": not errors and not unsafe, "errors": errors, "unauthorized_change": unsafe,
            "wrong_draft": False, "false_refusal": False, "safe_refusal": False}


FIXTURE_HOLDOUT = SimpleNamespace(load_cases=lambda path: json.loads(Path(path).read_text(encoding='utf-8')),
                                  TaskSession=FixtureSession, score_step=fixture_score)


class FixtureTransport:
    def __init__(self, content='{"action":"bills","arguments":{}}', exception=None):
        self.messages, self.content, self.exception = [], content, exception
        self.lock = threading.Lock()

    def __call__(self, messages):
        with self.lock:
            self.messages.append(deepcopy(messages))
        if self.exception:
            raise self.exception
        return {"content": self.content, "model": compare.MODEL,
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                "finish_reason": "stop"}


class TemporaryInputs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v3-compare-invented-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        backend = self.root / "backend"
        backend.mkdir()
        (backend / "__init__.py").write_text("# invented source\n", encoding='utf-8')
        (backend / "fixtures.json").write_text(json.dumps({"personas": {
            "A": {"income": 300, "salaryDay": 5, "goal": {"target": 1000}}}}), encoding='utf-8')
        self.suite = self.root / "invented_tasks.json"
        self.write_suite([invented_case()])
        FixtureSession.created = []

    def write_suite(self, cases):
        self.suite.write_text(json.dumps({"schema": "TROPOS-V3-TASKS-1", "origin": {"author": "invented"},
                                          "cases": cases}, ensure_ascii=False), encoding='utf-8')

    def run_transport(self, transport=None, **kwargs):
        return compare.run_comparison(self.suite, self.root, transport=transport or FixtureTransport(),
                                      holdout=FIXTURE_HOLDOUT, **kwargs)


class SelectionTests(unittest.TestCase):
    def test_caps_category_coverage_and_input_order_independence(self):
        cases = [invented_case(f"i{i}", f"c{i % 5}", 3 + i % 4) for i in range(70)]
        chosen, decisions = compare.select_cases(cases)
        other, _ = compare.select_cases(list(reversed(cases)))
        self.assertEqual([c["id"] for c in chosen], [c["id"] for c in other])
        self.assertLessEqual(len(chosen), 12)
        self.assertLessEqual(sum(len(c["steps"]) for c in chosen), 40)
        self.assertEqual({c["category"] for c in chosen}, {f"c{i}" for i in range(5)})
        self.assertEqual(len(decisions), len(cases))

    def test_oracles_profiles_text_events_and_results_do_not_affect_selection(self):
        cases = [invented_case(f"i{i}", f"c{i % 3}", 2) for i in range(40)]
        modified = deepcopy(cases)
        for case in modified:
            case["profile"] = {"anything": "unrelated"}
            case["events"] = ["income_delayed"]
            for step in case["steps"]:
                step["text"] = "other text"
                step["expect"] = {"eq": {"impossible": True}}
        a, _ = compare.select_cases(cases)
        b, _ = compare.select_cases(modified)
        self.assertEqual([c["id"] for c in a], [c["id"] for c in b])

    def test_small_budget_selects_complete_three_arm_tasks_only(self):
        chosen, _ = compare.select_cases([invented_case("a", "x", 2), invented_case("b", "y", 1)], 4)
        self.assertEqual([c["id"] for c in chosen], ["b"])
        for value in (-1, 121, 1.2):
            with self.assertRaises(ValueError):
                compare.select_cases([], value)

    def test_concurrent_budget_has_hard_cap_even_after_errors(self):
        budget = compare.CallBudget(120)
        def claim(_):
            try:
                return budget.claim()
            except compare.BudgetExhausted:
                return None
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(claim, range(180)))
        self.assertEqual(budget.count, 120)
        self.assertEqual(sorted(v for v in results if v), list(range(1, 121)))


class PromptAndParsingTests(unittest.TestCase):
    def test_common_financial_facts_and_only_presentation_differs(self):
        snapshot = {"profile": PROFILE, "balance": 80, "income": 300, "cash": {"free": 30},
                    "riskProfile": {"version": "R2", "role": {"key": "buffer"}, "reasons": ["present cash"]},
                    "policy": "do not leak", "confirmationToken": "hidden-approval"}
        history = [{"user": f"past-{i}"} for i in range(30)]
        prompts = [compare.make_messages(arm, snapshot, {"history": history * 2, "income": 300},
                    "new", history, PROFILE, {"income": 300}) for arm in compare.ARMS]
        bodies = [json.loads(m[1]["content"]) for m in prompts]
        self.assertEqual(len({m[0]["content"] for m in prompts}), 1)
        for body in bodies:
            self.assertEqual(body["current_facts"]["profile"]["assessment"], PROFILE["assessment"])
            self.assertEqual(body["current_facts"]["profile"]["reserve"], 50)
            self.assertEqual(body["public_financial_facts"]["income"], 300)
            self.assertEqual(len(body["memory"]), compare.MEMORY_TURNS)
            self.assertNotIn("history", body["task_state"])
            self.assertNotIn("riskProfile", body["current_facts"])
            self.assertNotIn("confirmationToken", body["current_facts"])
        self.assertNotIn("confirmed_profile_summary", bodies[0])
        self.assertEqual(bodies[1]["confirmed_profile_summary"], bodies[2]["confirmed_profile_summary"])
        self.assertEqual(bodies[2]["current_R2_role_explanation"], snapshot["riskProfile"])
        for body in bodies:
            body.pop("confirmed_profile_summary", None)
            body.pop("current_R2_role_explanation", None)
        self.assertEqual(bodies[0], bodies[1])
        self.assertEqual(bodies[1], bodies[2])

    def test_strict_json_no_execution_no_repair_no_coercion(self):
        invalid = ["```json\n{}\n```", '{"action":"confirm_transfer","arguments":{}}',
                   '{"action":"execute","arguments":{}}', '{"action":"bills","arguments":{},"extra":1}',
                   '{"action":"bills","action":"plan","arguments":{}}',
                   '{"action":"transfer","arguments":{"amount":NaN}}', '[]',
                   '{"action":"transfer","arguments":{"confirmationToken":"hidden"}}',
                   '{"action":"bills","arguments":{},"args":{}}']
        for content in invalid:
            with self.subTest(content=content), self.assertRaises(compare.InvalidAction):
                compare.parse_action(content)
        self.assertEqual(compare.parse_action('{"action":"bills","args":{}}'), ("bills", {}))
        # Parameter type validation belongs to TaskSession.tool, without repairs.
        self.assertEqual(compare.parse_action('{"action":"transfer","arguments":{"amount":"12"}}')[1]["amount"], "12")

    def test_transport_unified_parameters_retry_and_timeout(self):
        captured = {}
        class Client:
            def with_options(self, **kwargs):
                captured["options"] = kwargs
                return self
            @property
            def chat(self):
                return SimpleNamespace(completions=SimpleNamespace(create=self.create))
            def create(self, **kwargs):
                captured["request"] = kwargs
                return SimpleNamespace(model=compare.MODEL, usage=SimpleNamespace(prompt_tokens=7, completion_tokens=4, total_tokens=11),
                    choices=[SimpleNamespace(message=SimpleNamespace(content='{"action":"bills","arguments":{}}'), finish_reason="stop")])
        client = Client()
        fake_spec = SimpleNamespace(loader=SimpleNamespace(exec_module=lambda module: None))
        module = SimpleNamespace(_get_deepseek_client=lambda: client)
        with patch.object(compare.importlib.util, "spec_from_file_location", return_value=fake_spec), \
             patch.object(compare.importlib.util, "module_from_spec", return_value=module):
            transport = compare.DeepSeekTransport()
            response = transport([{"role": "user", "content": "invented"}])
        self.assertEqual(captured["options"], {"timeout": 25, "max_retries": 0})
        self.assertEqual({k: captured["request"][k] for k in ("model", "temperature", "max_tokens", "reasoning_effort")},
                         {"model": compare.MODEL, "temperature": 0, "max_tokens": 1200, "reasoning_effort": "none"})
        self.assertEqual(response["usage"]["total_tokens"], 11)
        self.assertNotIn("tools", captured["request"])


class ComparisonTests(TemporaryInputs):
    def test_not_live_is_only_plan_and_never_loads_client_or_session(self):
        with patch.object(compare, "_load_client", side_effect=AssertionError("API forbidden")):
            report = compare.run_comparison(self.suite, self.root, holdout=FIXTURE_HOLDOUT)
        self.assertEqual(report["status"], "not_run")
        self.assertEqual(report["call_count"], 0)
        self.assertEqual(report["records"], [])
        self.assertIsNone(report["metrics"])
        self.assertFalse(FixtureSession.created)

    def test_mock_even_live_flag_has_no_model_scores(self):
        transport = FixtureTransport()
        report = self.run_transport(transport, live_model=True)
        self.assertEqual(report["status"], "transport_test")
        self.assertIsNone(report["metrics"])
        self.assertEqual(report["call_count"], 3)
        self.assertEqual(len(report["records"]), 3)
        self.assertTrue(all(r["passed"] for r in report["records"]))
        self.assertEqual({r["arm"] for r in report["records"]}, set(compare.ARMS))
        self.assertTrue(all(len(s.tools) == 1 for s in FixtureSession.created))
        self.assertTrue(all(r["source"]["suite_sha256"] == report["suite_sha256"] for r in report["records"]))
        self.assertIn("没有模型成绩", compare.markdown_report(report))
        self.assertNotIn("opaque-controller-only", json.dumps(compare.clean(report)))

    def test_bad_json_is_one_call_and_no_fallback_but_later_steps_still_run(self):
        case = invented_case()
        case["steps"] += [{"type": "recover"}, {"type": "say", "text": "下一条"}]
        self.write_suite([case])
        transport = FixtureTransport("not-json")
        report = self.run_transport(transport)
        self.assertEqual(report["call_count"], 6)
        self.assertEqual(len(transport.messages), 6)
        self.assertTrue(all(not r["passed"] for r in report["records"]))
        for row in report["records"]:
            self.assertEqual(len(row["steps"]), 3)
            self.assertTrue(row["steps"][1]["passed"])
            self.assertTrue(all(c["badJSON"] for c in row["calls"]))
        self.assertTrue(all(not session.tools for session in FixtureSession.created))

    def test_upstream_exceptions_sanitized_counted_and_never_retried(self):
        secret = "private-auth-must-never-appear"
        error = RuntimeError("Authorization Bearer " + secret)
        error.code = secret
        transport = FixtureTransport(exception=error)
        report = self.run_transport(transport)
        self.assertEqual(report["call_count"], 3)
        self.assertEqual(len(transport.messages), 3)
        serialized = json.dumps(report) + compare.markdown_report(report)
        self.assertNotIn(secret, serialized)
        self.assertTrue(all(c["upstream_exception"] == {"class": "RuntimeError"}
                            for r in report["records"] for c in r["calls"]))
        self.assertTrue(all(not r["passed"] for r in report["records"]))

    def test_future_events_and_oracle_never_enter_prompt_setup_applied(self):
        case = invented_case()
        case["events"] = ["bill_added"]
        case["steps"][0]["expect"] = {"eq": {"oracle_marker": "hidden_answer"}}
        case["steps"] += [{"type": "event", "event": "income_delayed"},
                           {"type": "say", "text": "事件后消息"}]
        self.write_suite([case])
        transport = FixtureTransport()
        report = self.run_transport(transport)
        self.assertEqual(report["call_count"], 6)
        for index, messages in enumerate(transport.messages):
            payload = json.loads(messages[1]["content"])
            self.assertNotIn("hidden_answer", messages[1]["content"])
            self.assertNotIn("oracle_marker", messages[1]["content"])
            self.assertEqual(payload["current_facts"]["balance"], 75)
            if index % 2 == 0:
                self.assertNotIn("income_delayed", messages[1]["content"])
            else:
                self.assertIn("income_delayed", messages[1]["content"])
        self.assertTrue(all(r["steps"][0]["setup"] for r in report["records"]))

    def test_source_digest_excludes_private_task_directories(self):
        before = compare.source_manifest(self.root)
        private = self.root / ".local"
        private.mkdir()
        (private / "never-read.json").write_text("private contents")
        self.assertEqual(before, compare.source_manifest(self.root))

    def test_setup_failure_keeps_every_task_and_step_denominator(self):
        class BrokenSession(FixtureSession):
            def __enter__(self):
                raise RuntimeError("not persisted")
        fake = SimpleNamespace(load_cases=FIXTURE_HOLDOUT.load_cases, TaskSession=BrokenSession, score_step=fixture_score)
        report = compare.run_comparison(self.suite, self.root, transport=FixtureTransport(), holdout=fake)
        self.assertEqual(len(report["records"]), 3)
        self.assertEqual(report["call_count"], 0)
        summary = compare.metrics(report["records"])
        for arm in compare.ARMS:
            self.assertEqual(summary["arms"][arm]["task"]["total"], 1)
            self.assertEqual(summary["arms"][arm]["step"]["total"], 1)
            self.assertEqual(summary["arms"][arm]["safety"]["unscorable_steps"], 1)

    def test_report_exclusive_creation_before_any_api(self):
        output = self.root / "first.md"
        output.write_text("original")
        with patch.object(compare, "_load_client", side_effect=AssertionError("must not call API")), \
             self.assertRaises(SystemExit):
            compare.main(["--suite", str(self.suite), "--source-root", str(self.root),
                          "--output", str(output), "--live-model"])
        self.assertEqual(output.read_text(), "original")

    def test_actual_holdout_interface_with_invented_task_no_live_model(self):
        # Validate suite loading against the other worker's real API. The fake
        # source is never executed and the provider is never initialized.
        from eval import v3_holdout
        report = compare.run_comparison(self.suite, self.root, holdout=v3_holdout)
        self.assertEqual(report["status"], "not_run")
        self.assertEqual(report["planned_calls"], 3)

    def test_real_task_session_gateway_with_invented_case_and_fake_transport(self):
        from eval import v3_holdout
        case = invented_case()
        case["profile"] = {"readConsent": True}
        case["steps"] += [{"type": "recover"}, {"type": "say", "text": "自造第二条消息", "expect": {
            "eq": {"intent.action": "bills"}}}]
        self.write_suite([case])
        root = Path(compare.__file__).resolve().parents[1]
        with patch.object(compare, "_load_client", side_effect=AssertionError("no external API")):
            report = compare.run_comparison(self.suite, root, transport=FixtureTransport(), holdout=v3_holdout)
        self.assertEqual(report["status"], "transport_test")
        self.assertEqual(report["call_count"], 6)
        self.assertIsNone(report["metrics"])
        for row in report["records"]:
            self.assertTrue(row["passed"], row)
            self.assertTrue(row["gateway_source"]["imported_modules"])

    def test_redaction_retains_usage_counts_and_parameter_limits(self):
        value = compare.clean({"usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                               "max_tokens": 1200, "confirmationToken": "never"})
        self.assertEqual(value["usage"]["total_tokens"], 15)
        self.assertEqual(value["max_tokens"], 1200)
        self.assertNotIn("confirmationToken", value)


class StatisticsTests(unittest.TestCase):
    def test_wilson_and_paired_ties_retain_uncertainty(self):
        interval = compare.wilson(0, 12)
        self.assertEqual(interval["rate"], 0)
        self.assertGreater(interval["ci95"][1], .2)
        pair = compare.paired_interval([0] * 12)
        self.assertEqual(pair["difference"], 0)
        self.assertLess(pair["ci95"][0], 0)
        self.assertGreater(pair["ci95"][1], 0)
        self.assertIsNone(compare.wilson(0, 0)["rate"])


if __name__ == "__main__":
    unittest.main()
