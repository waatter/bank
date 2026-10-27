import json
import tempfile
import unittest
from pathlib import Path

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
        self.connected = False

    def auth_start(self):
        if self.cached:
            self.connected = True
            return {"alreadyConnected": True}
        return {"authUrl": "https://dpurl.cn/mock-official", "expiresInSeconds": 600}

    def auth_complete(self):
        self.connected = True
        return True

    def logout(self):
        self.connected = False

    def addresses(self):
        return [{"addressId": "addr-1", "address": "测试路 88 号", "houseNumber": "2 栋 301",
                 "name": "王同学", "phone": "138****8000", "lat": 31123456, "lng": 121123456,
                 "cityId": 1, "isDefault": True}]

    def address_info(self, address_id):
        self.last_address_id = address_id
        return {"phone": "13800008000"}

    def preview_buy(self, recipient, purchase_detail):
        self.preview_phone = recipient["phone"]
        return {"orderToken": "quote-token", "_deliveryFee": int(self.fee * 100),
                "_previewTotal": int(self.fee * 100), "_couponViewId": "coupon",
                "feeDisplay": f"¥{self.fee:.2f}", "estimateTime": "约 45 分钟"}

    def submit_buy(self, recipient, purchase_detail, quote, remark=""):
        self.submit_calls += 1
        self.submit_phone = recipient["phone"]
        self.submit_quote = quote
        return {"orderId": "mt-order-1", "tradeNo": "trade-1", "payToken": "pay-1"}

    def pay_url(self, order):
        return {"mobilePayUrl": "https://i.meituan.com/pay/mock", "pcPayUrl": "https://www.meituan.com/pay/mock",
                "loginUrl": "https://passport.meituan.com/useraccount/ilogin"}

    def order_status(self, order_id):
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
        self.tmp.cleanup()

    def connect(self):
        started = self.shop.paotui_auth_start(self.user)
        self.assertEqual(started["authUrl"], "https://dpurl.cn/mock-official")
        return self.shop.paotui_auth_complete(self.user)

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
        with self.assertRaises(BankError) as blocked:
            self.shop.paotui_auth_start(other)
        self.assertEqual(blocked.exception.code, "MEITUAN_ACCOUNT_IN_USE")

    def test_cached_official_authorization_connects_without_new_link(self):
        runner = FakeRunner(cached=True)
        shop = ShoppingService(self.bank, official=OfficialMeituanConnector(runner))
        result = shop.paotui_auth_start(self.user)
        self.assertTrue(result["status"]["connected"])
        self.assertTrue(result["reusedAuthorization"])
        self.assertNotIn("authUrl", result)
        self.assertEqual(shop.paotui_addresses(self.user)["addresses"][0]["id"], "addr-1")

    def test_preview_does_not_persist_real_phone_or_address(self):
        preview = self.make_preview()
        self.assertEqual(preview["deliveryFee"], 12)
        self.assertEqual(self.runner.preview_phone, "13800008000")
        with self.bank._db() as db:
            stored = db.execute("SELECT content FROM shopping_official_previews WHERE id=?", (preview["id"],)).fetchone()[0]
        self.assertNotIn("13800008000", stored)
        self.assertNotIn("测试路 88 号", stored)
        self.assertNotIn("2 栋 301", stored)

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


class MeituanRunnerCompatibilityTests(unittest.TestCase):
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
        result = runner.auth_start()

        self.assertEqual(result, {"alreadyConnected": True})
        self.assertEqual(calls, [(("login",), secret)])
        self.assertNotIn(secret, json.dumps(result))


if __name__ == "__main__":
    unittest.main()
