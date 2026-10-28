import tempfile
import unittest
from pathlib import Path

from backend.service import BankError, BankService
from backend.shopping import ShoppingService


def profile(style="A", reserve=8000):
    return {"goalType": "protect", "goalName": "保持现金安全", "target": 6000,
            "reserve": reserve, "incomeStability": "uncertain" if style == "A" else "stable",
            "style": style, "automation": "confirm", "readConsent": True}


class ShoppingServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "bank.sqlite")
        self.bank = BankService(self.path)
        session = self.bank.session("A")
        self.user = self.bank.authenticate(session["token"])
        self.bank.save_profile(self.user, profile())
        self.shop = ShoppingService(self.bank)

    def tearDown(self):
        self.tmp.cleanup()

    def _checkout(self, message="帮我点一份 30 元以内、40 分钟送到的牛肉饭"):
        result = self.shop.search(self.user, {"message": message})
        offer = result["search"]["offers"][0]
        return self.shop.checkout(self.user, {"searchId": result["search"]["id"], "offerId": offer["id"]})["checkout"]

    def test_full_mock_order_requires_confirmation_and_debits_once(self):
        checkout = self._checkout()
        self.assertEqual(checkout["status"], "pending_confirmation")
        before = self.bank.snapshot(self.user)["balance"]
        with self.assertRaises(BankError) as denied:
            self.shop.confirm(self.user, checkout["id"], {"confirmation": False,
                "confirmationToken": checkout["confirmationToken"], "requestKey": "order-one"})
        self.assertEqual(denied.exception.code, "CONFIRMATION_REQUIRED")
        payload = {"confirmation": True, "confirmationToken": checkout["confirmationToken"], "requestKey": "order-one"}
        first = self.shop.confirm(self.user, checkout["id"], payload)
        second = self.shop.confirm(self.user, checkout["id"], payload)
        self.assertEqual(first["order"]["id"], second["order"]["id"])
        self.assertEqual(first["order"]["status"], "paid")
        self.assertEqual(before - first["order"]["total"], first["snapshot"]["balance"])
        self.assertEqual(first["snapshot"]["balance"], second["snapshot"]["balance"])
        shopping_transactions = [tx for tx in second["snapshot"]["transactions"] if tx.get("shoppingCheckoutId") == checkout["id"]]
        self.assertEqual(len(shopping_transactions), 1)

    def test_budget_and_latest_cash_are_rechecked(self):
        blocked = self._checkout("帮我点一份 10 元以内的牛肉饭")
        self.assertEqual(blocked["status"], "blocked")
        with self.assertRaises(BankError) as denied:
            self.shop.confirm(self.user, blocked["id"], {"confirmation": True,
                "confirmationToken": blocked["confirmationToken"], "requestKey": "too-expensive"})
        self.assertEqual(denied.exception.code, "PURCHASE_BLOCKED")

        checkout = self._checkout()
        self.bank.event(self.user, {"type": "bill_added", "event_key": "shopping-stale"})
        with self.assertRaises(BankError) as stale:
            self.shop.confirm(self.user, checkout["id"], {"confirmation": True,
                "confirmationToken": checkout["confirmationToken"], "requestKey": "stale-order"})
        self.assertEqual(stale.exception.code, "STALE_APPROVAL")

    def test_user_isolation_and_mock_fulfilment(self):
        checkout = self._checkout("买一束 150 元以内的花")
        result = self.shop.confirm(self.user, checkout["id"], {"confirmation": True,
            "confirmationToken": checkout["confirmationToken"], "requestKey": "flower-order"})
        order = result["order"]
        states = []
        for _ in range(4):
            order = self.shop.advance(self.user, order["id"], {"confirmation": True})["order"]
            states.append(order["status"])
        self.assertEqual(states, ["merchant_confirmed", "preparing", "delivering", "delivered"])

        other_session = self.bank.session("B")
        other = self.bank.authenticate(other_session["token"])
        self.bank.save_profile(other, profile("B", 6000))
        with self.assertRaises(BankError) as hidden:
            self.shop.order(other, order["id"])
        self.assertEqual(hidden.exception.code, "ORDER_NOT_FOUND")

    def test_official_connector_is_visible_but_not_faked(self):
        providers = self.shop.providers(self.user)["providers"]
        official = next(item for item in providers if item["mode"] == "official_paotui")
        self.assertFalse(official["synthetic"])
        self.assertIn("询价", official["capabilities"])
        self.assertIn("支付跳转", official["capabilities"])


if __name__ == "__main__":
    unittest.main()
