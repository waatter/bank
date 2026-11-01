"""User-confirmed shopping flows with mock commerce and official Meituan Paotui."""
from __future__ import annotations

import json
import base64
import hashlib
import hmac
import re
import secrets
import threading
import time
from abc import ABC, abstractmethod

from .service import BankError, cents, now, uid, yuan
from .meituan_paotui import MeituanPaotuiRunner


class CommerceConnector(ABC):
    """Provider boundary used by the domain service; secrets stay server-side."""

    provider = "unknown"
    mode = "unknown"

    @property
    @abstractmethod
    def configured(self):
        raise NotImplementedError

    @abstractmethod
    def search(self, criteria):
        raise NotImplementedError

    @abstractmethod
    def create_order(self, checkout, request_key):
        raise NotImplementedError

    @abstractmethod
    def get_order(self, provider_order_id):
        raise NotImplementedError


class MockMeituanConnector(CommerceConnector):
    provider = "meituan"
    mode = "mock"

    CATALOG = (
        {"id": "mt-beef-rice", "category": "food", "merchant": "巷口牛肉饭（模拟）", "name": "招牌牛肉饭套餐",
         "description": "微辣可选，含例汤", "price": 2580, "deliveryFee": 300, "discount": 300, "etaMinutes": 32, "rating": 4.8,
         "keywords": ("外卖", "饭", "牛肉", "午餐", "晚餐", "吃")},
        {"id": "mt-chicken-rice", "category": "food", "merchant": "轻食厨房（模拟）", "name": "照烧鸡腿饭",
         "description": "默认不辣，可备注少饭", "price": 2380, "deliveryFee": 250, "discount": 200, "etaMinutes": 28, "rating": 4.7,
         "keywords": ("外卖", "饭", "鸡", "午餐", "晚餐", "吃")},
        {"id": "mt-noodle", "category": "food", "merchant": "老街面馆（模拟）", "name": "番茄牛腩面",
         "description": "不辣，热汤面", "price": 2200, "deliveryFee": 350, "discount": 100, "etaMinutes": 36, "rating": 4.6,
         "keywords": ("外卖", "面", "牛腩", "午餐", "晚餐", "吃")},
        {"id": "mt-salad", "category": "food", "merchant": "每日轻盈（模拟）", "name": "鸡胸谷物沙拉",
         "description": "酱汁分装", "price": 2980, "deliveryFee": 200, "discount": 500, "etaMinutes": 25, "rating": 4.9,
         "keywords": ("外卖", "轻食", "沙拉", "减脂", "健康", "吃")},
        {"id": "mt-milk-tea", "category": "drink", "merchant": "山茶小铺（模拟）", "name": "低糖鲜奶茶",
         "description": "可选三分糖或无糖", "price": 1680, "deliveryFee": 200, "discount": 300, "etaMinutes": 22, "rating": 4.7,
         "keywords": ("奶茶", "饮料", "喝", "咖啡", "外卖")},
        {"id": "mt-flower-pink", "category": "flower", "merchant": "春日花房（模拟）", "name": "粉色小花束",
         "description": "康乃馨与洋桔梗，含祝福卡", "price": 12900, "deliveryFee": 800, "discount": 1000, "etaMinutes": 75, "rating": 4.9,
         "keywords": ("花", "鲜花", "花束", "礼物", "生日")},
        {"id": "mt-flower-sun", "category": "flower", "merchant": "城市花礼（模拟）", "name": "向日葵暖阳花束",
         "description": "向日葵与配叶，含祝福卡", "price": 15900, "deliveryFee": 600, "discount": 1500, "etaMinutes": 90, "rating": 4.8,
         "keywords": ("花", "鲜花", "花束", "礼物", "向日葵")},
    )

    @property
    def configured(self):
        return True

    @staticmethod
    def _public(item):
        total = item["price"] + item["deliveryFee"] - item["discount"]
        return {k: item[k] for k in ("id", "category", "merchant", "name", "description", "etaMinutes", "rating")} | {
            "price": yuan(item["price"]), "deliveryFee": yuan(item["deliveryFee"]),
            "discount": yuan(item["discount"]), "total": yuan(total), "currency": "CNY",
            "source": "mock_meituan", "synthetic": True,
        }

    def search(self, criteria):
        message = criteria["message"].lower()
        wanted = []
        for item in self.CATALOG:
            score = sum(3 for word in item["keywords"] if word in message)
            if criteria.get("category") and item["category"] == criteria["category"]:
                score += 4
            if not wanted or score:
                wanted.append((score, item))
        if any(word in message for word in ("花", "花束", "鲜花")):
            wanted = [(score, item) for score, item in wanted if item["category"] == "flower"]
        elif any(word in message for word in ("外卖", "饭", "面", "吃", "奶茶", "饮料", "轻食")):
            wanted = [(score, item) for score, item in wanted if item["category"] in {"food", "drink"}]
        max_eta = criteria.get("maxDeliveryMinutes")
        if max_eta:
            wanted = [(score, item) for score, item in wanted if item["etaMinutes"] <= max_eta]
        offers = [self._public(item) for _, item in sorted(wanted, key=lambda row: (-row[0], row[1]["price"]))]
        budget = criteria.get("budgetCents")
        for offer in offers:
            offer["withinBudget"] = budget is None or cents(offer["total"], True) <= budget
        return offers[:8]

    def create_order(self, checkout, request_key):
        return {"providerOrderId": "mock_mt_" + request_key, "status": "paid", "paymentStatus": "mock_paid"}

    def get_order(self, provider_order_id):
        return {"providerOrderId": provider_order_id, "status": "paid", "paymentStatus": "mock_paid"}


class OfficialMeituanConnector(CommerceConnector):
    """Adapter for Meituan's official consumer Paotui Skill.

    Restaurant/menu ordering remains a separate commercial OpenH5/OpenAPI
    approval.  This connector only exposes the official help-buy/pickup/delivery
    capabilities shipped by meituan/MT-Paotui-For-Client.
    """

    provider = "meituan"
    mode = "official_paotui"

    def __init__(self, runner=None):
        self.runner = runner or MeituanPaotuiRunner()

    @property
    def configured(self):
        return self.runner.available

    @property
    def readiness(self):
        value = getattr(self.runner, "readiness", None)
        return value if isinstance(value, dict) else {
            "node": bool(self.runner.available),
            "skill": bool(self.runner.available),
            "passport": bool(self.runner.available),
            "storage": bool(self.runner.available),
        }

    def _unavailable(self):
        raise BankError(503, "OFFICIAL_CONNECTOR_NOT_ENABLED", "美团官方跑腿 Skill 尚未安装完整。")

    def search(self, criteria):
        self._unavailable()

    def create_order(self, checkout, request_key):
        self._unavailable()

    def get_order(self, provider_order_id):
        self._unavailable()


