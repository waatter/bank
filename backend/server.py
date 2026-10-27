"""HTTP gateway for the synthetic TROPOS banking sandbox.

Loopback is the default. LAN mode must be explicitly configured with either a
separate access token or an open-demo flag.
"""
from __future__ import annotations

import argparse
import hmac
import ipaddress
import json
import re
import secrets
import socket
import sys
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .model import IntentPlanner, ModelConfig, agent_response, strict_json
from .agent_ops import AgentOps
from .life_service import LifeService
from .shopping import ShoppingService


ROOT = Path(__file__).resolve().parent.parent
APP_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
MAX_BODY = 65536
STATIC_FILES = {
    "/": "index.html", "/index.html": "index.html", "/demo.html": "demo.html",
    "/assets/demo.js": "assets/demo.js", "/assets/demo.css": "assets/demo.css",
    "/assets/profile-engine.js": "assets/profile-engine.js",
    "/assets/profile-ui.js": "assets/profile-ui.js", "/assets/profile-ui.css": "assets/profile-ui.css",
    "/assets/backend-client.js": "assets/backend-client.js",
    "/assets/life-ui.js": "assets/life-ui.js", "/assets/life-ui.css": "assets/life-ui.css",
    "/assets/action-ui.js": "assets/action-ui.js", "/assets/action-ui.css": "assets/action-ui.css",
    "/assets/shopping-ui.js": "assets/shopping-ui.js", "/assets/shopping-ui.css": "assets/shopping-ui.css",
    "/assets/mission-ui.js": "assets/mission-ui.js", "/assets/mission-ui.css": "assets/mission-ui.css",
    "/assets/guard-ui.js": "assets/guard-ui.js", "/assets/director.js": "assets/director.js",
}
IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class HttpError(Exception):
    def __init__(self, status, code, message, details=None):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


def _object(body, allowed, required=()):
    if not isinstance(body, dict):
        raise HttpError(400, "INVALID_JSON", "请求正文必须是 JSON 对象。")
    if set(body) - set(allowed):
        raise HttpError(400, "UNKNOWN_FIELD", "请求包含不支持的字段。")
    if set(required) - set(body):
        raise HttpError(400, "MISSING_FIELD", "请求缺少必要字段。", {"required": list(required)})
    return body


def _confirmed(body):
    if body.get("confirmation") is not True:
        raise HttpError(400, "CONFIRMATION_REQUIRED", "请单独确认这项操作。")


def _identifier(text):
    if not isinstance(text, str) or not IDENTIFIER.fullmatch(text):
        raise HttpError(400, "INVALID_IDENTIFIER", "凭证或条目标识格式不正确。")
    return text


class TroposServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, service, planner=None, root=ROOT, *, access_token=None,
                 allowed_hosts=(), allow_unauthenticated_lan=False):
        host = address[0]
        try:
            loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = False
        if access_token is not None and (not isinstance(access_token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{24,128}", access_token)):
            raise ValueError("LAN access token must be 24-128 URL-safe characters")
        if not isinstance(allow_unauthenticated_lan, bool):
            raise ValueError("LAN open-demo flag must be boolean")
        if access_token and allow_unauthenticated_lan:
            raise ValueError("LAN mode cannot use both an access token and open-demo mode")
        if not loopback and not (host == "0.0.0.0" and (access_token or allow_unauthenticated_lan)):
            raise ValueError("Non-loopback binding requires an explicit LAN access mode")
        validated_hosts = {"127.0.0.1", "localhost", "[::1]"}
        for item in allowed_hosts:
            try:
                address_value = ipaddress.ip_address(item)
            except ValueError:
                raise ValueError("LAN allowed hosts must be literal IP addresses") from None
            if not (address_value.is_private or address_value.is_loopback):
                raise ValueError("LAN allowed hosts must be private or loopback addresses")
            validated_hosts.add(f"[{item}]" if address_value.version == 6 else item)
        if not loopback and len(validated_hosts) == 3:
            raise ValueError("LAN mode requires at least one allowed private address")
        self.access_token = access_token
        self.allow_unauthenticated_lan = allow_unauthenticated_lan
        self.allowed_hosts = frozenset(validated_hosts)
        self.service = service
        self.life = LifeService(service)
        self.shopping = ShoppingService(service)
        self.ops = AgentOps(service)
        self.planner = planner if planner is not None else IntentPlanner()
        self.root = Path(root).resolve()
        if ":" in host:
            self.address_family = socket.AF_INET6
        super().__init__(address, TroposHandler)


class TroposHandler(BaseHTTPRequestHandler):
    server_version = "TROPOS-Sandbox/1.0"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        # Official Paotui preview/submit calls can legitimately take close to a minute.
        self.connection.settimeout(75)

    def log_message(self, format, *args):
        # Request paths can contain transaction keys; log neither paths nor credentials.
        pass

    def _headers(self, status, content_type, length, nonce=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        # Existing prototype uses inline SVG/HTML and fonts; credentials stay server-side.
        script = "script-src 'self'" + (" 'nonce-" + nonce + "'" if nonce else "")
        self.send_header("Content-Security-Policy", "default-src 'self'; " + script + "; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; font-src 'self' https://cdn.jsdelivr.net data:; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        self.end_headers()

    def _json(self, status, value):
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(raw))
        if self.command != "HEAD":
            self.wfile.write(raw)

    def _error(self, error):
        payload = {"code": error.code, "message": error.message}
        if getattr(error, "details", None) is not None:
            payload["details"] = error.details
        self.close_connection = True
        self._json(error.status, {"error": payload})

    def send_error(self, code, message=None, explain=None):
        # Also covers parser-level unsupported methods and oversized headers.
        self._error(HttpError(code, "HTTP_ERROR", "HTTP 请求格式或方法不受支持。"))

    def _guard(self):
        hosts = self.headers.get_all("Host", [])
        port = self.server.server_port
        allowed = {f"{host}:{port}" for host in self.server.allowed_hosts}
        if port == 80:
            allowed |= set(self.server.allowed_hosts)
        if len(hosts) != 1 or hosts[0].lower() not in allowed:
            raise HttpError(403, "HOST_FORBIDDEN", "此地址未获准访问演示服务。")
        self._loopback_request = hosts[0].lower() in {
            f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}",
            *( {"127.0.0.1", "localhost", "[::1]"} if port == 80 else set() ),
        }
        origins = self.headers.get_all("Origin", [])
        if len(origins) > 1 or (origins and origins[0].lower() != "http://" + hosts[0].lower()):
            raise HttpError(403, "ORIGIN_FORBIDDEN", "仅允许同源页面访问演示服务。")
        if self.headers.get("Sec-Fetch-Site", "").lower() == "cross-site":
            raise HttpError(403, "ORIGIN_FORBIDDEN", "拒绝跨站请求。")
        try:
            parsed = urllib.parse.urlsplit(self.path)
        except ValueError:
            raise HttpError(400, "INVALID_PATH", "请求路径格式不正确。") from None
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or not parsed.path.startswith("/"):
            raise HttpError(400, "INVALID_PATH", "请求路径格式不正确。")
        if re.search(r"%(?![0-9a-fA-F]{2})", parsed.path):
            raise HttpError(400, "INVALID_PATH", "请求路径格式不正确。")
        try:
            path = urllib.parse.unquote(parsed.path, errors="strict")
        except UnicodeError:
            raise HttpError(400, "INVALID_PATH", "请求路径编码不正确。") from None
        if "\\" in path or "\x00" in path or any(part in {".", ".."} for part in path.split("/")):
            raise HttpError(400, "INVALID_PATH", "不允许目录路径。")
        return path

    def _body(self):
        if self.headers.get("Transfer-Encoding"):
            raise HttpError(400, "INVALID_BODY", "不支持分块请求正文。")
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,8}", lengths[0]):
            raise HttpError(411, "LENGTH_REQUIRED", "需要有效的请求正文长度。")
        length = int(lengths[0])
        if length > MAX_BODY:
            raise HttpError(413, "BODY_TOO_LARGE", "请求正文过大。")
        if self.headers.get_content_type() != "application/json":
            raise HttpError(415, "JSON_REQUIRED", "请使用 application/json。")
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise HttpError(400, "INVALID_BODY", "请求正文不完整。")
        try:
            value = strict_json(raw.decode("utf-8"))
        except (ValueError, UnicodeError, RecursionError):
            raise HttpError(400, "INVALID_JSON", "JSON 正文不合法。") from None
        if not isinstance(value, dict):
            raise HttpError(400, "INVALID_JSON", "请求正文必须是 JSON 对象。")
        return value

    def _user(self):
        headers = self.headers.get_all("Authorization", [])
        if len(headers) != 1 or not headers[0].startswith("Bearer ") or len(headers[0]) > 512:
            raise HttpError(401, "AUTH_REQUIRED", "请先选择模拟账户登录。")
        token = headers[0][7:]
        if not token or any(c.isspace() for c in token):
            raise HttpError(401, "AUTH_REQUIRED", "模拟登录已失效，请重新登录。")
        return self.server.service.authenticate(token)

    def _static(self, path):
        relative = STATIC_FILES.get(path)
        if not relative:
            raise HttpError(404, "NOT_FOUND", "页面不存在。")
        file = (self.server.root / relative).resolve()
        if not file.is_relative_to(self.server.root) or not file.is_file():
            raise HttpError(404, "NOT_FOUND", "页面不存在。")
        extension = file.suffix
        types = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}
        raw = file.read_bytes()
        nonce = secrets.token_urlsafe(20) if extension == ".html" else None
        if nonce:
            raw = raw.replace(b"<script>", ('<script nonce="' + nonce + '">').encode())
        self._headers(200, types[extension], len(raw), nonce=nonce)
        if self.command != "HEAD":
            self.wfile.write(raw)

    def _dispatch(self):
        path = self._guard()
        if self.server.access_token and not self._loopback_request:
            supplied = self.headers.get_all("X-Tropos-Access", [])
            if len(supplied) != 1 or not hmac.compare_digest(supplied[0], self.server.access_token):
                raise HttpError(401, "LAN_ACCESS_REQUIRED", "局域网访问口令无效，请使用本机生成的手机专用链接。")
        method = self.command
        service = self.server.service
        if not path.startswith("/api/"):
            if method not in {"GET", "HEAD"}:
                raise HttpError(405, "METHOD_NOT_ALLOWED", "此路径仅允许读取。")
            return self._static(path)
        if method == "GET" and path == "/api/health":
            return self._json(200, {"ok": True, "sandbox": True, "authentication": "simulated-persona-session",
                                    "version": APP_VERSION,
                                    "modelMode": self.server.planner.mode, "service": "TROPOS synthetic bank"})
        if method == "POST" and path == "/api/sessions":
            body = _object(self._body(), {"persona"}, {"persona"})
            if not isinstance(body["persona"], str) or body["persona"] not in {"A", "B", "C"}:
                raise HttpError(400, "INVALID_PERSONA", "请选择 A、B 或 C 模拟账户。")
            return self._json(201, service.session(body["persona"]))
        uid = self._user()
        body = self._body() if method in {"POST", "PUT"} else None
        if method == "GET" and path == "/api/life":
            return self._json(200, self.server.life.get(uid))
        if method == "POST" and path == "/api/life/settings":
            _object(body, {"version", "settings"}, {"version", "settings"})
            return self._json(200, self.server.life.save_settings(uid, body))
        if method == "POST" and path == "/api/life/scenes":
            _object(body, {"version", "scene"}, {"version", "scene"})
            return self._json(201, self.server.life.add_scene(uid, body))
        if method == "POST" and path == "/api/life/analyze":
            _object(body, set())
            return self._json(200, self.server.life.analyze(uid))
        if method == "POST" and path == "/api/life/goal":
            _object(body, {"version", "accountRevision", "amount"}, {"version", "accountRevision", "amount"})
            return self._json(201, self.server.life.propose_goal(uid, body))
        if method == "POST" and path == "/api/life/commit":
            _object(body, {"version", "accountRevision", "policyId", "confirmation", "requestKey"},
                    {"version", "accountRevision", "policyId", "confirmation", "requestKey"})
            _confirmed(body)
            return self._json(200, self.server.life.commit_plan(uid, body))
        life_match = re.fullmatch(r"/api/life/scenes/([^/]+)/(cancel|commit)", path)
        if method == "POST" and life_match:
            scene_id, action = _identifier(life_match[1]), life_match[2]
            fields = {"version", "confirmation", "accountRevision"} if action == "commit" else {"version", "confirmation"}
            _object(body, fields, fields)
            _confirmed(body)
            operation = self.server.life.commit_scene if action == "commit" else self.server.life.cancel_scene
            return self._json(200, operation(uid, scene_id, body))
        if method == "GET" and path == "/api/state":
            return self._json(200, service.snapshot(uid))
        if method == "GET" and path == "/api/insights":
            return self._json(200, service.insights(uid))
        if method == "GET" and path == "/api/shopping/providers":
            return self._json(200, self.server.shopping.providers(uid))
        if method == "GET" and path == "/api/shopping/paotui":
            return self._json(200, self.server.shopping.paotui_status(uid))
        if method == "POST" and path == "/api/shopping/paotui/auth/start":
            _object(body, set())
            return self._json(200, self.server.shopping.paotui_auth_start(uid))
        if method == "POST" and path == "/api/shopping/paotui/auth/complete":
            _object(body, set())
            return self._json(200, self.server.shopping.paotui_auth_complete(uid))
        if method == "POST" and path == "/api/shopping/paotui/logout":
            body = _object(body, {"confirmation"}, {"confirmation"})
            _confirmed(body)
            return self._json(200, self.server.shopping.paotui_logout(uid, body))
        if method == "GET" and path == "/api/shopping/paotui/addresses":
            return self._json(200, self.server.shopping.paotui_addresses(uid))
        if method == "POST" and path == "/api/shopping/paotui/preview":
            body = _object(body, {"purchaseDetail", "addressId", "maxItemBudget", "remark"},
                           {"purchaseDetail", "addressId", "maxItemBudget"})
            return self._json(201, self.server.shopping.paotui_preview(uid, body))
        if method == "GET" and path == "/api/shopping/paotui/orders":
            return self._json(200, self.server.shopping.paotui_orders(uid))
        paotui_preview = re.fullmatch(r"/api/shopping/paotui/previews/([^/]+)/confirm", path)
        if method == "POST" and paotui_preview:
            preview_id = _identifier(paotui_preview[1])
            body = _object(body, {"confirmation", "confirmationToken", "requestKey", "highAmountConfirmation"},
                           {"confirmation", "confirmationToken", "requestKey"})
            _confirmed(body)
            _identifier(body["requestKey"])
            if not isinstance(body["confirmationToken"], str) or not 1 <= len(body["confirmationToken"]) <= 256:
                raise HttpError(400, "INVALID_CONFIRMATION_TOKEN", "请重新获取跑腿报价。")
            if "highAmountConfirmation" in body and not isinstance(body["highAmountConfirmation"], bool):
                raise HttpError(400, "INVALID_CONFIRMATION", "高额配送费确认格式不正确。")
            return self._json(200, self.server.shopping.paotui_confirm(uid, preview_id, body))
        paotui_order = re.fullmatch(r"/api/shopping/paotui/orders/([^/]+)/(payment|status)", path)
        if method == "POST" and paotui_order:
            _object(body, set())
            order_id, action = _identifier(paotui_order[1]), paotui_order[2]
            operation = self.server.shopping.paotui_payment if action == "payment" else self.server.shopping.paotui_order_status
            return self._json(200, operation(uid, order_id))
        if method == "POST" and path == "/api/shopping/search":
            body = _object(body, {"message", "budget", "maxDeliveryMinutes", "category"}, {"message"})
            return self._json(200, self.server.shopping.search(uid, body))
        if method == "POST" and path == "/api/shopping/checkout":
            body = _object(body, {"searchId", "offerId", "addressId"}, {"searchId", "offerId"})
            return self._json(201, self.server.shopping.checkout(uid, body))
        if method == "GET" and path == "/api/shopping/orders":
            return self._json(200, self.server.shopping.orders(uid))
        shopping_checkout = re.fullmatch(r"/api/shopping/checkouts/([^/]+)/confirm", path)
        if method == "POST" and shopping_checkout:
            checkout_id = _identifier(shopping_checkout[1])
            body = _object(body, {"confirmation", "confirmationToken", "requestKey"}, {"confirmation", "confirmationToken", "requestKey"})
            _confirmed(body)
            _identifier(body["requestKey"])
            if not isinstance(body["confirmationToken"], str) or not 1 <= len(body["confirmationToken"]) <= 256:
                raise HttpError(400, "INVALID_CONFIRMATION_TOKEN", "请重新打开待确认订单。")
            return self._json(200, self.server.shopping.confirm(uid, checkout_id, body))
        shopping_order = re.fullmatch(r"/api/shopping/orders/([^/]+)(?:/(advance))?", path)
        if shopping_order and method == "GET" and shopping_order[2] is None:
            return self._json(200, self.server.shopping.order(uid, _identifier(shopping_order[1])))
        if shopping_order and method == "POST" and shopping_order[2] == "advance":
            body = _object(body, {"confirmation"}, {"confirmation"})
            _confirmed(body)
            return self._json(200, self.server.shopping.advance(uid, _identifier(shopping_order[1]), body))
        if method == "GET" and path == "/api/proposals":
            return self._json(200, service.proposals(uid))
        if method == "PUT" and path == "/api/profile":
            return self._json(200, service.save_profile(uid, body))
        if method == "POST" and path == "/api/agent":
            body = _object(body, {"message"}, {"message"})
            if not isinstance(body["message"], str) or not 1 <= len(body["message"].strip()) <= 2000:
                raise HttpError(400, "INVALID_MESSAGE", "请输入 1 至 2000 字的请求。")
            return self._json(200, agent_response(service, uid, body["message"], self.server.planner, self.server.ops))
        if method == "GET" and path == "/api/portfolio":
            return self._json(200, self.server.ops.portfolio(uid))
        if method == "POST" and path == "/api/missions":
            body = _object(body, {"message"}, {"message"})
            return self._json(201, self.server.ops.plan(uid, body["message"]))
        mission = re.fullmatch(r"/api/missions/([^/]+)/confirm", path)
        if method == "POST" and mission:
            body = _object(body, {"confirmation", "token", "approve", "phrases"}, {"confirmation", "token", "approve"})
            _confirmed(body)
            return self._json(200, self.server.ops.confirm(uid, _identifier(mission[1]), body))
        if method == "POST" and path == "/api/transfers/propose":
            body = _object(body, {"to", "amount", "when", "memo"}, {"to", "amount"})
            return self._json(201, service.propose_transfer(uid, body))
        match = re.fullmatch(r"/api/proposals/([^/]+)/(confirm|revoke)", path)
        if method == "POST" and match:
            proposal_id, action = _identifier(match[1]), match[2]
            if action == "revoke":
                _object(body, {"confirmation"})
                if body:
                    _confirmed(body)
                return self._json(200, service.revoke(uid, proposal_id))
            body = _object(body, {"confirmation", "confirmationToken", "key", "simulate_timeout"}, {"confirmation", "confirmationToken"})
            _confirmed(body)
            if not isinstance(body["confirmationToken"], str) or not 1 <= len(body["confirmationToken"]) <= 256:
                raise HttpError(400, "INVALID_CONFIRMATION_TOKEN", "请重新打开待确认草案。")
            if "simulate_timeout" in body and not isinstance(body["simulate_timeout"], bool):
                raise HttpError(400, "INVALID_TIMEOUT_FLAG", "超时演示选项必须是布尔值。")
            if "key" in body:
                _identifier(body["key"])
            # Commit first. UNKNOWN deliberately withholds the committed receipt.
            result = service.confirm(uid, proposal_id, body)
            if body.get("simulate_timeout"):
                transaction = result.get("transaction", {})
                key = transaction.get("key") or transaction.get("idempotencyKey") or body.get("key") or proposal_id
                return self._json(202, {"status": "UNKNOWN", "proposalId": proposal_id, "key": key,
                                        "message": "模拟接口未返回结果。请按同一凭证查询，不要重新创建转账。"})
            return self._json(200, result)
        match = re.fullmatch(r"/api/transactions/by-key/([^/]+)", path)
        if method == "GET" and match:
            return self._json(200, service.transaction(uid, _identifier(match[1])))
        match = re.fullmatch(r"/api/subscriptions/([^/]+)/cancel", path)
        if method == "POST" and match:
            body = _object(body, {"confirmation"}, {"confirmation"})
            _confirmed(body)
            return self._json(200, service.cancel_subscription(uid, _identifier(match[1]), body))
        if method == "POST" and path == "/api/events":
            body = _object(body, {"type", "event_key"}, {"type", "event_key"})
            if not isinstance(body["type"], str) or not 1 <= len(body["type"]) <= 80:
                raise HttpError(400, "INVALID_EVENT", "事件类型不正确。")
            _identifier(body["event_key"])
            return self._json(200, service.event(uid, body))
        if method == "POST" and path == "/api/reset":
            body = _object(body, {"confirmation"}, {"confirmation"})
            _confirmed(body)
            return self._json(200, service.reset(uid))
        if method == "GET" and path in {"/api/evaluation/latest", "/api/evaluation/intents", "/api/evaluation/redteam"}:
            filename = {"/api/evaluation/intents": "intent-rules.json", "/api/evaluation/redteam": "redteam.json"}.get(path, "evaluation.json")
            report = self.server.root / "reports" / filename
            if not report.is_file() or report.stat().st_size > 2000000:
                raise HttpError(404, "EVALUATION_NOT_FOUND", "尚未生成评估报告。")
            try:
                result = strict_json(report.read_text(encoding="utf-8"))
            except (ValueError, UnicodeError, RecursionError):
                raise HttpError(500, "INVALID_EVALUATION", "评估报告格式不正确。") from None
            return self._json(200, result)
        raise HttpError(404, "NOT_FOUND", "接口不存在或方法不受支持。")

    def _handle(self):
        try:
            self._dispatch()
        except HttpError as error:
            self._error(error)
        except Exception as error:
            # Domain errors use the same public envelope. Unexpected errors are redacted.
            from .service import BankError
            if isinstance(error, BankError):
                self._error(error)
            elif isinstance(error, (BrokenPipeError, ConnectionResetError)):
                self.close_connection = True
            else:
                self._error(HttpError(500, "INTERNAL_ERROR", "演示服务暂不可用，请稍后重试。"))

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _handle


