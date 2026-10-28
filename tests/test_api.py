"""Real loopback HTTP + SQLite integration tests; no real banking or paid model API."""
import concurrent.futures
import contextlib
import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from backend.model import IntentPlanner, ModelConfig
from backend.server import ROOT, create_server
from backend.service import BankService


class ApiTests(unittest.TestCase):
    def test_intent_report_is_authenticated_and_read_from_fixed_path(self):
        token, _ = self.login()
        original_root = self.server.root
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'reports').mkdir()
            self.server.root = root
            try:
                self.assertEqual(self.request('GET', '/api/evaluation/intents')[0], 401)
                self.assertEqual(self.request('GET', '/api/evaluation/intents', token=token)[0], 404)
                expected = {'summary': {'cases': 41}, 'live_model_run': False}
                (root / 'reports' / 'intent-rules.json').write_text(json.dumps(expected))
                status, result, _ = self.request('GET', '/api/evaluation/intents', token=token)
                self.assertEqual(status, 200)
                self.assertEqual(result, expected)
                self.assertEqual(self.request('GET', '/api/evaluation/intents?file=.env', token=token)[0], 400)
                (root / 'reports' / 'intent-rules.json').write_text('{invalid')
                self.assertEqual(self.request('GET', '/api/evaluation/intents', token=token)[0], 500)
            finally:
                self.server.root = original_root

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.directory.name) / "bank.sqlite3"
        self.service = BankService(self.db_path)
        self.server = create_server(port=0, service=self.service, planner=IntentPlanner(ModelConfig()))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.directory.cleanup()

    def request(self, method, path, body=None, token=None, headers=None, raw=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        request_headers = {"Content-Type": "application/json"}
        if token:
            request_headers["Authorization"] = "Bearer " + token
        request_headers.update(headers or {})
        content = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        connection.request(method, path, body=content, headers=request_headers)
        response = connection.getresponse()
        data, response_headers = response.read(), dict(response.getheaders())
        status = response.status
        connection.close()
        return status, (json.loads(data) if response_headers.get("Content-Type", "").startswith("application/json") else data), response_headers

    def login(self, persona="C", consent=True):
        status, result, _ = self.request("POST", "/api/sessions", {"persona": persona})
        self.assertEqual(status, 201)
        token, snapshot = result["token"], result["snapshot"]
        if consent:
            status, snapshot, _ = self.request("PUT", "/api/profile", dict(snapshot["profile"], readConsent=True, reserve=1000), token)
            self.assertEqual(status, 200)
        return token, snapshot

    def draft(self, token):
        status, result, _ = self.request("POST", "/api/transfers/propose", {"to": "mom", "amount": 100, "when": "now", "memo": "合成测试"}, token)
        self.assertEqual(status, 201)
        return result

    def confirmation(self, proposal, **kwargs):
        return dict(confirmation=True, confirmationToken=proposal["confirmationToken"], **kwargs)

    def test_health_discloses_synthetic_mode_and_no_cors_wildcard(self):
        status, result, headers = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(result["sandbox"])
        self.assertEqual(result["modelMode"], "rules")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_all_state_requires_bearer_and_bad_sessions_rejected(self):
        self.assertEqual(self.request("GET", "/api/state")[0], 401)
        self.assertEqual(self.request("GET", "/api/state", token="fake")[0], 401)
        status, result, _ = self.request("POST", "/api/sessions", {"persona": "D"})
        self.assertEqual(status, 400)
        self.assertIn("code", result["error"])

    def test_session_expiration(self):
        token, _ = self.login()
        with contextlib.closing(sqlite3.connect(self.db_path)) as database:
            database.execute("UPDATE sessions SET expires=0")
            database.commit()
        self.assertEqual(self.request("GET", "/api/state", token=token)[0], 401)

    def test_static_allowlist_and_inline_script_nonce(self):
        for path in ["/", "/demo.html", "/assets/demo.js", "/assets/profile-engine.js"]:
            self.assertEqual(self.request("GET", path)[0], 200)
        # Use a fixture: the current homepage loads an external same-origin script.
        # The nonce mechanism must still protect any registered page with inline JS.
        original_root = self.server.root
        with tempfile.TemporaryDirectory() as directory:
            self.server.root = Path(directory).resolve()
            (self.server.root / 'index.html').write_text('<!doctype html><script>window.example = true;</script>')
            try:
                status, document, headers = self.request("GET", "/")
                self.assertEqual(status, 200)
                self.assertIn(b"<script nonce=", document)
                nonce = document.split(b'<script nonce="', 1)[1].split(b'"', 1)[0].decode()
                self.assertIn("'nonce-" + nonce + "'", headers["Content-Security-Policy"])
            finally:
                self.server.root = original_root
        for path in ["/.env", "/.git/config", "/backend/service.py", "/README.md", "/tests/test_api.py", "/.local/tropos.sqlite3", "/assets/", "/assets/unregistered.js"]:
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path)[0], 404)
        for path in ["/assets/../.env", "/assets/%2e%2e/.env", "/%ff", "/assets%5c..%5c.env"]:
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path)[0], 400)

    def test_cross_origin_host_and_fetch_site_rejected_before_mutation(self):
        for headers in [{"Host": "attacker.example"}, {"Origin": "https://attacker.example"},
                        {"Origin": "null"}, {"Sec-Fetch-Site": "cross-site"}]:
            with self.subTest(headers=headers):
                self.assertEqual(self.request("POST", "/api/sessions", {"persona": "A"}, headers=headers)[0], 403)
        origin = f"http://127.0.0.1:{self.port}"
        self.assertEqual(self.request("POST", "/api/sessions", {"persona": "A"}, headers={"Origin": origin})[0], 201)

    def test_json_type_length_unknown_fields_and_nonfinite_rejected(self):
        for raw in [b"[]", b"null", b"not-json", b'{"persona":"A","persona":"C"}', b'{"persona":NaN}', b'{"persona":1e309}']:
            with self.subTest(raw=raw):
                self.assertEqual(self.request("POST", "/api/sessions", raw=raw)[0], 400)
        self.assertEqual(self.request("POST", "/api/sessions", {"persona": "A", "user_id": "demo_C"})[0], 400)
        self.assertEqual(self.request("POST", "/api/sessions", raw=b" " * 65537)[0], 413)
        self.assertEqual(self.request("POST", "/api/sessions", {"persona": "A"}, headers={"Content-Type": "text/plain"})[0], 415)

    def test_without_consent_cannot_propose(self):
        token, snapshot = self.login(consent=False)
        self.assertFalse(snapshot["profile"]["readConsent"])
        self.assertEqual(self.request("POST", "/api/transfers/propose", {"to": "mom", "amount": 100}, token)[0], 403)

    def test_proposals_require_bearer_and_recover_only_current_user(self):
        self.assertEqual(self.request("GET", "/api/proposals")[0], 401)
        self.assertEqual(self.request("GET", "/api/proposals", token="fake")[0], 401)
        token_c, snapshot = self.login("C")
        token_a, _ = self.login("A")
        proposal = self.draft(token_c)['proposal']
        self.server.service = BankService(self.db_path)
        status, recovered, headers = self.request("GET", "/api/proposals", token=token_c)
        self.assertEqual(status, 200)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(recovered['snapshot']['balance'], snapshot['balance'])
        self.assertEqual(len(recovered['proposals']), 1)
        self.assertEqual(recovered['proposals'][0]['id'], proposal['id'])
        self.assertTrue(recovered['proposals'][0]['canConfirm'])
        self.assertEqual(recovered['proposals'][0]['confirmationToken'], proposal['confirmationToken'])
        status, other, _ = self.request("GET", "/api/proposals", token=token_a)
        self.assertEqual(status, 200)
        self.assertEqual(other['proposals'], [])
        self.assertNotIn(proposal['id'], json.dumps(other))

    def test_proposal_recovery_marks_invalid_and_committed_states_without_reposting(self):
        token, snapshot = self.login()
        stale = self.draft(token)['proposal']
        revoked = self.draft(token)['proposal']
        executed = self.draft(token)['proposal']
        self.request('POST', f'/api/proposals/{revoked["id"]}/revoke', {'confirmation': True}, token)
        status, transaction, _ = self.request('POST', f'/api/proposals/{executed["id"]}/confirm', self.confirmation(executed), token)
        self.assertEqual(status, 200)
        status, result, _ = self.request('GET', '/api/proposals', token=token)
        self.assertEqual(status, 200)
        proposals = {p['id']: p for p in result['proposals']}
        self.assertEqual(proposals[stale['id']]['status'], 'stale')
        self.assertEqual(proposals[revoked['id']]['status'], 'revoked')
        self.assertEqual(proposals[executed['id']]['status'], 'executed')
        self.assertEqual(proposals[executed['id']]['transaction'], transaction['transaction'])
        for proposal in proposals.values():
            self.assertFalse(proposal['canConfirm'])
            self.assertNotIn('confirmationToken', proposal)
        self.assertEqual(result['snapshot']['balance'], snapshot['balance'] - 100)
        status, queried, _ = self.request('GET', '/api/transactions/by-key/' + executed['id'], token=token)
        self.assertEqual(status, 200)
        self.assertEqual(queried['transaction']['receipt'], transaction['transaction']['receipt'])
        status, replay, _ = self.request('POST', f'/api/proposals/{executed["id"]}/confirm', self.confirmation(executed), token)
        self.assertEqual(status, 200)
        self.assertEqual(replay['transaction'], transaction['transaction'])
        self.assertEqual(replay['snapshot']['balance'], result['snapshot']['balance'])

    def test_profile_update_requires_new_proposal(self):
        token, snapshot = self.login()
        proposal = self.draft(token)["proposal"]
        status, snapshot, _ = self.request("PUT", "/api/profile", dict(snapshot["profile"], reserve=2000), token)
        self.assertEqual(status, 200)
        status, error, _ = self.request("POST", f'/api/proposals/{proposal["id"]}/confirm', self.confirmation(proposal), token)
        self.assertEqual(status, 409)
        self.assertEqual(error["error"]["code"], "STALE_APPROVAL")

    def test_confirmation_token_and_explicit_confirmation_are_mandatory(self):
        token, _ = self.login()
        proposal = self.draft(token)["proposal"]
        path = f'/api/proposals/{proposal["id"]}/confirm'
        for body in [{"confirmation": True}, self.confirmation(proposal, simulate_timeout="true"),
                     {"confirmation": False, "confirmationToken": proposal["confirmationToken"]},
                     {"confirmation": True, "confirmationToken": "wrong"}]:
            with self.subTest(body=body):
                self.assertIn(self.request("POST", path, body, token)[0], {400, 403})
        self.assertEqual(self.request("POST", path, self.confirmation(proposal), token)[0], 200)

    def test_timeout_commit_query_and_replay_only_debit_once(self):
        token, snapshot = self.login()
        proposal = self.draft(token)["proposal"]
        path = f'/api/proposals/{proposal["id"]}/confirm'
        status, unknown, _ = self.request("POST", path, self.confirmation(proposal, simulate_timeout=True), token)
        self.assertEqual(status, 202)
        self.assertEqual(unknown["status"], "UNKNOWN")
        self.assertNotIn("transaction", unknown)
        self.assertNotIn("snapshot", unknown)
        status, queried, _ = self.request("GET", "/api/transactions/by-key/" + unknown["key"], token=token)
        self.assertEqual(status, 200)
        self.assertEqual(queried["snapshot"]["balance"], snapshot["balance"] - 100)
        status, replay, _ = self.request("POST", path, self.confirmation(proposal), token)
        self.assertEqual(status, 200)
        self.assertEqual(replay["transaction"]["id"], queried["transaction"]["id"])
        self.assertEqual(replay["snapshot"]["balance"], queried["snapshot"]["balance"])

    def test_parallel_confirmation_has_one_receipt_and_one_debit(self):
        token, snapshot = self.login()
        proposal = self.draft(token)["proposal"]
        path = f'/api/proposals/{proposal["id"]}/confirm'
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(lambda _: self.request("POST", path, self.confirmation(proposal), token), range(8)))
        self.assertEqual({status for status, _, _ in outcomes}, {200})
        self.assertEqual(len({result["transaction"]["id"] for _, result, _ in outcomes}), 1)
        self.assertEqual(self.request("GET", "/api/state", token=token)[1]["balance"], snapshot["balance"] - 100)

    def test_cross_user_proposals_and_receipts_not_visible(self):
        token_c, _ = self.login("C")
        token_a, snapshot_a = self.login("A")
        proposal = self.draft(token_c)["proposal"]
        path = f'/api/proposals/{proposal["id"]}/confirm'
        self.assertEqual(self.request("POST", path, self.confirmation(proposal), token_a)[0], 404)
        self.assertEqual(self.request("POST", path, self.confirmation(proposal), token_c)[0], 200)
        self.assertEqual(self.request("GET", "/api/transactions/by-key/" + proposal["id"], token=token_a)[0], 404)
        self.assertEqual(self.request("GET", "/api/state", token=token_a)[1]["balance"], snapshot_a["balance"])

    def test_revoke_invalidates_unexecuted_proposal(self):
        token, _ = self.login()
        proposal = self.draft(token)["proposal"]
        self.assertEqual(self.request("POST", f'/api/proposals/{proposal["id"]}/revoke', {"confirmation": True}, token)[0], 200)
        self.assertEqual(self.request("POST", f'/api/proposals/{proposal["id"]}/confirm', self.confirmation(proposal), token)[0], 409)

    def test_agent_discloses_rule_mode_and_does_not_execute(self):
        token, snapshot = self.login()
        status, result, _ = self.request("POST", "/api/agent", {"message": "给妈妈转100元"}, token)
        self.assertEqual(status, 200)
        self.assertEqual(result["mode"], "rules")
        self.assertIn("本地规则", result["reply"])
        self.assertIn("proposal", result)
        self.assertNotIn("transaction", result)
        self.assertEqual(result["snapshot"]["balance"], snapshot["balance"])
        self.assertEqual(self.request("POST", "/api/agent", {"message": "x" * 2001}, token)[0], 400)

    def test_events_and_subscription_cancellation_require_confirmation(self):
        token, snapshot = self.login()
        subscription = snapshot["subscriptions"][0]
        path = f'/api/subscriptions/{subscription["id"]}/cancel'
        self.assertEqual(self.request("POST", path, {"confirmation": False}, token)[0], 400)
        status, result, _ = self.request("POST", path, {"confirmation": True}, token)
        self.assertEqual(status, 200)
        self.assertIn("receipt", result)
        self.assertEqual(self.request("POST", "/api/events", {"type": "arbitrary_payment", "event_key": "test-event"}, token)[0], 400)
        self.assertEqual(self.request("POST", "/api/reset", {"confirmation": False}, token)[0], 400)
        self.assertEqual(self.request("POST", "/api/reset", {"confirmation": True}, token)[0], 200)

    def test_non_loopback_binding_is_rejected(self):
        with self.assertRaises(ValueError):
            create_server(host="0.0.0.0", port=0, service=self.service)


if __name__ == "__main__":
    unittest.main()
