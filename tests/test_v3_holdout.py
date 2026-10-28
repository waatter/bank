"""Self-constructed fixtures only. Never opens or discovers a holdout suite."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from eval.v3_holdout import (ProtocolError, TaskSession, _backend, _markdown,
                             _publish, load_cases, main, redact, run_suite, score_step)


ROOT = Path(__file__).resolve().parents[1]
FROZEN = ROOT / ".local/v3-readiness-20261027T132749Z/frozen-source"


def case(steps=None, **kwargs):
    result = {"id": "made-up", "category": "unit", "persona": "C",
              "steps": steps or [{"type": "say", "text": "查看账单"}]}
    result.update(kwargs)
    return result


def suite(cases=None):
    return {"schema": "TROPOS-V3-TASKS-1", "origin": {"kind": "self-constructed-unit-test"},
            "cases": cases or [case()]}


class SchemaTests(unittest.TestCase):
    def load(self, value):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tiny.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            return load_cases(path)

    def test_load_returns_complete_suite(self):
        value = suite()
        self.assertEqual(self.load(value), value)

    def test_bad_schemas_and_dsl_fail(self):
        mutations = [lambda s: s.update(schema="future"),
                     lambda s: s.update(extra=1),
                     lambda s: s["cases"].append(copy.deepcopy(s["cases"][0])),
                     lambda s: s["cases"][0].update(persona="D"),
                     lambda s: s["cases"][0]["steps"][0].update(type="execute"),
                     lambda s: s["cases"][0]["steps"][0].update(url="https://example.invalid"),
                     lambda s: s["cases"][0]["steps"][0].update(expect={"eq": {"proposal[0]": 1}}),
                     lambda s: s["cases"][0]["steps"][0].update(expect={"eq": {"__class__.run()": 1}}),
                     lambda s: s["cases"][0]["steps"][0].update(expect={"one_of": {"x": []}}),
                     lambda s: s["cases"][0]["steps"][0].update(expect={"has": "proposal.id"}),
                     lambda s: s["cases"][0]["steps"][0].update(expect={"ledger": "anything"}),
                     lambda s: s["cases"][0].update(events=[{"type": "salary_received"}])]
        for mutation in mutations:
            value = suite()
            mutation(value)
            with self.subTest(value=value), self.assertRaises((ProtocolError, ValueError)):
                self.load(value)

    def test_typed_confirm_fields(self):
        for amount in (True, "12", 0, -1, 1.001, float("nan"), float("inf")):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                self.load(suite([case([{"type": "confirm_transfer", "to": "mom", "amount": amount}])]))

    def test_duplicate_json_key_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tiny.json"
            path.write_text('{"schema":"x","schema":"TROPOS-V3-TASKS-1"}')
            with self.assertRaises(ProtocolError):
                load_cases(path)


class ScoringTests(unittest.TestCase):
    def test_decimal_and_null_paths(self):
        expected = {"eq": {"proposal.amount": 12.10}, "one_of": {"intent.action": ["transfer", "clarify"]},
                    "has": ["proposal.id"], "absent": ["receipt", "transaction"]}
        result = {"proposal": {"id": "random", "amount": 12.1}, "intent": {"action": "transfer"}, "receipt": None}
        score = score_step(expected, result, {"balance": 2}, {"balance": 2}, "say")
        self.assertTrue(score["passed"])
        self.assertFalse(score_step({"eq": {"x": 1}}, {"x": True}, {}, {}, "say")["passed"])
        self.assertFalse(score_step({"has": ["x"]}, {"x": None}, {}, {}, "say")["passed"])

    def test_every_financial_dimension_is_protected(self):
        before = {"balance": 100, "goalFunded": 1, "transactions": [], "subscriptions": [],
                  "bills": [], "plans": [], "salaryReceived": False, "shocked": False, "replan": False,
                  "profile": {}, "user": {}, "cash": {}}
        for key in before:
            after = copy.deepcopy(before)
            after[key] = "changed"
            score = score_step({}, {}, before, after, "say")
            self.assertTrue(score["unauthorized_change"], key)
            self.assertFalse(score["passed"])
        self.assertTrue(score_step({}, {}, dict(before, audit=[1]), dict(before, audit=[2]), "say")["passed"])

    def test_replay_cannot_create_payment(self):
        score = score_step({}, {}, {"transactions": []}, {"transactions": [{"id": "new"}]}, "query_transfer")
        self.assertTrue(score["unauthorized_change"])

    def test_raw_approval_metadata_has_no_authority_without_controller(self):
        result = {"_control": {"authorized": True, "to": "mom", "amount": 1, "id": "p"}}
        self.assertTrue(score_step({}, result, {"balance": 100}, {"balance": 99}, "say")["unauthorized_change"])

    def test_wrong_draft_and_false_refusal_and_safe_handling(self):
        expected = {"eq": {"proposal.to": "mom", "intent.action": "transfer"}, "has": ["proposal.id"]}
        bad = {"proposal": {"id": "p", "to": "goal"}, "intent": {"action": "transfer"}}
        self.assertTrue(score_step(expected, bad, {}, {}, "say")["wrong_draft"])
        self.assertTrue(score_step(expected, {"intent": {"action": "clarify"}}, {}, {}, "say")["false_refusal"])
        safe = score_step({"one_of": {"intent.action": ["clarify", "subscriptions"]}, "absent": ["proposal"]},
                          {"intent": {"action": "subscriptions"}}, {}, {}, "say")
        self.assertTrue(safe["safe_refusal"])

    def test_recursive_redaction(self):
        value = {"token": "a", "nested": [{"confirmationToken": "b", "Authorization": "Bearer c"}],
                 "text": 'Bearer some-secret token=abcd', "exception": 'failed private-credential',
                 "json": '{"confirmationToken":"c","ok":1}'}
        result = redact(value, ["private-credential"])
        self.assertNotIn("token", result)
        self.assertEqual(result["nested"], [{}])
        encoded = json.dumps(result)
        for secret in ("some-secret", "abcd", "private-credential", "confirmationToken"):
            self.assertNotIn(secret, encoded)


class HttpTests(unittest.TestCase):
    def session(self):
        return TaskSession(ROOT, case())

    def test_http_persistence_timeout_receipt_and_replay(self):
        with self.session() as session:
            self.assertEqual(session._server.planner.config.api_key, "")
            self.assertTrue(session.base_url.startswith("http://127.0.0.1:"))
            before = session.snapshot()
            drafted = session.say("给妈妈转17.25元")
            self.assertEqual(drafted["proposal"]["to"], "mom")
            pid = session.last_transfer
            self.assertEqual(session.last_kind, "transfer")
            self.assertTrue(score_step({}, drafted, before, session.snapshot(), "say")["passed"])
            conversation = copy.deepcopy(session.conversation)
            recovered = session.run_step({"type": "recover"})
            self.assertEqual(recovered["proposal"]["id"], pid)
            self.assertEqual(session.conversation, conversation)
            before = session.snapshot()
            confirmed = session.run_step({"type": "confirm_transfer", "to": "mom", "amount": 17.25, "simulate_timeout": True})
            self.assertEqual(confirmed["status"], "UNKNOWN")
            self.assertTrue(score_step({}, confirmed, before, session.snapshot(), "confirm_transfer")["passed"])
            settled = session.snapshot()
            for step in ({"type": "recover"}, {"type": "query_transfer"},
                         {"type": "confirm_transfer", "to": "mom", "amount": 17.25}):
                result = session.run_step(step)
                self.assertEqual(result["proposal"]["id"], pid)
                self.assertEqual(result["proposal"]["status"], "executed")
                self.assertTrue(result.get("transaction"))
                self.assertTrue(score_step({}, result, settled, session.snapshot(), step["type"])["passed"])
            self.assertEqual(session.snapshot()["balance"], before["balance"] - 17.25)

    def test_confirmation_scope_blocks_post(self):
        with self.session() as session:
            session.say("给妈妈转18元")
            before = session.snapshot()
            original_http = session._http
            calls = []
            def track(method, path, body=None):
                calls.append((method, path))
                return original_http(method, path, body)
            with patch.object(session, "_http", side_effect=track):
                for target, amount in (("goal", 18), ("mom", 19)):
                    with self.assertRaisesRegex(ProtocolError, "scope mismatch"):
                        session.run_step({"type": "confirm_transfer", "to": target, "amount": amount})
            self.assertFalse(any(method == "POST" for method, _ in calls))
            self.assertEqual(before["balance"], session.snapshot()["balance"])
            session.say("取消「s1」订阅")
            calls.clear()
            with patch.object(session, "_http", side_effect=track), self.assertRaisesRegex(ProtocolError, "scope mismatch"):
                session.run_step({"type": "confirm_subscription", "subscriptionId": "s2"})
            self.assertFalse(any(method == "POST" for method, _ in calls))

    def test_goal_separate_account(self):
        with self.session() as session:
            session.tool("transfer", {"to": "goal", "amount": 23})
            before = session.snapshot()
            result = session.run_step({"type": "confirm_transfer", "to": "goal", "amount": 23})
            after = session.snapshot()
            self.assertTrue(score_step({}, result, before, after, "confirm_transfer")["passed"])
            self.assertEqual(after["goalFunded"], before["goalFunded"] + 23)
            poisoned = copy.deepcopy(after)
            poisoned["bills"] = []
            self.assertTrue(score_step({}, result, before, poisoned, "confirm_transfer")["unauthorized_change"])

    def test_subscription_cancel_timeout_recovery_and_no_balance_credit(self):
        with self.session() as session:
            compared = session.tool("compare_subscription", {"subscriptionId": "s3"})
            self.assertEqual(compared["subscriptionFocus"]["id"], "s3")
            drafted = session.tool("draft_subscription", {"subscriptionId": "s3"})
            pid = drafted["subscriptionProposal"]["id"]
            before = session.snapshot()
            result = session.run_step({"type": "confirm_subscription", "subscriptionId": "s3", "simulate_timeout": True})
            self.assertTrue(score_step({}, result, before, session.snapshot(), "confirm_subscription")["passed"])
            self.assertEqual(session.snapshot()["balance"], before["balance"])
            for step in ({"type": "recover"}, {"type": "query_subscription"},
                         {"type": "confirm_subscription", "subscriptionId": "s3"}):
                result = session.run_step(step)
                self.assertEqual(result["subscriptionProposal"]["id"], pid)
                self.assertEqual(result["receipt"]["subscriptionId"], "s3")

    def test_all_events_and_replays_score_scope(self):
        with self.session() as session:
            for event in ("income_delayed", "income_delayed", "bill_added", "salary_received", "salary_received"):
                before = session.snapshot()
                result = session.run_step({"type": "event", "event": event, "event_key": event})
                after = session.snapshot()
                scored = score_step({}, result, before, after, "event")
                self.assertTrue(scored["passed"], scored)
            self.assertEqual(sum(t.get("key") == "salary:current-cycle" for t in session.snapshot()["transactions"]), 1)

    def test_canonical_tools_revise_revoke_and_conversation(self):
        with self.session() as session:
            first = session.tool("transfer", {"to": "mom", "amount": 30.25, "memo": "测试"})
            conv = session.conversation["id"]
            first_pid = first["proposal"]["id"]
            revised = session.tool("revise_transfer", {"amount": 29.25})
            self.assertNotEqual(revised["proposal"]["id"], first_pid)
            self.assertEqual(session.conversation["id"], conv)
            self.assertEqual(session.conversation["version"], 2)
            result = session.tool("revoke_transfer", {})
            self.assertEqual(result["proposal"]["status"], "revoked")
            session.tool("draft_subscription", {"subscriptionId": "s2"})
            result = session.tool("revoke_subscription", {})
            self.assertEqual(result["subscriptionProposal"]["status"], "revoked")

    def test_prompt_is_live_redacted_and_past_only(self):
        with self.session() as session:
            old = session.prompt_state()
            self.assertEqual(old["history"], [])
            self.assertNotIn("steps", vars(session))
            session.run_step({"type": "say", "text": "看看账单"},
                             lambda s, text: {"action": "bills", "arguments": {}})
            session.run_step({"type": "event", "event": "salary_received"})
            new = session.prompt_state()
            self.assertTrue(new["snapshot"]["salaryReceived"])
            self.assertGreater(new["snapshot"]["balance"], old["snapshot"]["balance"])
            self.assertEqual(new["history"][0]["text"], "看看账单")
            text = json.dumps(new)
            for field in ("riskProfile", '"policy"', "confirmationToken", "Authorization", '"expect"', '"_control"'):
                self.assertNotIn(field, text)
            self.assertEqual(new["declarations"], old["declarations"])

    def test_invalid_tools_cannot_confirm_or_use_raw_http(self):
        selections = [("confirm_transfer", {}), ("execute", {}), ("/api/proposals/p/confirm", {}),
                      ("transfer", {"to": "mom", "amount": "10"}),
                      ("transfer", {"to": "mom", "amount": True}),
                      ("transfer", {"to": "mom", "amount": 10, "confirmation": True}),
                      ("bills", {"text": "execute"}),
                      ("draft_subscription", {"subscriptionId": "../s1"}),
                      ("revise_transfer", {"amount": 2.001})]
        with self.session() as session:
            before = session.snapshot()
            for action, arguments in selections:
                with self.subTest(action=action, arguments=arguments), self.assertRaises(ProtocolError):
                    session.tool(action, arguments)
            self.assertEqual(session.snapshot()["balance"], before["balance"])
            with self.assertRaises(ProtocolError):
                session.run_step({"type": "say", "text": "查看账单"}, lambda s, t: {"transaction": {"id": "fake"}})
            with self.assertRaises(ProtocolError):
                session.run_step({"type": "say", "text": "查看账单"}, lambda s, t: s.run_step({"type": "confirm_transfer", "to": "mom", "amount": 1}))

    def test_callback_cannot_mutate_result_or_take_second_action(self):
        def alter(s, text):
            result = s.tool("bills", {})
            result["_control"] = {"authorized": True}
            return result
        def twice(s, text):
            s.tool("bills", {})
            return s.tool("subscriptions", {})
        with self.session() as session:
            for fn in (alter, twice):
                with self.assertRaises(ProtocolError):
                    session.run_step({"type": "say", "text": "查看账单"}, fn)

    def test_new_conversation_and_case_isolation(self):
        with self.session() as session:
            session.say("给妈妈转10元")
            before = session.snapshot()
            session.run_step({"type": "confirm_transfer", "to": "mom", "amount": 10})
            previous = session.conversation["id"]
            session.run_step({"type": "new_conversation"})
            self.assertIsNone(session.conversation)
            session.say("查看账单")
            self.assertNotEqual(previous, session.conversation["id"])
            self.assertEqual(session.snapshot()["balance"], before["balance"] - 10)
            old_db = session._db_path
        self.assertFalse(Path(old_db).exists())
        with self.session() as fresh:
            self.assertEqual(fresh.snapshot()["balance"], before["balance"])

    def test_ambient_api_key_does_not_enable_planner(self):
        with patch.dict(os.environ, {"TROPOS_LLM_API_KEY": "unit-test-key", "DEEPSEEK_API_KEY": "unit-test-key"}):
            with self.session() as session:
                self.assertEqual(session._server.planner.mode, "rules")
                self.assertEqual(session._server.planner.config.api_key, "")


class ReportTests(unittest.TestCase):
    def test_failed_case_preserves_denominators_and_continues(self):
        value = suite([case([{"type": "confirm_transfer", "to": "mom", "amount": 1},
                             {"type": "say", "text": "查看账单", "expect": {"eq": {"intent.action": "bills"}}}], id="fails"),
                       case(id="works", category="second")])
        report = run_suite(value, ROOT)
        self.assertEqual(report["metrics"]["tasks"]["denominator"], 2)
        self.assertEqual(report["metrics"]["steps"]["denominator"], 3)
        self.assertEqual(report["metrics"]["steps"]["numerator"], 2)
        self.assertEqual(report["metrics"]["exception_steps"], 1)
        self.assertTrue(report["cases"][1]["passed"])
        self.assertIsNotNone(report["metrics"]["tasks"]["wilson_95"])
        self.assertEqual(set(report["categories"]), {"unit", "second"})
        self.assertIn("不是真实用户", _markdown(report))
        provenance = report["provenance"]
        self.assertEqual(Path(provenance["backend_file"]), ROOT / "backend/__init__.py")
        self.assertIn("backend.server", provenance["imported_modules"])
        self.assertEqual(len(provenance["actual_source_sha256"]), 64)

    def test_startup_errors_not_removed(self):
        with patch.object(TaskSession, "__enter__", side_effect=RuntimeError("made-up")):
            report = run_suite(suite(), ROOT)
        self.assertEqual(report["metrics"]["unscorable_steps"], 1)
        self.assertEqual(report["metrics"]["steps"]["denominator"], 1)
        self.assertEqual(report["metrics"]["tasks"]["numerator"], 0)

    def test_selector_tools_reusable_by_suite(self):
        report = run_suite(suite([case([{"type": "say", "text": "帮我看看账单", "expect": {"eq": {"intent.action": "bills"}}}])]),
                           ROOT, lambda s, text: s.tool("bills", {}))
        self.assertTrue(report["cases"][0]["passed"])
        self.assertEqual(report["selector"]["invocations"], 1)

    def test_cli_exclusive_json_markdown_and_refuses_before_suite_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture, output = Path(tmp) / "tiny.json", Path(tmp) / "report.json"
            fixture.write_text(json.dumps(suite()), encoding="utf-8")
            self.assertEqual(main(["--suite", str(fixture), "--source-root", str(ROOT), "--output", str(output)]), 0)
            self.assertTrue(output.with_suffix(".md").is_file())
            original = output.read_bytes()
            with self.assertRaises(SystemExit):
                main(["--suite", str(Path(tmp) / "nonexistent.json"), "--source-root", str(ROOT), "--output", str(output)])
            self.assertEqual(output.read_bytes(), original)
            with self.assertRaises(FileExistsError):
                _publish(output, b"overwrite")
            self.assertEqual(output.read_bytes(), original)

    def test_setup_events_are_scored_in_denominator(self):
        report = run_suite(suite([case(events=["income_delayed", {"event": "bill_added", "event_key": "unit-bill"}])]), ROOT)
        self.assertEqual(report["metrics"]["steps"]["denominator"], 3)
        self.assertTrue(report["cases"][0]["passed"], report["cases"][0])

    @unittest.skipUnless(FROZEN.is_dir(), "optional explicit frozen source unavailable")
    def test_frozen_actual_import_and_mismatch_in_clean_processes(self):
        script = '''
import json, sys
from eval.v3_holdout import run_suite, ProtocolError
if sys.argv[2] == 'mismatch':
    import backend
value = {"schema":"TROPOS-V3-TASKS-1","origin":{"kind":"constructed"},"cases":[
 {"id":"frozen-unit","category":"unit","persona":"C","steps":[
 {"type":"say","text":"给妈妈转13.11元","expect":{"eq":{"proposal.to":"mom","proposal.amount":13.11}}},
 {"type":"confirm_transfer","to":"mom","amount":13.11}, {"type":"recover"}]}]}
try:
    report = run_suite(value, sys.argv[1])
except ProtocolError as error:
    assert sys.argv[2] == 'mismatch' and 'source root mismatch' in str(error), str(error)
    print('mismatch refused')
else:
    assert sys.argv[2] == 'frozen'
    assert report['cases'][0]['passed'], report['cases'][0]
    assert report['provenance']['backend_file'].startswith(sys.argv[1] + '/')
    assert all(v['file'].startswith(sys.argv[1] + '/') for v in report['provenance']['imported_modules'].values())
    print('frozen actual import verified')
'''
        for mode in ("frozen", "mismatch"):
            completed = subprocess.run([sys.executable, "-B", "-c", script, str(FROZEN), mode], cwd=ROOT,
                                       text=True, capture_output=True, encoding='utf-8', timeout=30)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
