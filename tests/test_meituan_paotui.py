import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend.service import BankError, BankService
from backend.meituan_paotui import MeituanPaotuiRunner
from backend.shopping import OfficialMeituanConnector, ShoppingService


def profile(style="A", reserve=1000):
    return {"goalType": "protect", "goalName": "保持现金安全", "target": 6000,
            "reserve": reserve, "incomeStability": "uncertain" if style == "A" else "stable",
            "style": style, "automation": "confirm", "readConsent": True}


class FakeRunner:
    available = True

    def __init__(self, fee=12, cached=False):
        self.fee = fee
        self.cached = cached
        self.submit_calls = 0
        self.connected = set()
        self.calls = []

    def auth_start(self, user_id):
        self.calls.append(("auth_start", user_id))
        if self.cached:
            self.connected.add(user_id)
            return {"alreadyConnected": True}
        return {"authUrl": "https://dpurl.cn/mock-official", "expiresInSeconds": 600}

    def auth_complete(self, user_id):
        self.calls.append(("auth_complete", user_id))
        self.connected.add(user_id)
        return True

    def logout(self, user_id):
        self.calls.append(("logout", user_id))
        self.connected.discard(user_id)

    def addresses(self, user_id):
        self.calls.append(("addresses", user_id))
        return [{"addressId": "addr-1", "address": "测试路 88 号", "houseNumber": "2 栋 301",
                 "name": "王同学", "phone": "138****8000", "lat": 31123456, "lng": 121123456,
                 "cityId": 1, "isDefault": True}]

    def address_info(self, user_id, address_id):
        self.calls.append(("address_info", user_id))
        self.last_address_id = address_id
        return {"phone": "13800008000"}

    def search_poi(self, user_id, keyword, city):
        self.calls.append(("search_poi", user_id))
        self.last_poi_query = (keyword, city)
        return [{"name": "测试小区", "lat": 31.123456, "lng": 121.123456, "cityId": 1}]

    def preview_buy(self, user_id, recipient, purchase_detail):
        self.calls.append(("preview_buy", user_id))
        self.preview_phone = recipient["phone"]
        return {"orderToken": "quote-token", "_deliveryFee": int(self.fee * 100),
                "_previewTotal": int(self.fee * 100), "_couponViewId": "coupon",
                "feeDisplay": f"¥{self.fee:.2f}", "estimateTime": "约 45 分钟"}

    def submit_buy(self, user_id, recipient, purchase_detail, quote, remark=""):
        self.calls.append(("submit_buy", user_id))
        self.submit_calls += 1
        self.submit_phone = recipient["phone"]
        self.submit_quote = quote
        return {"orderId": "mt-order-1", "tradeNo": "trade-1", "payToken": "pay-1"}

    def pay_url(self, user_id, order):
        self.calls.append(("pay_url", user_id))
        return {"mobilePayUrl": "https://i.meituan.com/pay/mock", "pcPayUrl": "https://www.meituan.com/pay/mock",
                "loginUrl": "https://passport.meituan.com/useraccount/ilogin"}

    def order_status(self, user_id, order_id):
        self.calls.append(("order_status", user_id))
        return {"orderStatus": {"text": "骑手配送中"}, "auditStatus": {"text": "审核通过"}}


class MeituanPaotuiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "bank.sqlite")
        self.bank = BankService(self.path)
        session = self.bank.session("A")
        self.user = self.bank.authenticate(session["token"])
        self.bank.save_profile(self.user, profile())
        self.runner = FakeRunner()
        self.shop = ShoppingService(self.bank, official=OfficialMeituanConnector(self.runner))

    def tearDown(self):
        for task in list(self.shop._auth_tasks.values()):
            task["wake"].set()
            task["thread"].join(2)
        self.tmp.cleanup()

    def wait_connected(self, shop=None, user=None):
        shop = shop or self.shop
        user = user or self.user
        deadline = time.time() + 2
        while time.time() < deadline:
            status = shop.paotui_status(user)
            if status["connected"]:
                return status
            time.sleep(0.01)
        self.fail("background Meituan authorization did not complete")

    def connect(self):
        started = self.shop.paotui_auth_start(self.user)
        self.assertEqual(started["authUrl"], "https://dpurl.cn/mock-official")
        self.shop.paotui_auth_complete(self.user)
        return self.wait_connected()

    def make_preview(self):
        self.connect()
        return self.shop.paotui_preview(self.user, {"purchaseDetail": "买一束粉色鲜花，不要百合",
            "addressId": "addr-1", "maxItemBudget": "100.00", "remark": "到店后联系"})["preview"]

    def test_auth_address_masking_and_user_binding(self):
        self.connect()
        result = self.shop.paotui_addresses(self.user)
        self.assertEqual(result["addresses"][0]["phone"], "138****8000")
        self.assertNotIn("13800008000", json.dumps(result, ensure_ascii=False))
        other_session = self.bank.session("B")
        other = self.bank.authenticate(other_session["token"])
        self.bank.save_profile(other, profile("B"))
        self.shop.paotui_auth_start(other)
        self.shop.paotui_auth_complete(other)
        self.wait_connected(user=other)
        self.assertTrue(self.shop.paotui_status(self.user)["connected"])
        self.assertTrue(self.shop.paotui_status(other)["connected"])
        self.shop.paotui_logout(other, {"confirmation": True})
        self.assertTrue(self.shop.paotui_status(self.user)["connected"])
        self.assertFalse(self.shop.paotui_status(other)["connected"])
        self.assertIn(("addresses", self.user), self.runner.calls)

    def test_status_reports_component_readiness_without_server_paths(self):
        status = self.shop.paotui_status(self.user)
        self.assertEqual(status["components"], {
            "node": True, "skill": True, "passport": True, "storage": True})
        self.assertNotIn("path", json.dumps(status).lower())

    def test_authorization_is_completed_by_one_background_poll(self):
        started = self.shop.paotui_auth_start(self.user)
        self.assertTrue(started["status"]["authPending"])
        self.assertEqual(started["status"]["authState"], "waiting_token")
        self.assertEqual(started["status"]["authUrl"], "https://dpurl.cn/mock-official")
        self.assertGreater(started["status"]["authExpiresAt"], time.time())
        status = self.wait_connected()
        self.assertTrue(status["connected"])
        self.assertNotIn("authUrl", status)
        self.assertEqual([call for call in self.runner.calls if call[0] == "auth_complete"],
                         [("auth_complete", self.user)])

    def test_pending_authorization_link_survives_browser_status_refresh(self):
        started = self.shop.paotui_auth_start(self.user)
        refreshed = self.shop.paotui_status(self.user)
        self.assertEqual(refreshed["authUrl"], started["authUrl"])
        self.assertTrue(refreshed["authPending"])

    def test_mobile_ui_offers_open_refresh_and_retry_controls(self):
        source = (Path(__file__).parents[1] / "assets" / "shopping-ui.js").read_text(encoding="utf-8")
        self.assertIn("打开美团官方授权", source)
        self.assertIn("刷新授权状态", source)
        self.assertIn("重新授权", source)

    def test_cached_official_authorization_connects_without_new_link(self):
        runner = FakeRunner(cached=True)
        shop = ShoppingService(self.bank, official=OfficialMeituanConnector(runner))
        result = shop.paotui_auth_start(self.user)
        self.assertTrue(result["status"]["connected"])
        self.assertTrue(result["reusedAuthorization"])
        self.assertNotIn("authUrl", result)
        self.assertEqual(shop.paotui_addresses(self.user)["addresses"][0]["id"], "addr-1")

    def test_legacy_global_binding_requires_one_safe_reauthorization(self):
        with self.bank._db(True) as db:
            db.execute("INSERT INTO shopping_provider_bindings(provider,user_id,content) VALUES(?,?,?)", (
                "meituan_paotui", self.user, json.dumps({"status": "connected"})))
        status = self.shop.paotui_status(self.user)
        self.assertFalse(status["connected"])
        self.assertTrue(status["requiresReauthorization"])
        started = self.shop.paotui_auth_start(self.user)
        self.assertEqual(started["authUrl"], "https://dpurl.cn/mock-official")
        self.assertTrue(started["status"]["authPending"])

    def test_preview_does_not_persist_real_phone_or_address(self):
        preview = self.make_preview()
        self.assertEqual(preview["deliveryFee"], 12)
        self.assertEqual(self.runner.preview_phone, "13800008000")
        with self.bank._db() as db:
            stored = db.execute("SELECT content FROM shopping_official_previews WHERE id=?", (preview["id"],)).fetchone()[0]
        self.assertNotIn("13800008000", stored)
        self.assertNotIn("测试路 88 号", stored)
        self.assertNotIn("2 栋 301", stored)

    def test_manual_address_fallback_is_temporary_and_can_confirm(self):
        self.connect()
        manual = {"city": "上海市", "address": "浦东新区测试路 88 号测试小区",
                  "houseNumber": "2 栋 301", "name": "王同学", "phone": "13800008000"}
        preview = self.shop.paotui_preview(self.user, {
            "purchaseDetail": "买一束粉色鲜花", "manualAddress": manual,
            "maxItemBudget": "100.00", "remark": "到店后联系"})["preview"]
        self.assertEqual(preview["address"]["source"], "manual")
        self.assertEqual(preview["address"]["phone"], "138****8000")
        self.assertIn("manualAddressToken", preview)
        self.assertEqual(self.runner.last_poi_query,
                         ("浦东新区测试路 88 号测试小区 2 栋 301", "上海市"))
        with self.bank._db() as db:
            stored = db.execute("SELECT content FROM shopping_official_previews WHERE id=?",
                                (preview["id"],)).fetchone()[0]
        self.assertNotIn("13800008000", stored)
        self.assertNotIn("测试小区", stored)
        result = self.shop.paotui_confirm(self.user, preview["id"], {
            "confirmation": True, "confirmationToken": preview["confirmationToken"],
            "manualAddressToken": preview["manualAddressToken"],
            "requestKey": "manual-address-order"})
        self.assertEqual(result["order"]["status"], "awaiting_payment")
        self.assertEqual(self.runner.submit_phone, "13800008000")

    def test_manual_address_token_cannot_be_omitted_or_tampered(self):
        self.connect()
        preview = self.shop.paotui_preview(self.user, {
            "purchaseDetail": "买一杯咖啡",
            "manualAddress": {"city": "上海市", "address": "浦东新区测试路 88 号",
                              "houseNumber": "1 楼", "name": "王同学", "phone": "13800008000"},
            "maxItemBudget": "50.00"})["preview"]
        with self.assertRaises(BankError) as denied:
            self.shop.paotui_confirm(self.user, preview["id"], {
                "confirmation": True, "confirmationToken": preview["confirmationToken"],
                "manualAddressToken": preview["manualAddressToken"] + "x",
                "requestKey": "tampered-manual-address"})
        self.assertEqual(denied.exception.code, "INVALID_MANUAL_ADDRESS_TOKEN")
        self.assertEqual(self.runner.submit_calls, 0)

    def test_confirm_is_explicit_idempotent_and_does_not_debit_mock_bank(self):
        preview = self.make_preview()
        before = self.bank.snapshot(self.user)["balance"]
        payload = {"confirmation": True, "confirmationToken": preview["confirmationToken"],
                   "requestKey": "real-order-one", "highAmountConfirmation": False}
        first = self.shop.paotui_confirm(self.user, preview["id"], payload)
        second = self.shop.paotui_confirm(self.user, preview["id"], payload)
        self.assertEqual(self.runner.submit_calls, 1)
        self.assertEqual(first["order"]["id"], second["order"]["id"])
        self.assertEqual(first["order"]["status"], "awaiting_payment")
        self.assertIn("payment", first["order"])
        self.assertEqual(before, self.bank.snapshot(self.user)["balance"])
        self.assertEqual(self.runner.submit_phone, "13800008000")

    def test_stale_revision_blocks_real_submission(self):
        preview = self.make_preview()
        self.bank.event(self.user, {"type": "bill_added", "event_key": "paotui-stale"})
        with self.assertRaises(BankError) as stale:
            self.shop.paotui_confirm(self.user, preview["id"], {"confirmation": True,
                "confirmationToken": preview["confirmationToken"], "requestKey": "stale-paotui"})
        self.assertEqual(stale.exception.code, "STALE_APPROVAL")
        self.assertEqual(self.runner.submit_calls, 0)

    def test_delivery_fee_over_100_requires_extra_confirmation(self):
        runner = FakeRunner(fee=120)
        shop = ShoppingService(self.bank, official=OfficialMeituanConnector(runner))
        shop.paotui_auth_start(self.user)
        shop.paotui_auth_complete(self.user)
        self.wait_connected(shop=shop)
        preview = shop.paotui_preview(self.user, {"purchaseDetail": "买一束花", "addressId": "addr-1",
                                                  "maxItemBudget": "100"})["preview"]
        base = {"confirmation": True, "confirmationToken": preview["confirmationToken"], "requestKey": "high-fee"}
        with self.assertRaises(BankError) as blocked:
            shop.paotui_confirm(self.user, preview["id"], base)
        self.assertEqual(blocked.exception.code, "HIGH_AMOUNT_CONFIRMATION_REQUIRED")
        result = shop.paotui_confirm(self.user, preview["id"], dict(base, highAmountConfirmation=True))
        self.assertEqual(result["order"]["status"], "awaiting_payment")

    def test_status_query_uses_provider_text(self):
        preview = self.make_preview()
        order = self.shop.paotui_confirm(self.user, preview["id"], {"confirmation": True,
            "confirmationToken": preview["confirmationToken"], "requestKey": "status-one"})["order"]
        updated = self.shop.paotui_order_status(self.user, order["id"])["order"]
        self.assertEqual(updated["statusText"], "骑手配送中")
        self.assertEqual(updated["auditStatusText"], "审核通过")

    def test_legacy_single_account_binding_is_migrated_without_data_loss(self):
        legacy_path = str(Path(self.tmp.name) / "legacy.sqlite")
        db = sqlite3.connect(legacy_path)
        try:
            db.execute("CREATE TABLE shopping_provider_bindings("
                       "provider TEXT PRIMARY KEY,user_id TEXT NOT NULL,content TEXT NOT NULL)")
            db.execute("INSERT INTO shopping_provider_bindings VALUES(?,?,?)", (
                "meituan_paotui", "legacy-user", json.dumps({"status": "connected"})))
            db.commit()
        finally:
            db.close()

        migrated = BankService(legacy_path)
        with migrated._db() as db:
            columns = db.execute("PRAGMA table_info(shopping_provider_bindings)").fetchall()
            primary_key = [row["name"] for row in sorted(
                (row for row in columns if row["pk"]), key=lambda row: row["pk"])]
            row = db.execute("SELECT user_id,content FROM shopping_provider_bindings").fetchone()

        self.assertEqual(primary_key, ["provider", "user_id"])
        self.assertEqual(row["user_id"], "legacy-user")
        self.assertEqual(json.loads(row["content"])["status"], "connected")


