"""Server-side adapter for Meituan's official MT-Paotui-For-Client Skill.

The adapter deliberately keeps the provider token out of HTTP responses, logs,
SQLite, and model context.  The official pt-passport helper owns its token cache;
TROPOS only asks for a token in-memory when invoking the official Node CLI.
"""
from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from urllib.parse import urlsplit

from .service import BankError


CLIENT_ID = "ac76c22d257c4c6d9164719eea64ed4b"


def _last_json(text):
    """Extract the last JSON value from a CLI response containing progress text."""
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    for line in reversed(lines):
        try:
            return json.loads(line)
        except (TypeError, ValueError):
            pass
    raw = "\n".join(lines)
    for start in (raw.find("{"), raw.find("[")):
        if start < 0:
            continue
        for end_char in ("}", "]"):
            end = raw.rfind(end_char)
            if end > start:
                try:
                    return json.loads(raw[start:end + 1])
                except ValueError:
                    pass
    return None


def _find_mapping(value, key):
    if isinstance(value, dict):
        if key in value:
            return value
        for child in value.values():
            found = _find_mapping(child, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_mapping(child, key)
            if found is not None:
                return found
    return None


def _find_list(value, keys):
    """Find an explicitly named list without mistaking an error object for data."""
    if isinstance(value, dict):
        for key in keys:
            if isinstance(value.get(key), list):
                return value[key]
        for child in value.values():
            found = _find_list(child, keys)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_list(child, keys)
            if found is not None:
                return found
    return None


def _safe_provider_url(value, *, auth=False):
    if not isinstance(value, str) or len(value) > 4096:
        raise BankError(502, "MEITUAN_INVALID_RESPONSE", "美团返回的链接格式不正确。")
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    allowed = host == "dpurl.cn" if auth else (host == "meituan.com" or host.endswith(".meituan.com"))
    if parsed.scheme != "https" or not allowed or parsed.username or parsed.password:
        raise BankError(502, "MEITUAN_INVALID_RESPONSE", "美团返回的链接未通过安全检查。")
    return value


class MeituanPaotuiRunner:
    """Invokes the official Skill through argument arrays, never a shell."""

    def __init__(self, project_root=None, skill_dir=None, node_bin=None, passport_js=None):
        root = Path(project_root or Path(__file__).resolve().parent.parent)
        self.root = root
        self.skill_dir = Path(skill_dir or os.getenv("TROPOS_MEITUAN_SKILL_DIR", "") or
                              Path.home() / ".codex" / "skills" / "meituan-paotui")
        bundled_node = (Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" /
                        "dependencies" / "node" / "bin" / "node.exe")
        configured_node = os.getenv("TROPOS_MEITUAN_NODE_BIN", "").strip()
        self.node_bin = Path(node_bin or configured_node or (bundled_node if bundled_node.is_file() else shutil.which("node") or ""))
        default_passport = root / ".local" / "meituan-runtime" / "package" / "dist" / "index.js"
        self.passport_js = Path(passport_js or os.getenv("TROPOS_MEITUAN_PASSPORT_JS", "") or default_passport)
        self.paotui_js = self.skill_dir / "paotui.js"
        configured_state = os.getenv("TROPOS_MEITUAN_STATE_DIR", "").strip()
        self.state_dir = Path(configured_state) if configured_state else root / ".local" / "meituan-runtime"
        self.temp_dir = self.state_dir / "tmp"
        self.account_dir = self.state_dir / "accounts"
        self._locks_guard = threading.Lock()
        self._account_locks = {}

    @property
    def available(self):
        return all(self.readiness.values())

    @property
    def readiness(self):
        """Return public component readiness without exposing server paths."""
        storage = self.state_dir.is_dir() and os.access(self.state_dir, os.W_OK | os.X_OK)
        return {
            "node": self.node_bin.is_file(),
            "skill": self.paotui_js.is_file(),
            "passport": self.passport_js.is_file(),
            "storage": bool(storage),
        }

    @staticmethod
    def _scope_id(credential_id):
        if not isinstance(credential_id, str) or not 1 <= len(credential_id) <= 256:
            raise BankError(400, "INVALID_MEITUAN_ACCOUNT", "美团授权账户标识不正确。")
        return hashlib.sha256(credential_id.encode("utf-8")).hexdigest()

    def _account_paths(self, credential_id):
        scope = self._scope_id(credential_id)
        profile = self.account_dir / scope
        return scope, profile, self.temp_dir / scope

    def _account_lock(self, credential_id):
        scope = self._scope_id(credential_id)
        with self._locks_guard:
            return self._account_locks.setdefault(scope, threading.RLock())

    def _ensure(self, credential_id):
        if not self.available:
            raise BankError(503, "MEITUAN_SKILL_NOT_INSTALLED",
                            "美团官方跑腿 Skill 或其授权工具尚未安装完整。")
        _scope, profile, temp = self._account_paths(credential_id)
        paths = (profile, temp, profile / "AppData" / "Roaming", profile / "AppData" / "Local",
                 profile / ".config")
        try:
            for path in paths:
                if os.name == "nt":
                    path.mkdir(parents=True, exist_ok=True)
                else:
                    path.mkdir(parents=True, exist_ok=True, mode=0o700)
            # Python 3.13 on Windows can translate chmod(0700) into a DACL that
            # prevents the same non-elevated process from creating child paths.
            # Windows inherits the private project directory ACL; Unix needs an
            # explicit owner-only mode for provider credentials.
            if os.name != "nt":
                for path in paths:
                    path.chmod(0o700)
        except OSError:
            raise BankError(503, "MEITUAN_STORAGE_UNAVAILABLE",
                            "无法初始化当前用户的美团独立授权目录，请检查服务端数据目录权限。") from None
        return profile, temp

    def _run(self, program, args, *, credential_id, token=None, timeout=45):
        profile, temp = self._ensure(credential_id)
        env = os.environ.copy()
        env["TEMP"] = env["TMP"] = str(temp)
        # pt-passport stores its token below the OS home directory. Giving each
        # authenticated TROPOS user a private child-process home prevents one
        # user's cached authorization from being reused by another user.
        env["USERPROFILE"] = env["HOME"] = str(profile)
        env["APPDATA"] = str(profile / "AppData" / "Roaming")
        env["LOCALAPPDATA"] = str(profile / "AppData" / "Local")
        env["XDG_CONFIG_HOME"] = str(profile / ".config")
        if token:
            env["MCP_ACCESS_TOKEN"] = token
        else:
            env.pop("MCP_ACCESS_TOKEN", None)
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            process = subprocess.run(
                [str(self.node_bin), str(program), *[str(item) for item in args]],
                cwd=str(self.skill_dir), env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout,
                creationflags=flags, check=False,
            )
        except subprocess.TimeoutExpired:
            raise BankError(504, "MEITUAN_TIMEOUT",
                            "美团请求超时。若刚才正在提交订单，请先查询状态，不要重复下单。") from None
        except OSError:
            raise BankError(503, "MEITUAN_SKILL_UNAVAILABLE", "美团官方跑腿程序暂时无法启动。") from None
        output = (process.stdout or "").strip()
        error_output = (process.stderr or "").strip()
        parsed = _last_json(output) or _last_json(error_output)
        operation = str(args[0]) if args else ""
        if process.returncode != 0:
            code = parsed.get("code") if isinstance(parsed, dict) else None
            message = parsed.get("message") if isinstance(parsed, dict) else None
            self._raise_cli(code, message, operation=operation)
        if isinstance(parsed, dict):
            # The official component can report a provider/business failure as
            # JSON while still exiting with code 0.  Treating that object as a
            # successful payload made get_address_list look like an empty
            # address book.  Detect the documented/common failure shapes before
            # any caller tries to interpret provider data.
            status = str(parsed.get("status") or "").strip().lower()
            success = parsed.get("success")
            failed = status in {"error", "failed", "failure"} or success is False
            error = parsed.get("error")
            if isinstance(error, dict):
                failed = True
                code = error.get("code") or parsed.get("code")
                message = error.get("message") or parsed.get("message")
            else:
                code = parsed.get("code")
                message = parsed.get("message") or (error if isinstance(error, str) else None)
                failed = failed or bool(error)
            if failed:
                self._raise_cli(code, message, operation=operation)
        return parsed if parsed is not None else output

    @staticmethod
    def _raise_cli(code, message=None, *, operation=""):
        raw = str(code or "")
        provider_message = str(message or "").strip().lower()
        if raw == "1003":
            raise BankError(504, "MEITUAN_AUTH_TIMEOUT", "美团授权入口已过期，请重新连接。")
        if raw == "1004":
            raise BankError(409, "MEITUAN_AUTH_SESSION_MISSING", "美团授权会话不存在，请重新连接。")
        if raw == "101368":
            raise BankError(409, "MEITUAN_AUTH_CANCELLED", "你已在美团取消本次授权，可以重新连接。")
        if raw in {"101144", "101267", "101269"}:
            raise BankError(409, "MEITUAN_AUTH_REJECTED", "美团未能完成本次授权，请在美团 App 中确认后重试。")
        if raw in {"AUTH_REQUIRED", "AUTH_FAILED", "10000", "401"} or \
                "unauthorized" in provider_message or re.search(r"\b401\b", provider_message):
            raise BankError(401, "MEITUAN_AUTH_REQUIRED", "美团授权已失效，请重新连接美团账号。")
        messages = {
            "10001": "地址或联系方式不完整，请重新选择地址。",
            "10002": "物品信息格式不正确，请修改后重试。",
            "10003": "美团服务暂时繁忙，请稍后重试。",
            "10309": "配送费已经变化，请重新询价并确认。",
            "10310": "当前美团账号受到下单限制，请在美团 App 中查看。",
            "10311": "本次询价已经过期，请重新询价。",
            "10112": "购买地址所在区域暂未开通跑腿服务。",
            "10113": "地址所在城市暂未开通跑腿服务。",
            "10114": "当前地址因临时交通原因无法配送。",
            "10115": "配送距离超出跑腿服务范围。",
            "10116": "地址不够精确，请补充街道、楼号或门牌号。",
            "11000": "订单内容不符合平台规范，请修改后重试。",
            "11003": "商品信息不符合平台规范，请修改后重试。",
            "11004": "订单备注不符合平台规范，请修改后重试。",
            "11016": "物品描述不符合平台规范，请修改后重试。",
            "40001": "美团跑腿不支持代购药品或医疗器械。",
            "10401": "配送费计算失败，请重新询价。",
        }
        # Provider messages can echo addresses, phone numbers or internal details.
        # Only expose our reviewed code-to-message mapping to the browser.
        public = messages.get(raw)
        if operation == "get_address_list" and not public:
            raise BankError(
                502,
                "MEITUAN_ADDRESS_SERVICE_ERROR",
                "美团地址接口暂时未返回可用数据。这不代表你的地址簿为空，请稍后刷新；若持续失败可重新授权。",
                {"providerCode": raw, "operation": operation, "retryable": True},
            )
        raise BankError(502, "MEITUAN_PROVIDER_ERROR", public or "美团跑腿请求未完成，请稍后重试。",
                        {"providerCode": raw} if raw else None)

    def _passport(self, *args, credential_id, timeout=45):
        return self._run(self.passport_js, args, credential_id=credential_id, timeout=timeout)

    def _paotui(self, *args, credential_id, token=None, timeout=45):
        return self._run(self.paotui_js, args, credential_id=credential_id, token=token, timeout=timeout)

    def auth_start(self, credential_id):
        with self._account_lock(credential_id):
            return self._auth_start(credential_id)

    def _auth_start(self, credential_id):
        result = self._passport("auth", "get-code", "--client_id", CLIENT_ID, credential_id=credential_id)
        text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        match = re.search(r"(?:^|\n)AUTH_LINK:\s*(https://\S+)", text)
        if match:
            return {"authUrl": _safe_provider_url(match.group(1), auth=True), "expiresInSeconds": 600}

        # pt-passport intentionally returns a cached Token instead of AUTH_LINK
        # when the existing authorization is still valid. Never parse, persist or
        # return that Token here; retrieve it through the normal helper and pass it
        # only to the official Skill process environment.
        cached_token = re.search(r"(?:^|\n)Token:\s*\S+", text)
        if cached_token or (isinstance(result, dict) and
                            isinstance(result.get("token") or result.get("accessToken"), str)):
            self._paotui("login", token=self.token(credential_id), credential_id=credential_id)
            return {"alreadyConnected": True}

        raise BankError(502, "MEITUAN_AUTH_LINK_MISSING", "暂时无法生成美团授权链接。")

    def auth_complete(self, credential_id):
        with self._account_lock(credential_id):
            self._passport("auth", "poll-token", "--client_id", CLIENT_ID,
                           "--timeout", "600", "--interval", "3",
                           credential_id=credential_id, timeout=630)
            token = self.token(credential_id)
            self._paotui("login", token=token, credential_id=credential_id)
            return True

    def token(self, credential_id):
        result = self._passport("get-token", "--client_id", CLIENT_ID, credential_id=credential_id)
        if isinstance(result, dict):
            candidate = result.get("token") or result.get("accessToken")
        else:
            lines = [line.strip() for line in str(result).splitlines() if line.strip()]
            candidate = lines[-1] if lines else ""
            candidate = re.sub(r"^Token:\s*", "", candidate, flags=re.I)
        if not isinstance(candidate, str) or len(candidate) < 20 or any(ch.isspace() for ch in candidate):
            raise BankError(401, "MEITUAN_AUTH_REQUIRED", "请先连接美团账号。")
        return candidate

    def logout(self, credential_id):
        with self._account_lock(credential_id):
            token = None
            try:
                token = self.token(credential_id)
            except BankError:
                pass
            try:
                self._paotui("logout", token=token, credential_id=credential_id)
            except BankError:
                pass
            try:
                self._passport("logout", "--client_id", CLIENT_ID, credential_id=credential_id)
            except BankError:
                pass

    def addresses(self, credential_id):
        data = self._paotui("get_address_list", token=self.token(credential_id), credential_id=credential_id)
        if isinstance(data, dict):
            result = _find_list(data, ("addresses", "addressList", "list"))
            if result is not None:
                return result
            # A non-error response that has no address-list field is also not a
            # trustworthy empty result.  Keep the UI from telling the user to
            # edit their address book when the provider schema changed.
            raise BankError(502, "MEITUAN_ADDRESS_INVALID_RESPONSE",
                            "美团地址接口返回了无法识别的数据，请稍后刷新。",
                            {"operation": "get_address_list", "retryable": True})
        if isinstance(data, list):
            return data
        raise BankError(502, "MEITUAN_ADDRESS_INVALID_RESPONSE",
                        "美团地址接口没有返回地址列表，请稍后刷新。",
                        {"operation": "get_address_list", "retryable": True})

    def search_poi(self, credential_id, keyword, city):
        """Resolve a user-entered delivery address with the official POI search.

        The provider has used a few wrapper names across releases, so accept the
        documented list as well as common nested POI list keys.  Never echo the
        provider payload because it can contain precise location information.
        """
        data = self._paotui(
            "search_poi", "--keyword", keyword, "--city", city,
            token=self.token(credential_id), credential_id=credential_id,
        )
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            result = _find_list(data, ("pois", "poiList", "poi_list", "items", "list"))
            if result is not None:
                return result
            single = _find_mapping(data, "lat") or _find_mapping(data, "latitude")
            if single is not None:
                return [single]
        raise BankError(
            502, "MEITUAN_POI_INVALID_RESPONSE",
            "美团没有返回可用于定位该地址的数据，请补充街道、楼号或小区名称后重试。",
            {"operation": "search_poi", "retryable": True},
        )

    def address_info(self, credential_id, address_id):
        data = self._paotui("get_address_info", "--address-id", address_id,
                            token=self.token(credential_id), credential_id=credential_id)
        found = _find_mapping(data, "phone")
        if found is None or "****" in str(found.get("phone", "")):
            raise BankError(502, "MEITUAN_PHONE_UNAVAILABLE", "该地址没有可用于下单的完整联系方式。")
        return found

    def preview_buy(self, credential_id, recipient, purchase_detail):
        address = json.dumps(recipient, ensure_ascii=False, separators=(",", ":"))
        goods = json.dumps({"goodsName": purchase_detail}, ensure_ascii=False, separators=(",", ":"))
        data = self._paotui(
            "delivery_preview", "--sender", address, "--recipient", address, "--goods", goods,
            "--business-type", "2", "--business-type-tag", "1",
            "--purchase-detail", purchase_detail, token=self.token(credential_id),
            credential_id=credential_id, timeout=60,
        )
        quote = _find_mapping(data, "orderToken")
        if quote is None:
            raise BankError(502, "MEITUAN_INVALID_RESPONSE", "美团没有返回有效的跑腿询价结果。")
        return quote

    def submit_buy(self, credential_id, recipient, purchase_detail, quote, remark=""):
        address = json.dumps(recipient, ensure_ascii=False, separators=(",", ":"))
        goods = json.dumps({"goodsName": purchase_detail}, ensure_ascii=False, separators=(",", ":"))
        args = [
            "submit_order", "--order-token", quote["orderToken"],
            "--sender", address, "--recipient", address, "--goods", goods,
            "--delivery-fee", quote["deliveryFee"], "--preview-total", quote["previewTotal"],
            "--business-type", "2", "--business-type-tag", "1",
            "--purchase-detail", purchase_detail,
        ]
        if quote.get("couponViewId"):
            args += ["--coupon-view-id", quote["couponViewId"]]
        if remark:
            args += ["--remark", remark]
        data = self._paotui(*args, token=self.token(credential_id), credential_id=credential_id, timeout=60)
        order = _find_mapping(data, "orderId")
        if order is None:
            raise BankError(502, "MEITUAN_ORDER_UNKNOWN",
                            "美团没有返回明确的订单结果，请先查询状态，不要重复下单。")
        return order

    def pay_url(self, credential_id, order):
        data = self._paotui(
            "get_pay_url", "--trade-no", order["tradeNo"], "--pay-token", order["payToken"],
            "--order-id", order["orderId"], token=self.token(credential_id), credential_id=credential_id,
        )
        links = _find_mapping(data, "mobilePayUrl")
        if links is None or not links.get("pcPayUrl"):
            raise BankError(502, "MEITUAN_PAYMENT_LINK_MISSING", "订单已创建，但暂时无法生成美团收银台链接。")
        return {"mobilePayUrl": _safe_provider_url(links["mobilePayUrl"]),
                "pcPayUrl": _safe_provider_url(links["pcPayUrl"]),
                "loginUrl": "https://passport.meituan.com/useraccount/ilogin"}

    def order_status(self, credential_id, order_id):
        data = self._paotui("get_order_status", "--order-id", order_id,
                            token=self.token(credential_id), credential_id=credential_id)
        order = _find_mapping(data, "orderStatus")
        if order is None:
            raise BankError(502, "MEITUAN_INVALID_RESPONSE", "美团没有返回有效的订单状态。")
        return order
