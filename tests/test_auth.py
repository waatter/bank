"""Real member identity, session lifecycle, and tenant-isolation tests."""
import http.client
import contextlib
import base64
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from backend.model import IntentPlanner, ModelConfig
from backend.server import create_server
from backend.service import BankService


class FakeMailer:
    def __init__(self):
        self.verifications = []
        self.resets = []

    def verification(self, recipient, token):
        self.verifications.append((recipient, token))
        return "test"

    def password_reset(self, recipient, token):
        self.resets.append((recipient, token))
        return "test"


class MemberAuthTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.directory.name) / "auth.sqlite3"
        self.mailer = FakeMailer()
        self.service = BankService(self.db_path, mailer=self.mailer)
        self.server = create_server(port=0, service=self.service,
                                    planner=IntentPlanner(ModelConfig()))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.directory.cleanup()

    def request(self, method, path, body=None, token=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=15)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        content = json.dumps(body).encode() if body is not None else None
        connection.request(method, path, body=content, headers=headers)
        response = connection.getresponse()
        payload = json.loads(response.read())
        status = response.status
        connection.close()
        return status, payload

    def register(self, username, name="测试用户", persona="C"):
        status, result = self.request("POST", "/api/auth/register", {
            "username": username, "password": "safe-password-2026",
            "email": username.casefold() + "@example.test",
            "displayName": name, "templatePersona": persona,
        })
        self.assertEqual(status, 201, result)
        return result

    def test_registration_creates_unique_member_and_slow_hash(self):
        result = self.register("member_one", "一号用户", "A")
        user = result["snapshot"]["user"]
        self.assertTrue(user["id"].startswith("usr_"))
        self.assertEqual(user["accountType"], "member")
        self.assertEqual(user["username"], "member_one")
        self.assertEqual(user["name"], "一号用户")
        with contextlib.closing(sqlite3.connect(self.db_path)) as database:
            stored = database.execute(
                "SELECT password_hash,password_salt FROM auth_accounts WHERE user_id=?",
                (user["id"],)).fetchone()
            self.assertNotIn("safe-password", stored[0])
            self.assertEqual(len(stored[0]), 64)
            self.assertEqual(len(stored[1]), 32)

    def test_login_logout_and_case_insensitive_uniqueness(self):
        registered = self.register("Case_User")
        status, duplicate = self.request("POST", "/api/auth/register", {
            "username": "case_user", "password": "another-password-2026",
            "email": "another@example.test",
            "displayName": "重复", "templatePersona": "B",
        })
        self.assertEqual(status, 409)
        self.assertEqual(duplicate["error"]["code"], "USERNAME_TAKEN")
        status, logged_in = self.request("POST", "/api/auth/login", {
            "username": "CASE_USER", "password": "safe-password-2026",
        })
        self.assertEqual(status, 200, logged_in)
        self.assertEqual(logged_in["snapshot"]["user"]["id"],
                         registered["snapshot"]["user"]["id"])
        token = logged_in["token"]
        self.assertEqual(self.request("GET", "/api/state", token=token)[0], 200)
        self.assertEqual(self.request("POST", "/api/auth/logout", {}, token)[0], 200)
        self.assertEqual(self.request("GET", "/api/state", token=token)[0], 401)

    def test_two_members_using_same_template_are_isolated(self):
        first = self.register("isolated_one", "用户甲", "C")
        second = self.register("isolated_two", "用户乙", "C")
        one, two = first["snapshot"], second["snapshot"]
        self.assertNotEqual(one["user"]["id"], two["user"]["id"])
        profile = dict(one["profile"], readConsent=True, reserve=4321)
        status, changed = self.request("PUT", "/api/profile", profile, first["token"])
        self.assertEqual(status, 200, changed)
        status, untouched = self.request("GET", "/api/state", token=second["token"])
        self.assertEqual(status, 200)
        self.assertNotEqual(changed["profile"]["reserve"], untouched["profile"]["reserve"])
        self.assertEqual(untouched["user"]["name"], "用户乙")

    def test_member_reset_keeps_identity_and_credentials(self):
        result = self.register("reset_member", "重置用户", "B")
        user_id, token = result["snapshot"]["user"]["id"], result["token"]
        status, snapshot = self.request("POST", "/api/reset", {"confirmation": True}, token)
        self.assertEqual(status, 200, snapshot)
        self.assertEqual(snapshot["user"]["id"], user_id)
        self.assertEqual(snapshot["user"]["accountType"], "member")
        status, login = self.request("POST", "/api/auth/login", {
            "username": "reset_member", "password": "safe-password-2026",
        })
        self.assertEqual(status, 200, login)
        self.assertEqual(login["snapshot"]["user"]["id"], user_id)

    def test_repeated_bad_passwords_are_throttled(self):
        self.register("limited_user")
        for _ in range(5):
            status, error = self.request("POST", "/api/auth/login", {
                "username": "limited_user", "password": "incorrect-password",
            })
            self.assertEqual(status, 401, error)
        status, result = self.request("POST", "/api/auth/login", {
            "username": "limited_user", "password": "safe-password-2026",
        })
        self.assertEqual(status, 401, result)
        self.assertEqual(result["error"]["code"], "INVALID_CREDENTIALS")
        with contextlib.closing(sqlite3.connect(self.db_path)) as database:
            locked_until = database.execute(
                "SELECT locked_until FROM auth_accounts WHERE username='limited_user'").fetchone()[0]
        self.assertGreater(locked_until, 0)

    def test_email_verification_and_password_reset_revoke_sessions(self):
        result = self.register("email_member")
        self.assertFalse(result["snapshot"]["user"]["emailVerified"])
        self.assertEqual(result["snapshot"]["user"]["email"], "em***@example.test")
        verification = self.mailer.verifications[-1][1]
        status, verified = self.request("POST", "/api/auth/email/verify", {"token": verification})
        self.assertEqual(status, 200, verified)
        status, snapshot = self.request("GET", "/api/state", token=result["token"])
        self.assertEqual(status, 200)
        self.assertTrue(snapshot["user"]["emailVerified"])
        status, generic = self.request("POST", "/api/auth/password/forgot", {"identifier": "email_member"})
        self.assertEqual(status, 200)
        self.assertNotIn("email_member", json.dumps(generic))
        reset_token = self.mailer.resets[-1][1]
        status, reset = self.request("POST", "/api/auth/password/reset", {
            "token": reset_token, "password": "new-safe-password-2026",
        })
        self.assertEqual(status, 200, reset)
        self.assertEqual(self.request("GET", "/api/state", token=result["token"])[0], 401)
        self.assertEqual(self.request("POST", "/api/auth/password/reset", {
            "token": reset_token, "password": "another-safe-password",
        })[0], 400)
        status, login = self.request("POST", "/api/auth/login", {
            "username": "email_member", "password": "new-safe-password-2026",
        })
        self.assertEqual(status, 200, login)
        self.assertIn("token", login)

    def test_existing_member_can_bind_and_verify_an_email(self):
        result = self.register("legacy_mail_member")
        user_id = result["snapshot"]["user"]["id"]
        with contextlib.closing(sqlite3.connect(self.db_path)) as database:
            database.execute(
                "UPDATE auth_accounts SET email=NULL,email_verified_at=NULL WHERE user_id=?",
                (user_id,))
            database.commit()
        status, bound = self.request("POST", "/api/auth/email/bind", {
            "email": "replacement@example.test",
            "password": "safe-password-2026",
            "code": "",
            "confirmation": True,
        }, result["token"])
        self.assertEqual(status, 200, bound)
        self.assertEqual(bound["snapshot"]["user"]["email"], "re***@example.test")
        self.assertFalse(bound["snapshot"]["user"]["emailVerified"])
        status, verified = self.request("POST", "/api/auth/email/verify", {
            "token": self.mailer.verifications[-1][1],
        })
        self.assertEqual(status, 200, verified)
        status, snapshot = self.request("GET", "/api/state", token=result["token"])
        self.assertEqual(status, 200)
        self.assertTrue(snapshot["user"]["emailVerified"])

    def test_totp_mfa_and_one_time_recovery_login(self):
        result = self.register("mfa_member")
        self.request("POST", "/api/auth/email/verify", {"token": self.mailer.verifications[-1][1]})
        status, setup = self.request("POST", "/api/auth/mfa/setup", {}, result["token"])
        self.assertEqual(status, 200, setup)
        secret = base64.b32decode(setup["secret"] + "=" * (-len(setup["secret"]) % 8))
        code = self.service._totp(secret, int(__import__('time').time() // 30))
        status, enabled = self.request("POST", "/api/auth/mfa/enable", {"code": code}, result["token"])
        self.assertEqual(status, 200, enabled)
        self.assertTrue(enabled["snapshot"]["user"]["mfaEnabled"])
        recovery = enabled["recoveryCodes"][0]
        self.request("POST", "/api/auth/logout", {}, result["token"])
        status, first = self.request("POST", "/api/auth/login", {
            "username": "mfa_member", "password": "safe-password-2026",
        })
        self.assertEqual(status, 200, first)
        self.assertTrue(first["mfaRequired"])
        status, completed = self.request("POST", "/api/auth/mfa/complete", {
            "challengeToken": first["challengeToken"], "code": recovery,
        })
        self.assertEqual(status, 200, completed)
        self.assertIn("token", completed)
        status, second = self.request("POST", "/api/auth/login", {
            "username": "mfa_member", "password": "safe-password-2026",
        })
        self.assertEqual(status, 200)
        self.assertEqual(self.request("POST", "/api/auth/mfa/complete", {
            "challengeToken": second["challengeToken"], "code": recovery,
        })[0], 401)


class LegacyIdentityMigrationTests(unittest.TestCase):
    def test_persona_unique_schema_is_upgraded_without_losing_demo_user(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.sqlite3"
            with contextlib.closing(sqlite3.connect(path)) as database:
                database.execute("CREATE TABLE users(id TEXT PRIMARY KEY, persona TEXT UNIQUE, name TEXT, revision INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL)")
                database.execute("INSERT INTO users VALUES('demo_C','C','知远',0,'{}')")
                database.commit()
            BankService(path)
            with contextlib.closing(sqlite3.connect(path)) as database:
                columns = {row[1] for row in database.execute("PRAGMA table_info(users)")}
                row = database.execute("SELECT id,account_type FROM users WHERE id='demo_C'").fetchone()
                unique_indexes = [index for index in database.execute("PRAGMA index_list(users)") if index[2]]
            self.assertIn("account_type", columns)
            self.assertEqual(row, ("demo_C", "demo"))
            self.assertFalse(any(index[1].startswith("sqlite_autoindex_users_2") for index in unique_indexes))


if __name__ == "__main__":
    unittest.main()