class MeituanRunnerCompatibilityTests(unittest.TestCase):
    @staticmethod
    def fixture_runner():
        runner = MeituanPaotuiRunner(project_root=Path.cwd())
        runner.node_bin = Path(__file__)
        runner.passport_js = Path(__file__)
        runner.paotui_js = Path(__file__)
        runner._ensure = lambda _credential_id: (Path.cwd(), Path.cwd())
        return runner

    def test_zero_exit_business_error_is_not_treated_as_success(self):
        runner = self.fixture_runner()
        response = json.dumps({"status": "error", "message": "请求失败"}, ensure_ascii=False)
        with patch("backend.meituan_paotui.subprocess.run",
                   return_value=SimpleNamespace(returncode=0, stdout=response, stderr="")):
            with self.assertRaises(BankError) as raised:
                runner._run(runner.paotui_js, ("get_address_list",), credential_id="user-a")

        self.assertEqual(raised.exception.code, "MEITUAN_ADDRESS_SERVICE_ERROR")
        self.assertTrue(raised.exception.details["retryable"])
        self.assertNotIn("请求失败", raised.exception.message)

    def test_unauthorized_business_error_requires_reauthorization(self):
        runner = self.fixture_runner()
        response = json.dumps({"status": "error", "message": "request failed: 401"})
        with patch("backend.meituan_paotui.subprocess.run",
                   return_value=SimpleNamespace(returncode=0, stdout=response, stderr="")):
            with self.assertRaises(BankError) as raised:
                runner._run(runner.paotui_js, ("get_address_list",), credential_id="user-a")

        self.assertEqual(raised.exception.code, "MEITUAN_AUTH_REQUIRED")
        self.assertEqual(raised.exception.status, 401)

    def test_unrecognized_address_payload_is_not_reported_as_empty(self):
        runner = MeituanPaotuiRunner(project_root=Path.cwd())
        runner.token = lambda _credential_id: "token_" + "x" * 48
        runner._paotui = lambda *_args, **_kwargs: {"status": "ok", "message": "schema changed"}
        with self.assertRaises(BankError) as raised:
            runner.addresses("user-a")
        self.assertEqual(raised.exception.code, "MEITUAN_ADDRESS_INVALID_RESPONSE")

    def test_address_ui_distinguishes_provider_failure_from_empty_book(self):
        source = (Path(__file__).parents[1] / "assets" / "shopping-ui.js").read_text(encoding="utf-8")
        self.assertIn("美团地址接口暂时不可用", source)
        self.assertIn("美团已明确返回空地址簿", source)
        self.assertIn('data-shop="refresh-addresses"', source)
        self.assertIn('data-shop="manual-address"', source)
        self.assertIn("手动添加临时地址", source)
        self.assertIn("不写入 TROPOS 地址簿或数据库", source)
        self.assertIn('data-shop="reauthorize"', source)

    def test_authorization_poll_uses_official_ten_minute_window(self):
        runner = MeituanPaotuiRunner(project_root=Path.cwd())
        calls = []
        secret = "token_" + "x" * 48

        def passport(*args, **kwargs):
            calls.append((args, kwargs))
            return {"ok": True}

        runner._passport = passport
        runner.token = lambda _credential_id: secret
        runner._paotui = lambda *args, **kwargs: {"ok": True}
        runner.auth_complete("user-a")

        args, kwargs = calls[0]
        self.assertEqual(args, ("auth", "poll-token", "--client_id",
                                "ac76c22d257c4c6d9164719eea64ed4b",
                                "--timeout", "600", "--interval", "3"))
        self.assertEqual(kwargs["timeout"], 630)

    def test_server_state_directory_is_configurable_and_part_of_readiness(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = root / "skill"
            state = root / "private-state"
            skill.mkdir()
            state.mkdir()
            node = root / "node"
            passport = root / "passport.js"
            paotui = skill / "paotui.js"
            for path in (node, passport, paotui):
                path.write_text("// fixture", encoding="utf-8")
            with patch.dict(os.environ, {"TROPOS_MEITUAN_STATE_DIR": str(state)}):
                runner = MeituanPaotuiRunner(
                    project_root=root, skill_dir=skill, node_bin=node, passport_js=passport)

            self.assertEqual(runner.state_dir, state)
            self.assertEqual(runner.account_dir, state / "accounts")
            self.assertEqual(runner.temp_dir, state / "tmp")
            self.assertEqual(runner.readiness, {
                "node": True, "skill": True, "passport": True, "storage": True})
            self.assertTrue(runner.available)

    def test_readiness_identifies_missing_private_storage(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = root / "skill"
            skill.mkdir()
            node = root / "node"
            passport = root / "passport.js"
            paotui = skill / "paotui.js"
            for path in (node, passport, paotui):
                path.write_text("// fixture", encoding="utf-8")
            runner = MeituanPaotuiRunner(
                project_root=root, skill_dir=skill, node_bin=node, passport_js=passport)

            self.assertFalse(runner.readiness["storage"])
            self.assertFalse(runner.available)

    def test_cached_passport_token_is_logged_in_without_being_returned(self):
        runner = MeituanPaotuiRunner(project_root=Path.cwd())
        secret = "cached_" + "x" * 48
        calls = []

        def passport(*args, **_kwargs):
            if args[:2] == ("auth", "get-code"):
                return f"Token: {secret}"
            if args[:1] == ("get-token",):
                return f"Token: {secret}"
            self.fail(f"unexpected passport call: {args}")

        def paotui(*args, token=None, **_kwargs):
            calls.append((args, token))
            return {"ok": True}

        runner._passport = passport
        runner._paotui = paotui
        result = runner.auth_start("user-a")

        self.assertEqual(result, {"alreadyConnected": True})
        self.assertEqual(calls, [(("login",), secret)])
        self.assertNotIn(secret, json.dumps(result))

    def test_child_process_home_and_token_cache_are_isolated_per_user(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = root / "skill"
            state = root / ".local" / "meituan-runtime"
            skill.mkdir()
            state.mkdir(parents=True)
            node = root / "node.exe"
            passport = root / "passport.js"
            paotui = skill / "paotui.js"
            for path in (node, passport, paotui):
                path.write_text("// fixture", encoding="utf-8")
            runner = MeituanPaotuiRunner(
                project_root=root, skill_dir=skill, node_bin=node, passport_js=passport)

            with patch("backend.meituan_paotui.subprocess.run",
                       return_value=SimpleNamespace(returncode=0, stdout="{}", stderr="")) as called:
                runner._run(passport, ("status",), credential_id="user-one")
                first_env = called.call_args.kwargs["env"]
                runner._run(passport, ("status",), credential_id="user-two")
                second_env = called.call_args.kwargs["env"]

            self.assertNotEqual(first_env["USERPROFILE"], second_env["USERPROFILE"])
            self.assertNotEqual(first_env["TEMP"], second_env["TEMP"])
            self.assertNotIn("user-one", first_env["USERPROFILE"])
            self.assertNotIn("user-two", second_env["USERPROFILE"])
            self.assertEqual(first_env["HOME"], first_env["USERPROFILE"])
            self.assertEqual(second_env["HOME"], second_env["USERPROFILE"])
            self.assertNotIn("MCP_ACCESS_TOKEN", first_env)

    @unittest.skipUnless(os.name == "nt", "Windows-specific credential directory behavior")
    def test_windows_does_not_apply_unix_chmod_to_credential_directories(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = root / "skill"
            state = root / ".local" / "meituan-runtime"
            skill.mkdir()
            state.mkdir(parents=True)
            node = root / "node.exe"
            passport = root / "passport.js"
            paotui = skill / "paotui.js"
            for path in (node, passport, paotui):
                path.write_text("// fixture", encoding="utf-8")
            runner = MeituanPaotuiRunner(
                project_root=root, skill_dir=skill, node_bin=node, passport_js=passport)

            with patch.object(Path, "chmod") as chmod:
                profile, temp = runner._ensure("windows-user")

            chmod.assert_not_called()
            self.assertTrue((profile / "AppData" / "Roaming").is_dir())
            self.assertTrue((profile / "AppData" / "Local").is_dir())
            self.assertTrue(temp.is_dir())

            with patch.object(Path, "mkdir") as mkdir:
                runner._ensure("windows-mode-check")
            self.assertTrue(mkdir.called)
            self.assertTrue(all("mode" not in call.kwargs for call in mkdir.call_args_list))


if __name__ == "__main__":
    unittest.main()