def create_server(host="127.0.0.1", port=8765, db_path=None, fixtures_path=None, *, service=None, planner=None, root=ROOT,
                  access_token=None, allowed_hosts=(), allow_unauthenticated_lan=False):
    if service is None:
        from .service import BankService
        db_path = Path(db_path) if db_path else ROOT / ".local" / "tropos.sqlite3"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        service = BankService(str(db_path), str(fixtures_path) if fixtures_path else None)
    return TroposServer((host, port), service, planner=planner, root=root,
                        access_token=access_token, allowed_hosts=allowed_hosts,
                        allow_unauthenticated_lan=allow_unauthenticated_lan)


def main(argv=None):
    parser = argparse.ArgumentParser(description="TROPOS synthetic banking sandbox")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", default=str(ROOT / ".local" / "tropos.sqlite3"))
    parser.add_argument("--lan-config", help="JSON file containing allowedHost and a LAN access mode")
    args = parser.parse_args(argv)
    access_token, allowed_hosts, allow_unauthenticated_lan = None, (), False
    if args.lan_config:
        config_path = Path(args.lan_config).resolve()
        try:
            config = strict_json(config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            parser.error("LAN config is missing or invalid")
        token_keys = {"accessToken", "allowedHost"}
        open_keys = {"accessMode", "allowedHost"}
        if not isinstance(config, dict):
            parser.error("LAN config must be a JSON object")
        if set(config) == token_keys:
            access_token, allowed_hosts = config["accessToken"], (config["allowedHost"],)
        elif set(config) == open_keys and config["accessMode"] == "open":
            allowed_hosts = (config["allowedHost"],)
            allow_unauthenticated_lan = True
        else:
            parser.error("LAN config must select token or open access mode")
    try:
        server = create_server(args.host, args.port, args.db, access_token=access_token,
                               allowed_hosts=allowed_hosts,
                               allow_unauthenticated_lan=allow_unauthenticated_lan)
    except ValueError as error:
        parser.error(str(error))
    print(f"TROPOS 合成银行沙箱：http://{args.host}:{server.server_port}/demo.html", flush=True)
    print(f"理解模式：{server.planner.mode}；仅合成数据，模拟账户登录。", flush=True)
    if allow_unauthenticated_lan:
        print("局域网免口令模式：同一网络内拿到地址的设备均可访问模拟数据。", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