class ShoppingService:
    ORDER_STATES = ("paid", "merchant_confirmed", "preparing", "delivering", "delivered")
    ADDRESS_LABELS = {"A": "常用地址 · 尾号 1024（模拟）", "B": "公司地址 · 尾号 3381（模拟）", "C": "家庭地址 · 尾号 5560（模拟）"}
    MEITUAN_CREDENTIAL_SCOPE = "per_user_v1"

    def __init__(self, bank_service, mock=None, official=None):
        self.bank = bank_service
        self.mock = mock or MockMeituanConnector()
        self.official = official or OfficialMeituanConnector()
        self._auth_tasks_guard = threading.Lock()
        self._auth_tasks = {}
        # Authorization links are short lived and user scoped.  Keep them only
        # in server memory so a browser refresh (especially on mobile) can
        # reopen the official page without persisting the link or any token in
        # the application database.
        self._auth_links = {}

    def _remember_auth_link(self, user_id, attempt_id, auth_url, expires_at):
        with self._auth_tasks_guard:
            self._auth_links[user_id] = {
                "attemptId": attempt_id,
                "authUrl": auth_url,
                "expiresAt": float(expires_at),
            }

    def _pending_auth_link(self, user_id, attempt_id):
        with self._auth_tasks_guard:
            entry = self._auth_links.get(user_id)
            if not entry or entry.get("attemptId") != attempt_id:
                return None
            if float(entry.get("expiresAt") or 0) <= time.time():
                self._auth_links.pop(user_id, None)
                return None
            return dict(entry)

    def _forget_auth_link(self, user_id, attempt_id=None):
        with self._auth_tasks_guard:
            entry = self._auth_links.get(user_id)
            if entry and (attempt_id is None or entry.get("attemptId") == attempt_id):
                self._auth_links.pop(user_id, None)

    def _auth_task(self, user_id):
        with self._auth_tasks_guard:
            task = self._auth_tasks.get(user_id)
            if task and task["thread"].is_alive():
                return task
            if task:
                self._auth_tasks.pop(user_id, None)
            return None

    def _start_auth_poll(self, user_id, attempt_id, *, immediate=False):
        """Start at most one provider poll for this TROPOS user.

        The official helper can take several seconds after the user confirms the
        authorization.  Keep that wait out of the HTTP request and only commit a
        result when it still belongs to the same authorization attempt.
        """
        with self._auth_tasks_guard:
            current = self._auth_tasks.get(user_id)
            if current and current["thread"].is_alive():
                if current["attemptId"] == attempt_id and immediate:
                    current["wake"].set()
                return
            wake = threading.Event()
            task = {"attemptId": attempt_id, "wake": wake}

            def poll():
                try:
                    # Let the auth-start response reach the browser before the
                    # provider process begins its long poll.  A manual check or
                    # a status refresh wakes it immediately.
                    wake.wait(0 if immediate else 0.5)
                    self.official.runner.auth_complete(user_id)
                    state = {"status": "connected", "connectedAt": now(),
                             "credentialScope": self.MEITUAN_CREDENTIAL_SCOPE}
                    with self.bank._db(True) as db:
                        binding = self._binding(db, user_id)
                        if not binding or binding.get("status") != "pending" or \
                                binding.get("attemptId") != attempt_id:
                            return
                        db.execute("UPDATE shopping_provider_bindings SET content=? "
                                   "WHERE provider='meituan_paotui' AND user_id=?",
                                   (json.dumps(state, ensure_ascii=False), user_id))
                        self.bank._audit(
                            db, user_id, "ARGOS", "美团官方跑腿已连接",
                            "本人授权及令牌同步成功；令牌由官方授权工具保管，未写入 TROPOS 数据库。")
                except BankError as error:
                    expired = error.code in {"MEITUAN_TIMEOUT", "MEITUAN_AUTH_TIMEOUT",
                                             "MEITUAN_AUTH_SESSION_MISSING"}
                    failure = {
                        "status": "expired" if expired else "failed",
                        "attemptId": attempt_id,
                        "credentialScope": self.MEITUAN_CREDENTIAL_SCOPE,
                        "authErrorCode": error.code,
                        "authMessage": ("美团授权入口已过期，请重新连接。" if expired else
                                        error.message),
                        "failedAt": now(),
                    }
                    with self.bank._db(True) as db:
                        binding = self._binding(db, user_id)
                        if not binding or binding.get("status") != "pending" or \
                                binding.get("attemptId") != attempt_id:
                            return
                        db.execute("UPDATE shopping_provider_bindings SET content=? "
                                   "WHERE provider='meituan_paotui' AND user_id=?",
                                   (json.dumps(failure, ensure_ascii=False), user_id))
                        self.bank._audit(db, user_id, "ARGOS", "美团授权未完成",
                                         failure["authMessage"])
                except Exception:
                    failure = {
                        "status": "failed", "attemptId": attempt_id,
                        "credentialScope": self.MEITUAN_CREDENTIAL_SCOPE,
                        "authErrorCode": "MEITUAN_AUTH_SYNC_FAILED",
                        "authMessage": "美团授权同步服务暂时不可用，请稍后重新连接。",
                        "failedAt": now(),
                    }
                    with self.bank._db(True) as db:
                        binding = self._binding(db, user_id)
                        if binding and binding.get("status") == "pending" and \
                                binding.get("attemptId") == attempt_id:
                            db.execute("UPDATE shopping_provider_bindings SET content=? "
                                       "WHERE provider='meituan_paotui' AND user_id=?",
                                       (json.dumps(failure, ensure_ascii=False), user_id))
                            self.bank._audit(db, user_id, "ARGOS", "美团授权同步异常",
                                             failure["authMessage"])
                finally:
                    with self._auth_tasks_guard:
                        if self._auth_tasks.get(user_id) is task:
                            self._auth_tasks.pop(user_id, None)
                        link = self._auth_links.get(user_id)
                        if link and link.get("attemptId") == attempt_id:
                            self._auth_links.pop(user_id, None)

            thread = threading.Thread(target=poll, name="tropos-meituan-auth", daemon=True)
            task["thread"] = thread
            self._auth_tasks[user_id] = task
            thread.start()

    def providers(self, user_id):
        with self.bank._db() as db:
            self.bank._user(db, user_id)
            binding = db.execute(
                "SELECT content FROM shopping_provider_bindings WHERE provider='meituan_paotui' AND user_id=?",
                (user_id,)).fetchone()
            binding_state = json.loads(binding["content"]) if binding else {}
        return {"providers": [
            {"id": "meituan_mock", "provider": "meituan", "mode": "mock", "ready": True,
             "label": "美团 Mock", "synthetic": True},
            {"id": "meituan_paotui", "provider": "meituan", "mode": "official_paotui",
             "ready": self.official.configured,
             "connected": binding_state.get("status") == "connected" and
                          binding_state.get("credentialScope") == self.MEITUAN_CREDENTIAL_SCOPE,
             "authPending": binding_state.get("status") == "pending", "ownedByAnother": False,
             "label": "美团官方跑腿 Skill", "synthetic": False,
             "capabilities": ["帮买", "帮取送", "询价", "下单", "支付跳转", "状态查询"]},
        ]}

    @staticmethod
    def _message(value):
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= 300:
            raise BankError(400, "INVALID_SHOPPING_REQUEST", "请输入 1 至 300 字的购物需求。")
        return value.strip()

    @staticmethod
    def _positive_int(value, name, maximum):
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
            raise BankError(400, "INVALID_SHOPPING_REQUEST", f"{name}不正确。")
        return value

    def _criteria(self, payload):
        if not isinstance(payload, dict):
            raise BankError(400, "INVALID_SHOPPING_REQUEST", "购物需求格式不正确。")
        message = self._message(payload.get("message"))
        budget = payload.get("budget")
        if budget is None:
            match = re.search(r"(\d+(?:\.\d{1,2})?)\s*元(?:以内|以下|左右|预算)?", message)
            budget = match.group(1) if match else None
        budget_cents = cents(budget, True) if budget is not None else None
        eta = payload.get("maxDeliveryMinutes")
        if eta is None:
            match = re.search(r"(\d{1,3})\s*分钟", message)
            eta = int(match.group(1)) if match else None
        eta = self._positive_int(eta, "送达时间", 360)
        category = payload.get("category")
        if category is not None and category not in {"food", "drink", "flower"}:
            raise BankError(400, "INVALID_SHOPPING_REQUEST", "暂只支持外卖、饮品和鲜花演示。")
        return {"message": message, "budgetCents": budget_cents, "budget": yuan(budget_cents) if budget_cents else None,
                "maxDeliveryMinutes": eta, "category": category}

    def search(self, user_id, payload):
        criteria = self._criteria(payload)
        with self.bank._db(True) as db:
            self.bank._consent(db, user_id)
            user, _ = self.bank._user(db, user_id)
            offers = self.mock.search(criteria)
            search = {"id": uid("shop_search"), "provider": "meituan", "mode": "mock", "synthetic": True,
                      "criteria": {k: v for k, v in criteria.items() if k != "budgetCents"}, "offers": offers,
                      "address": self.ADDRESS_LABELS[user["persona"]], "createdAt": now(), "expiresAt": time.time() + 900}
            db.execute("INSERT INTO shopping_searches VALUES(?,?,?,?)", (search["id"], user_id, json.dumps(search, ensure_ascii=False), time.time()))
            self.bank._audit(db, user_id, "METIS", "已生成模拟购物候选", f"需求：{criteria['message']}；共 {len(offers)} 个合成候选；尚未下单或扣款。")
            return {"search": search, "snapshot": self.bank._snapshot(db, user_id)}

    def _checks(self, db, user_id, amount, offer, search):
        profile = self.bank._profile(db, user_id)
        balance = self.bank._balance(db, user_id)
        cash = self.bank._cash(db, user_id, amount)
        budget = search.get("criteria", {}).get("budget")
        return [
            {"name": "Mock 商品可用", "ok": offer.get("synthetic") is True, "detail": "当前仅使用合成商家和商品。"},
            {"name": "用户预算", "ok": budget is None or amount <= cents(budget, True),
             "detail": "订单总额不得超过本次需求中的预算。" if budget is not None else "本次未设置单独预算。"},
            {"name": "已到账余额", "ok": balance >= amount, "detail": "预计收入不作为当前可支付余额。"},
            {"name": "当前现金底线", "ok": balance - amount >= cents(profile["reserve"]), "detail": "支付后仍须保留你设定的现金。"},
            {"name": "30 天现金路径", "ok": cash["free"] >= 0,
             "detail": f"模拟支付后预计最低 ¥{cash['min']:,.2f}，已计必要账单和订阅。"},
        ]

    def checkout(self, user_id, payload):
        if not isinstance(payload, dict) or set(payload) - {"searchId", "offerId", "addressId"}:
            raise BankError(400, "INVALID_CHECKOUT", "结算请求格式不正确。")
        search_id, offer_id = payload.get("searchId"), payload.get("offerId")
        if not isinstance(search_id, str) or not isinstance(offer_id, str):
            raise BankError(400, "INVALID_CHECKOUT", "请选择本次搜索返回的商品。")
        if payload.get("addressId", "home") != "home":
            raise BankError(400, "INVALID_ADDRESS", "演示仅支持当前账户的模拟常用地址。")
        with self.bank._db(True) as db:
            self.bank._consent(db, user_id)
            row = db.execute("SELECT content FROM shopping_searches WHERE id=? AND user_id=?", (search_id, user_id)).fetchone()
            if not row:
                raise BankError(404, "SEARCH_NOT_FOUND", "购物候选不存在或不属于当前账户。")
            search = json.loads(row[0])
            if search["expiresAt"] < time.time():
                raise BankError(409, "SEARCH_EXPIRED", "商品候选已过期，请重新搜索价格和配送时间。")
            offer = next((item for item in search["offers"] if item["id"] == offer_id), None)
            if not offer:
                raise BankError(400, "OFFER_NOT_FOUND", "请选择本次搜索返回的商品。")
            amount = cents(offer["total"], True)
            user, _ = self.bank._user(db, user_id)
            checks = self._checks(db, user_id, amount, offer, search)
            checkout = {"id": uid("checkout"), "searchId": search_id, "provider": "meituan", "mode": "mock",
                        "synthetic": True, "offer": offer, "address": self.ADDRESS_LABELS[user["persona"]],
                        "total": yuan(amount), "currency": "CNY", "checks": checks,
                        "revision": user["revision"], "status": "pending_confirmation" if all(c["ok"] for c in checks) else "blocked",
                        "confirmationToken": secrets.token_urlsafe(24), "createdAt": now(), "expiresAt": time.time() + 600}
            db.execute("INSERT INTO shopping_checkouts VALUES(?,?,?)", (checkout["id"], user_id, json.dumps(checkout, ensure_ascii=False)))
            self.bank._audit(db, user_id, "ARGOS", "模拟订单待你确认" if checkout["status"] == "pending_confirmation" else "模拟订单被预算检查拦截",
                             f"{offer['merchant']} · {offer['name']}；总额 ¥{checkout['total']:,.2f}；尚未下单或扣款。")
            return {"checkout": checkout, "snapshot": self.bank._snapshot(db, user_id)}

    def _checkout(self, db, user_id, checkout_id):
        row = db.execute("SELECT content FROM shopping_checkouts WHERE id=? AND user_id=?", (checkout_id, user_id)).fetchone()
        if not row:
            raise BankError(404, "CHECKOUT_NOT_FOUND", "待确认订单不存在或不属于当前账户。")
        return json.loads(row[0])

    def confirm(self, user_id, checkout_id, payload):
        self.bank._confirm(payload)
        if not isinstance(payload, dict) or set(payload) - {"confirmation", "confirmationToken", "requestKey"}:
            raise BankError(400, "INVALID_CONFIRMATION", "订单确认格式不正确。")
        request_key = payload.get("requestKey", checkout_id)
        if not isinstance(request_key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_key):
            raise BankError(400, "INVALID_KEY", "请提供有效的下单请求凭证。")
        with self.bank._db(True) as db:
            self.bank._consent(db, user_id)
            checkout = self._checkout(db, user_id, checkout_id)
            existing = db.execute("SELECT content FROM shopping_orders WHERE user_id=? AND json_extract(content,'$.checkoutId')=?", (user_id, checkout_id)).fetchone()
            if existing:
                return {"order": json.loads(existing[0]), "snapshot": self.bank._snapshot(db, user_id)}
            if payload.get("confirmationToken") != checkout.get("confirmationToken"):
                raise BankError(403, "INVALID_APPROVAL", "确认凭证与本次订单不匹配。")
            used = db.execute("SELECT resource FROM idempotency WHERE user_id=? AND key=?", (user_id, request_key)).fetchone()
            if used and used[0] != checkout_id:
                raise BankError(409, "KEY_REUSED", "此下单凭证已用于另一笔操作。")
            user, _ = self.bank._user(db, user_id)
            if checkout["revision"] != user["revision"]:
                raise BankError(409, "STALE_APPROVAL", "账户或画像已变化，请重新结算后确认。")
            if checkout["expiresAt"] < time.time():
                raise BankError(409, "APPROVAL_EXPIRED", "订单价格确认已过期，请重新结算。")
            amount = cents(checkout["total"], True)
            search_row = db.execute("SELECT content FROM shopping_searches WHERE id=? AND user_id=?", (checkout["searchId"], user_id)).fetchone()
            if not search_row:
                raise BankError(409, "SEARCH_NOT_FOUND", "原商品搜索记录不存在，请重新搜索。")
            checks = self._checks(db, user_id, amount, checkout["offer"], json.loads(search_row[0]))
            if not all(check["ok"] for check in checks):
                raise BankError(409, "PURCHASE_BLOCKED", "当前预算或现金条件未满足，未创建订单。", {"checks": checks})
            provider = self.mock.create_order(checkout, request_key)
            before = self.bank._summary(db, user_id)
            category = "鲜花" if checkout["offer"]["category"] == "flower" else "餐饮"
            transaction = self.bank._post(db, user_id, checkout_id, category, amount, checkout["offer"]["merchant"], "external",
                                          {"shoppingCheckoutId": checkout_id, "provider": "meituan_mock", "synthetic": True})
            order = {"id": uid("shop_order"), "checkoutId": checkout_id, "requestKey": request_key,
                     "provider": "meituan", "mode": "mock", "synthetic": True,
                     "providerOrderId": provider["providerOrderId"], "status": provider["status"],
                     "paymentStatus": provider["paymentStatus"], "offer": checkout["offer"], "address": checkout["address"],
                     "total": checkout["total"], "currency": "CNY", "transactionId": transaction["id"],
                     "receipt": transaction["receipt"], "createdAt": now(),
                     "timeline": [{"status": "paid", "label": "模拟支付完成，等待商家确认", "at": now()}]}
            db.execute("INSERT INTO shopping_orders VALUES(?,?,?,?)", (order["id"], user_id, request_key, json.dumps(order, ensure_ascii=False)))
            db.execute("INSERT OR IGNORE INTO idempotency VALUES(?,?,?,?,?)", (user_id, request_key, "shopping_order", checkout_id, checkout_id))
            checkout.update(status="ordered", orderId=order["id"], transactionId=transaction["id"])
            checkout.pop("confirmationToken", None)
            db.execute("UPDATE shopping_checkouts SET content=? WHERE id=?", (json.dumps(checkout, ensure_ascii=False), checkout_id))
            db.execute("INSERT INTO authorizations VALUES(?,?,?,?,?,?)", (uid("auth"), user_id, user["revision"], "shopping_order",
                       json.dumps({"checkoutId": checkout_id, "requestKey": request_key, "total": checkout["total"]}), now()))
            self.bank._changed(db, user_id, before, "模拟美团订单已创建", "用户逐笔确认",
                               f"{checkout['offer']['name']}；模拟支付 ¥{checkout['total']:,.2f}；已记录合成回执，不代表真实美团订单。")
            return {"order": order, "transaction": transaction, "snapshot": self.bank._snapshot(db, user_id)}

    # ---- Official Meituan Paotui Skill ---------------------------------

    @staticmethod
    def _masked_phone(value):
        text = str(value or "")
        digits = re.sub(r"\D", "", text)
        if len(digits) >= 7:
            return digits[:3] + "****" + digits[-4:]
        return text if "****" in text else "已由美团保护"

    @staticmethod
    def _address_public(item):
        return {
            "id": str(item.get("addressId", "")),
            "address": str(item.get("address", ""))[:160],
            "houseNumber": str(item.get("houseNumber", ""))[:80],
            "name": str(item.get("name", ""))[:50],
            "phone": ShoppingService._masked_phone(item.get("phone")),
            "isDefault": bool(item.get("isDefault")),
            "lastUseTime": item.get("lastUseTime"),
        }

    @staticmethod
    def _recipient(item, detail):
        phone = str(detail.get("phone", ""))
        if not re.fullmatch(r"1\d{10}", phone):
            raise BankError(502, "MEITUAN_PHONE_UNAVAILABLE", "该地址没有可用于下单的完整联系方式。")
        try:
            lat, lng = int(item.get("lat")), int(item.get("lng"))
        except (TypeError, ValueError):
            raise BankError(502, "MEITUAN_ADDRESS_INVALID", "美团地址缺少有效坐标，请重新选择地址。") from None
        return {
            "address": str(item.get("address", ""))[:160],
            "houseNumber": str(item.get("houseNumber", ""))[:80],
            "lat": lat, "lng": lng,
            "name": str(item.get("name", ""))[:50],
            "phone": phone,
            "cityId": item.get("cityId"),
        }

    @staticmethod
    def _recipient_fingerprint(recipient):
        raw = json.dumps(recipient, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _manual_text(value, label, minimum, maximum):
        if not isinstance(value, str):
            raise BankError(400, "INVALID_MANUAL_ADDRESS", f"{label}格式不正确。")
        text = re.sub(r"\s+", " ", value).strip()
        if not minimum <= len(text) <= maximum or any(ord(ch) < 32 for ch in text):
            raise BankError(400, "INVALID_MANUAL_ADDRESS", f"请填写有效的{label}。")
        return text

    @staticmethod
    def _poi_coordinate(candidate, primary, alternate):
        containers = [candidate]
        if isinstance(candidate, dict) and isinstance(candidate.get("location"), dict):
            containers.append(candidate["location"])
        value = None
        for container in containers:
            if not isinstance(container, dict):
                continue
            value = container.get(primary)
            if value is None:
                value = container.get(alternate)
            if value is not None:
                break
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if abs(number) < 1000:
            number *= 1_000_000
        return int(round(number))

    def _manual_recipient(self, user_id, payload):
        if not isinstance(payload, dict) or set(payload) != {"city", "address", "houseNumber", "name", "phone"}:
            raise BankError(400, "INVALID_MANUAL_ADDRESS", "临时地址格式不正确。")
        city = self._manual_text(payload.get("city"), "城市", 2, 40)
        address = self._manual_text(payload.get("address"), "收货地址", 4, 160)
        house = self._manual_text(payload.get("houseNumber"), "门牌号", 1, 80)
        name = self._manual_text(payload.get("name"), "联系人", 1, 50)
        raw_phone = self._manual_text(payload.get("phone"), "手机号", 11, 20)
        phone = re.sub(r"[\s-]", "", raw_phone)
        if not re.fullmatch(r"1\d{10}", phone):
            raise BankError(400, "INVALID_MANUAL_ADDRESS", "请填写有效的中国大陆手机号。")

        candidates = self.official.runner.search_poi(user_id, f"{address} {house}", city)
        selected = None
        lat = lng = None
        for candidate in candidates[:20]:
            if not isinstance(candidate, dict):
                continue
            lat = self._poi_coordinate(candidate, "lat", "latitude")
            lng = self._poi_coordinate(candidate, "lng", "longitude")
            if lat is not None and lng is not None:
                selected = candidate
                break
        if selected is None:
            raise BankError(422, "MEITUAN_POI_NOT_FOUND",
                            "美团没有定位到该地址，请补充区县、街道、小区或楼号后重试。")
        recipient = {
            "address": address, "houseNumber": house, "lat": lat, "lng": lng,
            "name": name, "phone": phone,
        }
        if selected.get("cityId") not in (None, ""):
            recipient["cityId"] = selected["cityId"]
        public = {
            "id": "manual", "source": "manual", "address": address,
            "houseNumber": house, "name": name, "phone": self._masked_phone(phone),
            "isDefault": False, "lastUseTime": None,
        }
        return recipient, public

    def _manual_token(self, user_id, recipient, expires_at):
        payload = json.dumps({"userId": user_id, "recipient": recipient, "expiresAt": expires_at},
                             ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        body = base64.urlsafe_b64encode(payload).rstrip(b"=")
        signature = hmac.new(self.bank.auth_master_key, b"tropos-manual-address\0" + body,
                             hashlib.sha256).digest()
        return (body + b"." + base64.urlsafe_b64encode(signature).rstrip(b"=")).decode("ascii")

    def _recipient_from_manual_token(self, user_id, token, fingerprint):
        if not isinstance(token, str) or not 40 <= len(token) <= 4096 or token.count(".") != 1:
            raise BankError(403, "INVALID_MANUAL_ADDRESS_TOKEN", "临时地址凭证无效，请重新询价。")
        body_text, signature_text = token.split(".", 1)
        try:
            body = body_text.encode("ascii")
            signature = base64.urlsafe_b64decode(signature_text + "=" * (-len(signature_text) % 4))
            expected = hmac.new(self.bank.auth_master_key, b"tropos-manual-address\0" + body,
                                hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError
            decoded = base64.urlsafe_b64decode(body_text + "=" * (-len(body_text) % 4))
            data = json.loads(decoded.decode("utf-8"))
        except (UnicodeError, ValueError, TypeError, json.JSONDecodeError):
            raise BankError(403, "INVALID_MANUAL_ADDRESS_TOKEN", "临时地址凭证无效，请重新询价。") from None
        if data.get("userId") != user_id or float(data.get("expiresAt") or 0) < time.time():
            raise BankError(403, "INVALID_MANUAL_ADDRESS_TOKEN", "临时地址凭证已过期，请重新询价。")
        recipient = data.get("recipient")
        if not isinstance(recipient, dict) or self._recipient_fingerprint(recipient) != fingerprint:
            raise BankError(403, "INVALID_MANUAL_ADDRESS_TOKEN", "临时地址与询价不匹配，请重新询价。")
        return recipient

    @staticmethod
    def _delivery_yuan(quote):
        display = str(quote.get("feeDisplay", ""))
        match = re.search(r"(\d+(?:\.\d{1,2})?)", display.replace(",", ""))
        if match:
            return yuan(cents(match.group(1), True))
        raw = quote.get("_previewTotal", quote.get("previewTotal", quote.get("_deliveryFee", quote.get("deliveryFee"))))
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            amount = raw / 100 if isinstance(raw, int) and raw >= 500 else raw
            return yuan(cents(amount, True))
        raise BankError(502, "MEITUAN_INVALID_RESPONSE", "美团没有返回可验证的配送费用。")

    def _binding(self, db, user_id, *, required=False):
        row = db.execute(
            "SELECT content FROM shopping_provider_bindings WHERE provider='meituan_paotui' AND user_id=?",
            (user_id,)).fetchone()
        if not row:
            if required:
                raise BankError(401, "MEITUAN_AUTH_REQUIRED", "请先连接美团账号。")
            return None
        content = json.loads(row["content"])
        if required and (content.get("status") != "connected" or
                         content.get("credentialScope") != self.MEITUAN_CREDENTIAL_SCOPE):
            raise BankError(401, "MEITUAN_AUTH_REQUIRED", "请先完成美团账号授权。")
        return content

    def paotui_status(self, user_id):
        with self.bank._db() as db:
            self.bank._user(db, user_id)
            binding = self._binding(db, user_id)
            scoped = bool(binding and binding.get("credentialScope") == self.MEITUAN_CREDENTIAL_SCOPE)
            status = binding.get("status") if binding else None
            result = {
                "provider": "meituan", "mode": "official_paotui", "installed": self.official.configured,
                "components": self.official.readiness,
                "connected": bool(binding and status == "connected" and scoped),
                "authPending": bool(binding and status == "pending"),
                "authState": ("connected" if status == "connected" and scoped else
                              "waiting_token" if status == "pending" else
                              status if status in {"expired", "failed"} else "disconnected"),
                "authMessage": binding.get("authMessage") if status in {"expired", "failed"} else None,
                "requiresReauthorization": bool(binding and status == "connected" and not scoped),
                "ownedByAnother": False,
                "accountScoped": True,
                "localOnly": False,
            }
            attempt_id = binding.get("attemptId") if status == "pending" else None
            pending_link = self._pending_auth_link(user_id, attempt_id) if attempt_id else None
            if pending_link:
                result["authUrl"] = pending_link["authUrl"]
                result["authExpiresAt"] = pending_link["expiresAt"]
        if attempt_id and self.official.configured:
            self._start_auth_poll(user_id, attempt_id)
        return result

    def paotui_auth_start(self, user_id):
        if not self.official.configured:
            self.official._unavailable()
        already_connected = False
        pending_attempt = None
        with self.bank._db() as db:
            self.bank._consent(db, user_id)
            current = self._binding(db, user_id)
            if (current and current.get("status") == "connected" and
                    current.get("credentialScope") == self.MEITUAN_CREDENTIAL_SCOPE):
                already_connected = True
            elif current and current.get("status") == "pending" and \
                    float(current.get("expiresAt") or 0) > time.time():
                pending_attempt = current.get("attemptId")
        if already_connected:
            return {"status": self.paotui_status(user_id)}
        if pending_attempt:
            self._start_auth_poll(user_id, pending_attempt, immediate=True)
            return {"status": self.paotui_status(user_id), "authorizationPending": True}
        authorization = self.official.runner.auth_start(user_id)
        if authorization.get("alreadyConnected") is True:
            state = {"status": "connected", "connectedAt": now(), "authorizationSource": "cached",
                     "credentialScope": self.MEITUAN_CREDENTIAL_SCOPE}
            with self.bank._db(True) as db:
                self._binding(db, user_id)
                db.execute("INSERT INTO shopping_provider_bindings(provider,user_id,content) VALUES('meituan_paotui',?,?) "
                           "ON CONFLICT(provider,user_id) DO UPDATE SET content=excluded.content",
                           (user_id, json.dumps(state, ensure_ascii=False)))
                self.bank._audit(db, user_id, "ARGOS", "美团官方跑腿已连接",
                                 "已复用当前用户仍有效的独立官方授权；令牌由官方授权工具保管，未写入 TROPOS 数据库。")
            return {"status": self.paotui_status(user_id), "reusedAuthorization": True}
        attempt_id = secrets.token_urlsafe(18)
        expires_at = time.time() + authorization["expiresInSeconds"]
        state = {"status": "pending", "startedAt": now(), "expiresAt": expires_at,
                 "attemptId": attempt_id,
                 "credentialScope": self.MEITUAN_CREDENTIAL_SCOPE}
        with self.bank._db(True) as db:
            self._binding(db, user_id)
            db.execute("INSERT INTO shopping_provider_bindings(provider,user_id,content) VALUES('meituan_paotui',?,?) "
                       "ON CONFLICT(provider,user_id) DO UPDATE SET content=excluded.content",
                       (user_id, json.dumps(state, ensure_ascii=False)))
            self.bank._audit(db, user_id, "ARGOS", "等待美团账号授权", "已生成十分钟有效的官方授权入口；尚未读取地址或创建订单。")
        self._remember_auth_link(user_id, attempt_id, authorization["authUrl"], expires_at)
        result = {"status": self.paotui_status(user_id), **authorization}
        self._start_auth_poll(user_id, attempt_id)
        return result

    def paotui_auth_complete(self, user_id):
        with self.bank._db() as db:
            self.bank._consent(db, user_id)
            current = self._binding(db, user_id, required=False)
            if not current or current.get("status") not in {"pending", "connected"}:
                raise BankError(409, "MEITUAN_AUTH_NOT_STARTED", "请先生成美团授权链接。")
        if current.get("status") == "pending":
            self._start_auth_poll(user_id, current.get("attemptId"), immediate=True)
        return {"status": self.paotui_status(user_id)}

    def paotui_logout(self, user_id, payload):
        self.bank._confirm(payload)
        with self.bank._db() as db:
            self._binding(db, user_id, required=False)
        self.official.runner.logout(user_id)
        with self.bank._db(True) as db:
            db.execute("DELETE FROM shopping_provider_bindings WHERE provider='meituan_paotui' AND user_id=?", (user_id,))
            self.bank._audit(db, user_id, "ARGOS", "已断开美团官方跑腿", "当前用户的独立授权已清除；历史真实订单引用仍保留用于审计。")
        self._forget_auth_link(user_id)
        return {"status": self.paotui_status(user_id)}

    def _official_addresses(self, user_id):
        with self.bank._db() as db:
            self.bank._consent(db, user_id)
            self._binding(db, user_id, required=True)
        addresses = self.official.runner.addresses(user_id)
        result = [self._address_public(item) for item in addresses[:50] if item.get("addressId")]
        return addresses, result

    def paotui_addresses(self, user_id):
        _, public = self._official_addresses(user_id)
        return {"addresses": public, "status": self.paotui_status(user_id)}

    def _official_recipient(self, user_id, address_id):
        addresses, public = self._official_addresses(user_id)
        item = next((item for item in addresses if str(item.get("addressId")) == address_id), None)
        if not item:
            raise BankError(404, "MEITUAN_ADDRESS_NOT_FOUND", "请选择当前美团地址簿中的地址。")
        detail = self.official.runner.address_info(user_id, address_id)
        public_item = next(entry for entry in public if entry["id"] == address_id)
        return self._recipient(item, detail), public_item

    def _official_checks(self, db, user_id, item_budget, delivery_fee):
        profile = self.bank._profile(db, user_id)
        balance = self.bank._balance(db, user_id)
        exposure = item_budget + delivery_fee
        cash = self.bank._cash(db, user_id, exposure)
        return [
            {"name": "本人美团账号", "ok": True, "detail": "真实订单仅使用当前用户独立授权的美团账号。"},
            {"name": "商品最高预算", "ok": item_budget > 0, "detail": f"已把商品代购上限按 ¥{yuan(item_budget):,.2f} 计入风险敞口。"},
            {"name": "美团实时配送费", "ok": delivery_fee > 0, "detail": f"官方询价配送费为 ¥{yuan(delivery_fee):,.2f}。"},
            {"name": "已到账余额", "ok": balance >= exposure, "detail": "商品预算加配送费不得超过当前模拟账户已到账余额。"},
            {"name": "当前现金底线", "ok": balance - exposure >= cents(profile["reserve"]), "detail": "按最高预算估算后仍须保留现金底线。"},
            {"name": "30 天现金路径", "ok": cash["free"] >= 0,
             "detail": f"按最高预算估算后预计最低 ¥{cash['min']:,.2f}；真实支付仍在美团收银台完成。"},
        ]

    @staticmethod
    def _official_preview_public(preview, address=None):
        return {
            "id": preview["id"], "provider": "meituan", "mode": "official_paotui", "synthetic": False,
            "purchaseDetail": preview["purchaseDetail"], "maxItemBudget": preview["maxItemBudget"],
            "deliveryFee": preview["deliveryFee"], "feeDisplay": preview["feeDisplay"],
            "estimateTime": preview.get("estimateTime"), "address": address,
            "checks": preview["checks"], "status": preview["status"],
            "confirmationToken": preview.get("confirmationToken"), "expiresAt": preview["expiresAt"],
            "notice": "商品价格不由跑腿询价接口返回；本页预算是你设置的最高上限，实际支付以美团收银台和骑手沟通为准。",
        }

    def paotui_preview(self, user_id, payload):
        if not isinstance(payload, dict) or set(payload) - {"purchaseDetail", "addressId", "manualAddress", "maxItemBudget", "remark"}:
            raise BankError(400, "INVALID_PAOTUI_REQUEST", "跑腿需求格式不正确。")
        detail = self._message(payload.get("purchaseDetail"))
        address_id = payload.get("addressId")
        manual_address = payload.get("manualAddress")
        if bool(address_id) == bool(manual_address):
            raise BankError(400, "INVALID_PAOTUI_REQUEST", "请选择一个美团地址或填写一个临时地址。")
        if address_id and (not isinstance(address_id, str) or not 1 <= len(address_id) <= 128):
            raise BankError(400, "INVALID_PAOTUI_REQUEST", "请选择美团地址簿中的送达地址。")
        item_budget = cents(payload.get("maxItemBudget"), True)
        if item_budget > 500_000:
            raise BankError(400, "INVALID_PAOTUI_REQUEST", "单次商品最高预算不能超过 ¥5,000。")
        remark = payload.get("remark", "")
        if not isinstance(remark, str) or len(remark) > 120:
            raise BankError(400, "INVALID_PAOTUI_REQUEST", "备注不能超过 120 字。")
        if manual_address:
            recipient, address = self._manual_recipient(user_id, manual_address)
            address_source = "manual"
        else:
            recipient, address = self._official_recipient(user_id, address_id)
            address_source = "official"
        provider_detail = detail + f"；商品最高预算 ¥{yuan(item_budget):.2f}"
        quote = self.official.runner.preview_buy(user_id, recipient, provider_detail)
        delivery_yuan = self._delivery_yuan(quote)
        delivery_fee = cents(delivery_yuan, True)
        with self.bank._db(True) as db:
            self.bank._consent(db, user_id)
            self._binding(db, user_id, required=True)
            user, _ = self.bank._user(db, user_id)
            checks = self._official_checks(db, user_id, item_budget, delivery_fee)
            preview = {
                "id": uid("paotui_preview"), "addressId": address_id,
                "addressSource": address_source,
                "recipientFingerprint": self._recipient_fingerprint(recipient),
                "purchaseDetail": detail, "providerPurchaseDetail": provider_detail,
                "maxItemBudgetCents": item_budget, "maxItemBudget": yuan(item_budget),
                "deliveryFeeCents": delivery_fee, "deliveryFee": yuan(delivery_fee),
                "feeDisplay": str(quote.get("feeDisplay") or f"¥{delivery_yuan:.2f}"),
                "estimateTime": quote.get("estimateTime"), "remark": remark,
                "providerQuote": {"orderToken": quote.get("orderToken"),
                                  "deliveryFee": quote.get("_deliveryFee", quote.get("deliveryFee")),
                                  "previewTotal": quote.get("_previewTotal", quote.get("previewTotal", quote.get("_deliveryFee", quote.get("deliveryFee")))),
                                  "couponViewId": quote.get("_couponViewId", quote.get("couponViewId"))},
                "checks": checks, "revision": user["revision"],
                "status": "pending_confirmation" if all(row["ok"] for row in checks) else "blocked",
                "confirmationToken": secrets.token_urlsafe(24), "createdAt": now(), "expiresAt": time.time() + 600,
            }
            if not preview["providerQuote"]["orderToken"] or preview["providerQuote"]["deliveryFee"] is None:
                raise BankError(502, "MEITUAN_INVALID_RESPONSE", "美团询价结果缺少下单凭证，请重新询价。")
            db.execute("INSERT INTO shopping_official_previews VALUES(?,?,?)",
                       (preview["id"], user_id, json.dumps(preview, ensure_ascii=False)))
            self.bank._audit(db, user_id, "ARGOS", "美团真实跑腿待确认",
                             f"代购物品：{detail}；商品预算上限 ¥{yuan(item_budget):,.2f}；官方配送费 ¥{yuan(delivery_fee):,.2f}；尚未下单或支付。")
            snapshot = self.bank._snapshot(db, user_id)
        public_preview = self._official_preview_public(preview, address)
        if address_source == "manual":
            public_preview["manualAddressToken"] = self._manual_token(user_id, recipient, preview["expiresAt"])
            public_preview["notice"] += " 临时地址仅保留在当前页面至本次确认结束，不写入 TROPOS 地址簿或数据库。"
        return {"preview": public_preview, "snapshot": snapshot}

    @staticmethod
    def _official_order_public(order, payment=None):
        result = {
            "id": order["id"], "provider": "meituan", "mode": "official_paotui", "synthetic": False,
            "purchaseDetail": order.get("purchaseDetail"), "maxItemBudget": order.get("maxItemBudget"),
            "deliveryFee": order.get("deliveryFee"), "feeDisplay": order.get("feeDisplay"),
            "estimateTime": order.get("estimateTime"), "status": order.get("status"),
            "statusText": order.get("statusText", "等待用户完成美团支付"),
            "auditStatusText": order.get("auditStatusText"), "createdAt": order.get("createdAt"),
            "paymentRequired": order.get("status") in {"awaiting_payment", "payment_link_error"},
        }
        if payment:
            result["payment"] = payment
        return result

    def paotui_confirm(self, user_id, preview_id, payload):
        self.bank._confirm(payload)
        if not isinstance(payload, dict) or set(payload) - {"confirmation", "confirmationToken", "requestKey", "highAmountConfirmation", "manualAddressToken"}:
            raise BankError(400, "INVALID_CONFIRMATION", "跑腿订单确认格式不正确。")
        request_key = payload.get("requestKey")
        if not isinstance(request_key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_key):
            raise BankError(400, "INVALID_KEY", "请提供有效的下单请求凭证。")
        with self.bank._db(True) as db:
            self.bank._consent(db, user_id)
            self._binding(db, user_id, required=True)
            row = db.execute("SELECT content FROM shopping_official_previews WHERE id=? AND user_id=?", (preview_id, user_id)).fetchone()
            if not row:
                raise BankError(404, "PAOTUI_PREVIEW_NOT_FOUND", "跑腿询价不存在或不属于当前账户。")
            preview = json.loads(row[0])
            existing = db.execute("SELECT content FROM shopping_official_orders WHERE user_id=? AND json_extract(content,'$.previewId')=?",
                                  (user_id, preview_id)).fetchone()
            if existing:
                order = json.loads(existing[0])
                if order.get("status") in {"submitting", "unknown"}:
                    raise BankError(409, "MEITUAN_ORDER_UNKNOWN", "这笔订单的提交结果尚未明确，请查询状态，不要重复下单。")
                return {"order": self._official_order_public(order), "snapshot": self.bank._snapshot(db, user_id)}
            used = db.execute("SELECT content FROM shopping_official_orders WHERE user_id=? AND request_key=?",
                              (user_id, request_key)).fetchone()
            if used:
                raise BankError(409, "KEY_REUSED", "此下单凭证已用于另一笔真实跑腿订单。")
            if payload.get("confirmationToken") != preview.get("confirmationToken"):
                raise BankError(403, "INVALID_APPROVAL", "确认凭证与本次跑腿询价不匹配。")
            user, _ = self.bank._user(db, user_id)
            if preview["revision"] != user["revision"]:
                raise BankError(409, "STALE_APPROVAL", "账户或画像已变化，请重新询价后确认。")
            if preview["expiresAt"] < time.time():
                raise BankError(409, "APPROVAL_EXPIRED", "跑腿价格已经过期，请重新询价。")
            if preview["deliveryFeeCents"] > 10_000 and payload.get("highAmountConfirmation") is not True:
                raise BankError(400, "HIGH_AMOUNT_CONFIRMATION_REQUIRED", "配送费超过 ¥100，请再次明确确认高额配送费。")
            checks = self._official_checks(db, user_id, preview["maxItemBudgetCents"], preview["deliveryFeeCents"])
            if not all(row["ok"] for row in checks):
                raise BankError(409, "PURCHASE_BLOCKED", "当前预算或现金条件未满足，未创建真实订单。", {"checks": checks})
            placeholder = {"id": uid("paotui_order"), "previewId": preview_id, "requestKey": request_key,
                           "status": "submitting", "createdAt": now()}
            db.execute("INSERT INTO shopping_official_orders VALUES(?,?,?,?)",
                       (placeholder["id"], user_id, request_key, json.dumps(placeholder, ensure_ascii=False)))
        try:
            if preview.get("addressSource") == "manual":
                recipient = self._recipient_from_manual_token(
                    user_id, payload.get("manualAddressToken"), preview["recipientFingerprint"])
            else:
                recipient, _ = self._official_recipient(user_id, preview["addressId"])
            if self._recipient_fingerprint(recipient) != preview["recipientFingerprint"]:
                raise BankError(409, "MEITUAN_ADDRESS_CHANGED", "美团地址或联系方式已变化，请重新询价并确认。")
            provider_order = self.official.runner.submit_buy(
                user_id, recipient, preview["providerPurchaseDetail"], preview["providerQuote"], preview.get("remark", ""))
        except BankError as error:
            with self.bank._db(True) as db:
                if error.code in {"MEITUAN_TIMEOUT", "MEITUAN_ORDER_UNKNOWN"}:
                    placeholder["status"] = "unknown"
                    db.execute("UPDATE shopping_official_orders SET content=? WHERE id=?",
                               (json.dumps(placeholder, ensure_ascii=False), placeholder["id"]))
                else:
                    db.execute("DELETE FROM shopping_official_orders WHERE id=?", (placeholder["id"],))
            raise
        order = {**placeholder, "providerOrder": provider_order, "status": "awaiting_payment",
                 "purchaseDetail": preview["purchaseDetail"], "maxItemBudget": preview["maxItemBudget"],
                 "deliveryFee": preview["deliveryFee"], "feeDisplay": preview["feeDisplay"],
                 "estimateTime": preview.get("estimateTime"), "statusText": "等待用户完成美团支付"}
        payment = None
        try:
            payment = self.official.runner.pay_url(user_id, provider_order)
        except BankError:
            order["status"] = "payment_link_error"
            order["statusText"] = "订单已创建，收银台链接暂未生成"
        with self.bank._db(True) as db:
            db.execute("UPDATE shopping_official_orders SET content=? WHERE id=?",
                       (json.dumps(order, ensure_ascii=False), order["id"]))
            preview["status"] = "ordered"
            preview.pop("confirmationToken", None)
            db.execute("UPDATE shopping_official_previews SET content=? WHERE id=?",
                       (json.dumps(preview, ensure_ascii=False), preview_id))
            self.bank._audit(db, user_id, "KAIROS", "美团真实跑腿订单已创建",
                             f"代购物品：{preview['purchaseDetail']}；真实支付仍待用户在美团收银台完成；未修改模拟银行账本。")
            snapshot = self.bank._snapshot(db, user_id)
        return {"order": self._official_order_public(order, payment), "snapshot": snapshot}

    def paotui_orders(self, user_id):
        with self.bank._db() as db:
            self.bank._user(db, user_id)
            rows = db.execute("SELECT content FROM shopping_official_orders WHERE user_id=? ORDER BY rowid DESC LIMIT 30", (user_id,)).fetchall()
            orders = [self._official_order_public(json.loads(row[0])) for row in rows if json.loads(row[0]).get("status") != "submitting"]
            snapshot = self.bank._snapshot(db, user_id)
        return {"orders": orders, "status": self.paotui_status(user_id), "snapshot": snapshot}

    def _official_order(self, db, user_id, order_id):
        row = db.execute("SELECT content FROM shopping_official_orders WHERE id=? AND user_id=?", (order_id, user_id)).fetchone()
        if not row:
            raise BankError(404, "PAOTUI_ORDER_NOT_FOUND", "真实跑腿订单不存在或不属于当前账户。")
        return json.loads(row[0])

    def paotui_payment(self, user_id, order_id):
        with self.bank._db() as db:
            self._binding(db, user_id, required=True)
            order = self._official_order(db, user_id, order_id)
        if not order.get("providerOrder"):
            raise BankError(409, "MEITUAN_ORDER_UNKNOWN", "订单结果尚未明确，不能生成支付链接。")
        payment = self.official.runner.pay_url(user_id, order["providerOrder"])
        return {"order": self._official_order_public(order, payment)}

    def paotui_order_status(self, user_id, order_id):
        with self.bank._db() as db:
            self._binding(db, user_id, required=True)
            order = self._official_order(db, user_id, order_id)
        provider = order.get("providerOrder") or {}
        if not provider.get("orderId"):
            raise BankError(409, "MEITUAN_ORDER_UNKNOWN", "订单结果尚未明确，暂时无法查询状态。")
        status = self.official.runner.order_status(user_id, provider["orderId"])
        order_status = status.get("orderStatus") if isinstance(status.get("orderStatus"), dict) else {}
        audit_status = status.get("auditStatus") if isinstance(status.get("auditStatus"), dict) else {}
        order["status"] = "provider_status"
        order["statusText"] = str(order_status.get("text") or "美团已返回订单状态")[:100]
        order["auditStatusText"] = str(audit_status.get("text") or "")[:100] or None
        order["statusUpdatedAt"] = now()
        with self.bank._db(True) as db:
            db.execute("UPDATE shopping_official_orders SET content=? WHERE id=?",
                       (json.dumps(order, ensure_ascii=False), order_id))
            self.bank._audit(db, user_id, "KAIROS", "已查询美团跑腿状态", "仅记录美团接口明确返回的状态文本，不推测支付或履约结果。")
        return {"order": self._official_order_public(order)}

    def orders(self, user_id):
        with self.bank._db() as db:
            self.bank._user(db, user_id)
            rows = db.execute("SELECT content FROM shopping_orders WHERE user_id=? ORDER BY rowid DESC LIMIT 50", (user_id,)).fetchall()
            return {"orders": [json.loads(row[0]) for row in rows], "snapshot": self.bank._snapshot(db, user_id)}

    def order(self, user_id, order_id):
        with self.bank._db() as db:
            self.bank._user(db, user_id)
            row = db.execute("SELECT content FROM shopping_orders WHERE id=? AND user_id=?", (order_id, user_id)).fetchone()
            if not row:
                raise BankError(404, "ORDER_NOT_FOUND", "订单不存在或不属于当前账户。")
            return {"order": json.loads(row[0]), "snapshot": self.bank._snapshot(db, user_id)}

    def advance(self, user_id, order_id, payload):
        self.bank._confirm(payload)
        with self.bank._db(True) as db:
            row = db.execute("SELECT content FROM shopping_orders WHERE id=? AND user_id=?", (order_id, user_id)).fetchone()
            if not row:
                raise BankError(404, "ORDER_NOT_FOUND", "订单不存在或不属于当前账户。")
            order = json.loads(row[0])
            try:
                index = self.ORDER_STATES.index(order["status"])
            except ValueError:
                raise BankError(409, "ORDER_NOT_ADVANCEABLE", "当前订单状态不能继续推进。") from None
            if index < len(self.ORDER_STATES) - 1:
                status = self.ORDER_STATES[index + 1]
                labels = {"merchant_confirmed": "模拟商家已接单", "preparing": "模拟商家备餐中",
                          "delivering": "模拟骑手配送中", "delivered": "模拟订单已送达"}
                order["status"] = status
                order["timeline"].append({"status": status, "label": labels[status], "at": now()})
                db.execute("UPDATE shopping_orders SET content=? WHERE id=?", (json.dumps(order, ensure_ascii=False), order_id))
                self.bank._audit(db, user_id, "KAIROS", labels[status], f"订单 {order_id}；仅推进 Mock 履约状态。")
            return {"order": order, "snapshot": self.bank._snapshot(db, user_id)}
