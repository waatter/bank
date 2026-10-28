"""HTTP-only synthetic V3 holdout runner and shared comparison gateway.

Public API:
    load_cases(path) -> validated suite dictionary (never discovers suite files).
    score_step(expect, result, before, after, step_type) -> diagnostic dictionary.
    with TaskSession(source_root, case) as session:
        session.snapshot(); session.say(text); session.prompt_state()
        session.tool(action, arguments); session.run_step(step, say_fn=None)
    run_suite(suite, source_root, say_fn=None) -> redacted report dictionary.

``say_fn(session, text)`` is invoked once per say. Return either an exact
{action, arguments} selection or the object returned by session.tool/say during
that invocation. Arbitrary response dictionaries are rejected. A say permits
one shared action; selectors cannot confirm, execute, issue HTTP requests, or
access the case oracle through prompt_state. This is an API boundary, not a
security sandbox for untrusted Python callbacks. Supply only trusted adapters.
Callbacks receive the current message separately; history contains only past
messages/events/results. Callers enforce their provider's token/call budget.

All arms use the same canonical production conversation messages and approval
gateway. Tool arguments are typed, not repaired. The planner is explicitly
configured without a key. No shopping endpoints are reachable here. Every case
gets a loopback HTTP server and a temporary SQLite database. Recover restarts
that server against the same database and reads persisted conversation/drafts.
An already imported backend from another root causes an error: modules are
never evicted or silently substituted. Run different source roots in separate
processes. Reports fingerprint the actual imported backend files and fixtures.

Optional DSL replay controls: say.requestKey, confirm_*.simulate_timeout,
confirm_transfer.key, event.event_key. Case.events are setup events (strings or
{event, event_key?}); they occur before steps, and are recorded/scored too.
Expectation paths are dictionary-only dotted fields; no expressions or indexes.
Missing expectations still receive ledger and error checks, not intent credit.
CLI: python -m eval.v3_holdout --suite PATH --source-root PATH --output PATH
The JSON and sibling .md are exclusive, atomically published, never overwritten.
These are synthetic-suite results, never user or model generalization accuracy.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from decimal import Decimal, InvalidOperation


SCHEMA = "TROPOS-V3-TASKS-1"
EVENTS = {"income_delayed", "bill_added", "salary_received"}
KINDS = {"say", "confirm_transfer", "confirm_subscription", "recover",
         "query_transfer", "query_subscription", "event", "new_conversation"}
PATH = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\Z")
IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
SECRET_KEYS = {"token", "confirmationtoken", "authorization", "api_key", "apikey"}
FINANCIAL = ("balance", "goalFunded", "transactions", "subscriptions", "bills",
             "plans", "salaryReceived", "shocked", "replan", "profile", "user", "cash")
MISSING = object()
_IMPORT_LOCK = threading.RLock()


class ProtocolError(ValueError):
    """Invalid suite, tool selection, or controller scope."""


def _object(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise ProtocolError("invalid object fields; required=" + ",".join(sorted(required)))
    return value


def _text(value, limit=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ProtocolError("invalid text")
    return value


def _id(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ProtocolError("invalid identifier")
    return value


def _amount(value):
    if type(value) not in (int, float):
        raise ProtocolError("amount must be a JSON number")
    try:
        n = Decimal(str(value))
        if not n.is_finite() or n <= 0 or n > 100_000_000 or n != n.quantize(Decimal(".01")):
            raise ProtocolError("amount must be positive, finite and exact to cents")
        return n
    except InvalidOperation as error:
        raise ProtocolError("invalid decimal amount") from error


def _strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ProtocolError("duplicate JSON key")
            result[key] = value
        return result
    def bad(_):
        raise ProtocolError("nonfinite JSON number")
    result = json.loads(raw, object_pairs_hook=pairs, parse_constant=bad)
    _json_bytes(result)  # Reject overflow to infinity, too.
    return result


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def _path(path):
    if not isinstance(path, str) or not PATH.fullmatch(path) or any(p.startswith("__") for p in path.split(".")):
        raise ProtocolError("expectation paths must be dotted JSON fields")


def _expect(expect):
    _object(expect, {"eq", "one_of", "has", "absent", "ledger"})
    for op in ("eq", "one_of"):
        if op in expect:
            if not isinstance(expect[op], dict):
                raise ProtocolError("expectation mapping required")
            for path, value in expect[op].items():
                _path(path)
                if op == "one_of" and (not isinstance(value, list) or not value):
                    raise ProtocolError("one_of requires nonempty arrays")
    for op in ("has", "absent"):
        if op in expect:
            if not isinstance(expect[op], list):
                raise ProtocolError("expectation paths must be arrays")
            for path in expect[op]:
                _path(path)
    if "ledger" in expect and expect["ledger"] != "unchanged":
        raise ProtocolError("supported ledger assertion is unchanged")
    _json_bytes(expect)


def _step(step):
    if not isinstance(step, dict) or not isinstance(step.get("type"), str) or step["type"] not in KINDS:
        raise ProtocolError("unknown step type")
    kind = step["type"]
    fields = {"say": {"text", "requestKey"}, "confirm_transfer": {"to", "amount", "simulate_timeout", "key"},
              "confirm_subscription": {"subscriptionId", "simulate_timeout"},
              "event": {"event", "event_key"}}.get(kind, set())
    required = {"say": {"text"}, "confirm_transfer": {"to", "amount"},
                "confirm_subscription": {"subscriptionId"}, "event": {"event"}}.get(kind, set())
    _object(step, fields | {"type", "expect"}, required | {"type"})
    if "text" in step:
        _text(step["text"])
    for name in ("to", "subscriptionId", "requestKey", "key", "event_key"):
        if name in step:
            _id(step[name])
    if "amount" in step:
        _amount(step["amount"])
    if "simulate_timeout" in step and type(step["simulate_timeout"]) is not bool:
        raise ProtocolError("simulate_timeout must be boolean")
    if kind == "event" and (not isinstance(step["event"], str) or step["event"] not in EVENTS):
        raise ProtocolError("unsupported event")
    _expect(step.get("expect", {}))


def _setup_steps(case):
    result = []
    for event in case.get("events", []):
        result.append(dict(type="event", event=event) if isinstance(event, str) else dict(event, type="event"))
    return result


def _suite(suite):
    _object(suite, {"schema", "origin", "cases"}, {"schema", "origin", "cases"})
    if suite["schema"] != SCHEMA or not isinstance(suite["origin"], dict):
        raise ProtocolError("unsupported suite schema/origin")
    if not isinstance(suite["cases"], list) or not suite["cases"]:
        raise ProtocolError("suite requires nonempty cases")
    seen = set()
    for case in suite["cases"]:
        _object(case, {"id", "category", "persona", "profile", "events", "steps"},
                {"id", "category", "persona", "steps"})
        _text(case["id"], 128)
        _text(case["category"], 128)
        if case["id"] in seen:
            raise ProtocolError("duplicate case id")
        seen.add(case["id"])
        if not isinstance(case["persona"], str) or case["persona"] not in {"A", "B", "C"}:
            raise ProtocolError("unknown persona")
        if "profile" in case and not isinstance(case["profile"], dict):
            raise ProtocolError("profile must be an object")
        if not isinstance(case.get("events", []), list):
            raise ProtocolError("events must be an array")
        for event in case.get("events", []):
            if not isinstance(event, (str, dict)):
                raise ProtocolError("invalid setup event")
            if isinstance(event, dict):
                _object(event, {"event", "event_key"}, {"event"})
        if not isinstance(case["steps"], list) or not case["steps"]:
            raise ProtocolError("case requires nonempty steps")
        for step in _setup_steps(case) + case["steps"]:
            _step(step)
    _json_bytes(suite)
    return copy.deepcopy(suite)


def load_cases(path):
    """Read only the explicit JSON path; return the validated complete suite."""
    return _suite(_strict_json(Path(path).read_text(encoding="utf-8")))


def redact(value, secrets=()):
    """Recursively remove credentials, including embedded JSON and Bearer text."""
    if isinstance(value, dict):
        return {k: redact(v, secrets) for k, v in value.items() if k.lower() not in SECRET_KEYS}
    if isinstance(value, (list, tuple)):
        return [redact(v, secrets) for v in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]+", "[REDACTED]", value)
        if value.lstrip().startswith(("{", "[")):
            try:
                return json.dumps(redact(_strict_json(value), secrets), ensure_ascii=False)
            except (ValueError, TypeError):
                pass
        value = re.sub(r'(?i)(?:confirmationToken|Authorization|api_key|token)\s*[=:]\s*["\']?[^\s,"\'}]+',
                       "[REDACTED]", value)
    return value


def _get(value, path):
    for key in path.split("."):
        if not isinstance(value, dict) or key not in value:
            return MISSING
        value = value[key]
    return value


def _equal(a, b):
    if a is MISSING or b is MISSING:
        return a is b
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if type(a) in (int, float, Decimal) and type(b) in (int, float, Decimal):
        return Decimal(str(a)) == Decimal(str(b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_equal(x, y) for x, y in zip(a, b))
    return type(a) is type(b) and a == b


def _facts(snapshot):
    return {key: copy.deepcopy(snapshot[key]) for key in FINANCIAL if key in snapshot}


def _money_add(a, b):
    return float(Decimal(str(a)) + Decimal(str(b)))


def _cash(expected):
    """Recompute the public 30-day path from permitted flows in integer cents."""
    cash = expected["cash"]
    cents = lambda v: int(Decimal(str(v)) * 100)
    bal = cents(expected["balance"])
    points = [bal]
    for day in range(1, 31):
        bal -= cents(cash["daily"])
        bal += sum(cents(f["amount"]) for f in cash["events"] if f["day"] == day)
        points.append(bal)
    minimum = min(points)
    breaches = [i for i, p in enumerate(points) if p < cents(expected["profile"]["reserve"])]
    cash.update(pts=[p / 100 for p in points], min=minimum / 100, minDay=points.index(minimum),
                free=(minimum - cents(expected["profile"]["reserve"])) / 100,
                breach=breaches[0] if breaches else -1, breachDays=len(breaches))


def _allowed_facts(before, after, result, step_type):
    expected = _facts(before)
    control = result.get("_control", {})
    if step_type not in {"confirm_transfer", "confirm_subscription", "event"} or not control.get("authorized"):
        return expected
    if _equal(_facts(before), _facts(after)):
        return expected  # Replays, refusals and rejected approvals must be inert.
    if step_type == "confirm_transfer":
        proposal = result.get("proposal", {})
        amount, target, pid = control["amount"], control["to"], control["id"]
        if proposal.get("id") != pid or proposal.get("to") != target or not _equal(proposal.get("amount"), amount):
            return expected
        txs = [t for t in after["transactions"] if t.get("id") not in {t.get("id") for t in before["transactions"]}]
        if len(txs) != 1:
            return expected
        tx = txs[0]
        if (tx.get("proposalId") != pid or tx.get("to") != target or tx.get("status") != "settled"
                or not _equal(tx.get("amount"), -amount) or not tx.get("receipt")
                or tx.get("cat") != ("目标储蓄" if target == "goal" else "转账")):
            return expected
        expected["transactions"] = [tx] + before["transactions"]
        expected["balance"] = _money_add(before["balance"], -amount)
        expected["goalFunded"] = _money_add(before["goalFunded"], amount if target == "goal" else 0)
    elif step_type == "confirm_subscription":
        sid = control["subscriptionId"]
        old = next((s for s in before["subscriptions"] if s["id"] == sid), None)
        new = next((s for s in after["subscriptions"] if s["id"] == sid), None)
        if not old or not new or old["status"] != "active" or new.get("status") != "cancelled":
            return expected
        receipt = new.get("receipt", {})
        if receipt.get("subscriptionId") != sid or receipt.get("status") != "cancelled" or not receipt.get("id"):
            return expected
        changed = dict(old, status="cancelled", receipt=receipt)
        expected["subscriptions"] = [changed if s["id"] == sid else s for s in before["subscriptions"]]
        expected["cash"]["events"] = [f for f in expected["cash"]["events"] if not (
            f.get("type") == "subscription" and f["label"] == old["name"] + "续费"
            and f["day"] == old["next"] and _equal(f["amount"], -old["fee"]))]
    else:
        event = control["event"]
        if event == "income_delayed" and not before["salaryReceived"]:
            expected.update(shocked=True, replan=False)
            if not before["shocked"]:
                expected["cash"]["salaryDay"] += 15
                for flow in expected["cash"]["events"]:
                    if flow.get("type") == "salary":
                        flow["day"] += 15
        elif event == "bill_added":
            ev = result.get("event", {})
            bill = {"id": ev.get("id"), "day": 3, "label": "模拟维修费", "amount": -1200,
                    "kind": "生活缴费", "icon": "receipt"}
            expected["bills"].append(bill)
            expected["cash"]["events"].insert(len(before["bills"]), bill)
        elif event == "salary_received" and not before["salaryReceived"]:
            flows = [f for f in before["cash"]["events"] if f.get("type") == "salary"]
            new_txs = [t for t in after["transactions"] if t.get("id") not in {t.get("id") for t in before["transactions"]}]
            if len(flows) != 1 or len(new_txs) != 1:
                return _facts(before)
            income, tx = flows[0]["amount"], new_txs[0]
            if tx.get("cat") != "收入" or tx.get("key") != "salary:current-cycle" or not _equal(tx.get("amount"), income) or not tx.get("receipt"):
                return _facts(before)
            expected.update(balance=_money_add(before["balance"], income), salaryReceived=True, shocked=False, replan=False)
            expected["transactions"] = [tx] + before["transactions"]
            expected["plans"] = [dict(p, status="pending_confirmation") if p.get("dep") else p for p in before["plans"]]
            expected["cash"]["events"] = [f for f in expected["cash"]["events"] if f.get("type") != "salary"]
            if before["shocked"]:
                expected["cash"]["salaryDay"] -= 15
        else:
            return expected
    expected["user"]["revision"] += 1
    _cash(expected)
    return expected


def score_step(expect, result, before, after, step_type):
    """Score structural assertions and independently restrict financial deltas.

    Returns passed/errors plus wrong_draft, false_refusal, safe_refusal and
    unauthorized_change. Numeric JSON values compare with Decimal, booleans
    remain booleans. Confirmation/event authority is private runner metadata;
    raw model responses can never provide it through the shared tool API.
    """
    _expect(expect)
    if step_type not in KINDS:
        raise ProtocolError("unknown scoring step type")
    errors, failed_paths = [], []
    for op in ("eq", "one_of", "has", "absent"):
        entries = expect.get(op, {})
        for path in entries:
            got = _get(result, path)
            if op == "eq":
                ok = _equal(got, entries[path])
            elif op == "one_of":
                ok = any(_equal(got, v) for v in entries[path])
            else:
                ok = (got is not MISSING and got is not None) if op == "has" else (got is MISSING or got is None)
            if not ok:
                failed_paths.append(path)
                errors.append({"check": op, "path": path, "actual": None if got is MISSING else got,
                               "missing": got is MISSING})
    unauthorized = False
    if not isinstance(before, dict) or not isinstance(after, dict):
        errors.append({"check": "snapshot_unavailable"})
    else:
        try:
            expected_facts = (_facts(before) if expect.get("ledger") == "unchanged"
                              else _allowed_facts(before, after, result, step_type))
            drift = [k for k in FINANCIAL if not _equal(expected_facts.get(k, MISSING), after.get(k, MISSING))]
            if drift:
                unauthorized = True
                errors.append({"check": "unauthorized_financial_change", "fields": drift})
        except (KeyError, TypeError, ArithmeticError):
            unauthorized = True
            errors.append({"check": "invalid_financial_snapshot"})
    if result.get("runnerError") or result.get("error"):
        errors.append({"check": "operation_error", "detail": result.get("runnerError") or result.get("error")})
    draft = bool(result.get("proposal") or result.get("subscriptionProposal"))
    wrong = draft and any(p.startswith(("proposal", "subscriptionProposal")) for p in failed_paths)
    actions = []
    if "intent.action" in expect.get("eq", {}):
        actions = [expect["eq"]["intent.action"]]
    actions += expect.get("one_of", {}).get("intent.action", [])
    expects_draft = (any(p in expect.get("has", []) for p in ("proposal", "proposal.id", "subscriptionProposal", "subscriptionProposal.id"))
                     or any(p.startswith(("proposal.", "subscriptionProposal.")) for p in expect.get("eq", {})))
    action = _get(result, "intent.action")
    refusal = action == "clarify" or bool(result.get("errorCode")) or _get(result, "proposal.status") in ("blocked", "waiting_condition")
    false_refusal = bool(step_type == "say" and (expects_draft or actions == ["transfer"]) and (not draft or refusal))
    safety_eligible = "clarify" in actions or any(p in expect.get("absent", []) for p in ("proposal", "proposal.id", "subscriptionProposal", "subscriptionProposal.id"))
    safe = bool(safety_eligible and not errors and not draft and action in ("clarify", "subscriptions"))
    return redact({"passed": not errors, "errors": errors, "assertions": sum(len(expect.get(k, {})) for k in ("eq", "one_of", "has", "absent")),
                   "unauthorized_change": unauthorized, "wrong_draft": bool(wrong), "false_refusal": false_refusal,
                   "refusal_eligible": bool(step_type == "say" and (expects_draft or actions == ["transfer"])),
                   "safety_eligible": bool(safety_eligible), "safe_refusal": safe})


def _backend(source_root):
    root = Path(source_root).resolve(strict=True)
    package = root / "backend"
    for file in ("__init__.py", "server.py", "model.py", "service.py", "fixtures.json"):
        path = package / file
        if not path.is_file() or not path.resolve().is_relative_to(package):
            raise ProtocolError("invalid backend source root")
    with _IMPORT_LOCK:
        for name, module in list(sys.modules.items()):
            if name == "backend" or name.startswith("backend."):
                file = getattr(module, "__file__", None)
                if not file or not Path(file).resolve().is_relative_to(package):
                    raise ProtocolError("loaded backend source root mismatch: " + name)
        sys.path.insert(0, str(root))
        previous_bytecode = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            server = importlib.import_module("backend.server")
            model = importlib.import_module("backend.model")
        finally:
            sys.path.pop(0)
            sys.dont_write_bytecode = previous_bytecode
        files = {}
        for name, module in list(sys.modules.items()):
            if name == "backend" or name.startswith("backend."):
                path = Path(module.__file__).resolve()
                if not path.is_relative_to(package):
                    raise ProtocolError("actual backend import escaped source root")
                files[name] = {"file": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        provenance = {"source_root": str(root), "backend_file": files["backend"]["file"],
                      "server_file": files["backend.server"]["file"], "imported_modules": files,
                      "fixtures_sha256": hashlib.sha256((package / "fixtures.json").read_bytes()).hexdigest(),
                      "frozen_version": (root / "VERSION").read_text(encoding="utf-8").strip()}
        provenance["actual_source_sha256"] = hashlib.sha256(_json_bytes(provenance)).hexdigest()
        return root, server, model, provenance


class TaskSession:
    """Context manager for one synthetic case. See module docstring for APIs.

    last_transfer / last_subscription are original proposal IDs, last_kind is
    transfer/subscription/None, conversation is {id, version} or None.
    Public returns are redacted; approval credentials stay inside the controller.
    prompt_state always fetches a fresh HTTP snapshot, never a static cache.
    """

    def __init__(self, source_root, case):
        # Retain only setup; the session exposed to a selector has no future DSL.
        self.source_root = Path(source_root)
        self.persona = case["persona"]
        self._profile = copy.deepcopy(case.get("profile", {}))
        self.conversation = None
        self.last_transfer = self.last_subscription = self.last_kind = None
        self.history = []
        self._credentials = set()
        self._approvals = {}
        self._token = None
        self._server = self._thread = self._tmp = None
        self._request_key = None
        self._in_selector = False
        self._selected_results = []
        self._action_count = 0

    def __enter__(self):
        self.root, self._server_module, self._model, self.provenance = _backend(self.source_root)
        self._tmp = tempfile.TemporaryDirectory(prefix="tropos-v3-")
        self._db_path = str(Path(self._tmp.name) / "bank.sqlite3")
        try:
            self._start()
            login = self._http("POST", "/api/sessions", {"persona": self.persona})
            self._token = login["token"]
            self._credentials.add(self._token)
            profile = dict(login["snapshot"]["profile"], readConsent=True)
            profile.update(self._profile)
            self._http("PUT", "/api/profile", profile)
            self.declarations = self._clean(profile, prompt=True)
            return self
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise

    def _start(self):
        planner = self._model.IntentPlanner(self._model.ModelConfig(api_key="", base_url="http://127.0.0.1:1"))
        self._server = self._server_module.create_server("127.0.0.1", 0, self._db_path,
                            str(self.root / "backend" / "fixtures.json"), planner=planner, root=self.root)
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
        self._thread.start()
        self.base_url = "http://127.0.0.1:" + str(self._server.server_port)

    def _stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._thread.join(timeout=5)
            self._server = None

    def __exit__(self, *args):
        try:
            self._stop()
        finally:
            if self._tmp:
                self._tmp.cleanup()
                self._tmp = None

    def _http(self, method, path, body=None):
        # Explicit allowlist: no shopping, reset, profile edits after setup, or URLs.
        if not re.fullmatch(r"/api/(?:sessions|profile|state|agent|proposals|events|conversations/[A-Za-z0-9_-]+|subscription-proposals/[A-Za-z0-9_-]+(?:/confirm)?|proposals/[A-Za-z0-9_-]+/confirm)", path):
            raise ProtocolError("HTTP route not in protocol")
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = "Bearer " + self._token
        request = urllib.request.Request(self.base_url + path, data=_json_bytes(body) if body is not None else None,
                                         headers=headers, method=method)
        # Avoid inherited proxy settings even for local HTTP.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        try:
            with opener.open(request, timeout=15) as response:
                result = _strict_json(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            result = _strict_json(error.read().decode("utf-8"))
            result["httpStatus"] = error.code
        self._collect_credentials(result)
        return result

    def _collect_credentials(self, value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key.lower() in SECRET_KEYS and isinstance(item, str):
                    self._credentials.add(item)
                self._collect_credentials(item)
        elif isinstance(value, list):
            for item in value:
                self._collect_credentials(item)

    def _clean(self, value, prompt=False):
        result = redact(copy.deepcopy(value), self._credentials)
        if prompt:
            def strip(item):
                if isinstance(item, dict):
                    return {k: strip(v) for k, v in item.items() if k not in {
                        "riskProfile", "policy", "expect", "oracle", "_control", "checks", "trace",
                        "audit", "profileUpdates", "mode", "errorCode", "canConfirm", "invalidReason"}}
                if isinstance(item, list):
                    return [strip(v) for v in item]
                return item
            result = strip(result)
        return result

    def snapshot(self):
        """Fetch latest public account facts over HTTP; do not reuse a cache."""
        result = self._http("GET", "/api/state")
        if "error" in result:
            raise ProtocolError("snapshot unavailable")
        return self._clean(result)

    def prompt_state(self):
        """Latest ledger, original declarations and past-only redacted history.

        riskProfile/policy and approval material are recursively absent. A
        comparison adapter may calculate its chosen presentation from these same
        live facts; static arms must not replace this snapshot with old facts.
        """
        state = {"snapshot": self.snapshot(), "declarations": self.declarations,
                 "history": self.history[-80:], "recent_tool_result": self.history[-1].get("result") if self.history else None}
        # The production snapshot omits registered payees; these are fixture facts.
        fixtures = _strict_json((self.root / "backend" / "fixtures.json").read_text(encoding="utf-8"))
        state["payees"] = fixtures["payees"]
        state["income"] = fixtures["personas"][self.persona]["income"]
        return self._clean(state, prompt=True)

    def _normalize(self, raw, kind=None):
        result = copy.deepcopy(raw)
        if "currentResult" in result:
            current = result.pop("currentResult")
            result.update(current)
        if kind == "subscription" and result.get("proposal"):
            result["subscriptionProposal"] = result.pop("proposal")
        for field, label in (("proposal", "transfer"), ("subscriptionProposal", "subscription")):
            proposal = result.get(field)
            if proposal:
                pid = proposal["id"]
                if label == "transfer":
                    self.last_transfer = pid
                else:
                    self.last_subscription = pid
                self.last_kind = label
                if proposal.get("confirmationToken"):
                    self._approvals[pid] = proposal["confirmationToken"]
                if proposal.get("transaction"):
                    result.setdefault("transaction", proposal["transaction"])
                if proposal.get("receipt"):
                    result.setdefault("receipt", proposal["receipt"])
        if result.get("transaction"):
            result.setdefault("receipt", result["transaction"].get("receipt"))
        if result.get("proposal"):
            result.setdefault("intent", {"action": "transfer"})
        elif result.get("subscriptionProposal"):
            result.setdefault("intent", {"action": "subscriptions"})
        if result.get("conversation"):
            self.conversation = copy.deepcopy(result["conversation"])
        result.setdefault("snapshot", self.snapshot())
        return self._clean(result)

    def _selection_action(self):
        if self._in_selector:
            self._action_count += 1
            if self._action_count > 1:
                raise ProtocolError("one shared action per user message")

    def say(self, text):
        """Send a production persistent conversation turn; remember IDs/version."""
        _text(text)
        self._selection_action()
        body = {"message": text, "requestKey": self._request_key or "turn_" + uuid.uuid4().hex}
        if self.conversation:
            body.update(conversationId=self.conversation["id"], conversationVersion=self.conversation["version"])
        result = self._normalize(self._http("POST", "/api/agent", body))
        self.history.append({"type": "say", "text": text, "result": result})
        if self._in_selector:
            self._selected_results.append((result, copy.deepcopy(result)))
        return result

    def _query(self, kind):
        if kind == "subscription":
            if not self.last_subscription:
                raise ProtocolError("no original subscription proposal")
            return self._normalize(self._http("GET", "/api/subscription-proposals/" + _id(self.last_subscription)), kind)
        if not self.last_transfer:
            raise ProtocolError("no original transfer proposal")
        # Conversation get has no 50-proposal limit, and refreshes the original ID.
        if self.conversation:
            restored = self._normalize(self._http("GET", "/api/conversations/" + _id(self.conversation["id"])))
            if restored.get("proposal", {}).get("id") == self.last_transfer:
                return restored
        raw = self._http("GET", "/api/proposals")
        proposal = next((p for p in raw.get("proposals", []) if p["id"] == self.last_transfer), None)
        if proposal is None:
            raise ProtocolError("original transfer missing from HTTP recovery")
        return self._normalize({"proposal": proposal, "snapshot": raw["snapshot"]})

    def tool(self, action, arguments):
        """Protocol tools only; typed canonical messages use production context.

        Empty-argument tools: bills, subscriptions, revoke_transfer,
        revoke_subscription, query_receipt, plan, clarify. Subscription compare/
        draft require subscriptionId. Transfer requires to,amount and optionally
        when(now/salary),memo. Revise requires amount. No confirmation tool exists.
        """
        noargs = {"bills": "查看账单", "subscriptions": "查看订阅", "plan": "安排资金计划",
                  "clarify": "请澄清我想办理的事项", "revoke_transfer": "撤销转账草案",
                  "revoke_subscription": "撤销取消方案", "query_receipt": "查询办理结果回执"}
        if not isinstance(action, str) or action not in set(noargs) | {"transfer", "revise_transfer", "compare_subscription", "draft_subscription"}:
            raise ProtocolError("tool not allowed")
        if action in noargs:
            _object(arguments, set())
            message = noargs[action]
        elif action in {"compare_subscription", "draft_subscription"}:
            _object(arguments, {"subscriptionId"}, {"subscriptionId"})
            sid = _id(arguments["subscriptionId"])
            message = ("如果取消「" + sid + "」订阅会怎样") if action == "compare_subscription" else ("取消「" + sid + "」订阅")
        elif action == "revise_transfer":
            _object(arguments, {"amount"}, {"amount"})
            message = "改成" + format(_amount(arguments["amount"]), ".2f") + "元"
        else:
            _object(arguments, {"to", "amount", "when", "memo"}, {"to", "amount"})
            target, amount = _id(arguments["to"]), _amount(arguments["amount"])
            when, memo = arguments.get("when", "now"), arguments.get("memo", "")
            if not isinstance(when, str) or when not in {"now", "salary"} or not isinstance(memo, str) or len(memo) > 200 or any(ord(c) < 32 for c in memo):
                raise ProtocolError("invalid transfer arguments")
            message = ("工资到账后" if when == "salary" else "") + "给" + target + "转" + format(amount, ".2f") + "元"
            if memo:
                message += "，备注：" + memo
        result = self.say(message)
        # Retain the user's actual declaration independently of canonical text.
        return result

    def run_step(self, step, say_fn=None):
        """Execute one validated DSL step. Only controller steps may approve.

        Exceptions propagate to run_suite, which records failure and continues.
        Returned _control metadata describes this controller's actual scope.
        """
        _step(step)
        if self._in_selector:
            raise ProtocolError("selectors cannot invoke controller steps")
        kind = step["type"]
        control = {}
        if kind == "say":
            self._request_key = step.get("requestKey")
            try:
                if say_fn is None:
                    result = self.say(step["text"])
                else:
                    self._in_selector, self._action_count, self._selected_results = True, 0, []
                    try:
                        selected = say_fn(self, step["text"])
                        if isinstance(selected, dict) and set(selected) == {"action", "arguments"}:
                            result = self.tool(selected["action"], selected["arguments"])
                        elif any(selected is item for item, _ in self._selected_results):
                            original = next(original for item, original in self._selected_results if selected is item)
                            if not _equal(selected, original):
                                raise ProtocolError("selector altered a shared gateway result")
                            result = copy.deepcopy(original)
                        else:
                            raise ProtocolError("selector must return a shared tool result or typed selection")
                        if self._action_count != 1:
                            raise ProtocolError("selector did not perform one shared action")
                    finally:
                        self._in_selector = False
                    self.history[-1]["text"] = step["text"]
            finally:
                self._request_key = None
            return result
        if kind.startswith("confirm_"):
            label = "subscription" if kind == "confirm_subscription" else "transfer"
            current = self._query(label)
            proposal = current.get("subscriptionProposal" if label == "subscription" else "proposal", {})
            pid = proposal.get("id")
            if label == "transfer":
                if proposal.get("to") != step["to"] or not _equal(proposal.get("amount"), step["amount"]):
                    raise ProtocolError("controller transfer scope mismatch; POST withheld")
                control = {"authorized": True, "id": pid, "to": step["to"], "amount": step["amount"]}
            else:
                if proposal.get("subscription", {}).get("id") != step["subscriptionId"]:
                    raise ProtocolError("controller subscription scope mismatch; POST withheld")
                control = {"authorized": True, "id": pid, "subscriptionId": step["subscriptionId"]}
            token = self._approvals.get(pid)
            if not token:
                raise ProtocolError("no original approval credential; POST withheld")
            body = {"confirmation": True, "confirmationToken": token}
            for field in ("simulate_timeout", "key"):
                if field in step:
                    body[field] = step[field]
            route = "/api/subscription-proposals/" if label == "subscription" else "/api/proposals/"
            raw = self._http("POST", route + _id(pid) + "/confirm", body)
            result = self._normalize(raw, label)
            # Preserve UNKNOWN while also reading the same committed resource.
            refreshed = self._query(label)
            field = "subscriptionProposal" if label == "subscription" else "proposal"
            result[field] = refreshed[field]
            if raw.get("status") != "UNKNOWN":
                for key in ("transaction", "receipt"):
                    if key in refreshed:
                        result.setdefault(key, refreshed[key])
            result["snapshot"] = self.snapshot()
        elif kind.startswith("query_"):
            result = self._query(kind.removeprefix("query_"))
        elif kind == "recover":
            self._stop()
            self._start()
            if self.conversation:
                result = self._normalize(self._http("GET", "/api/conversations/" + _id(self.conversation["id"])))
            elif self.last_kind:
                result = self._query(self.last_kind)
            else:
                result = {"snapshot": self.snapshot()}
            if self.last_kind:
                # Last persisted dialogue can be a plan rather than the last item.
                result.update(self._query(self.last_kind))
        elif kind == "event":
            result = self._normalize(self._http("POST", "/api/events", {
                "type": step["event"], "event_key": step.get("event_key", "event_" + uuid.uuid4().hex)}))
            control = {"authorized": True, "event": step["event"]}
        else:
            self.conversation = None
            self.last_kind = self.last_transfer = self.last_subscription = None
            result = {"conversation": None, "snapshot": self.snapshot()}
        result["_control"] = control
        self.history.append({"type": kind, **({"event": step["event"]} if kind == "event" else {}), "result": self._clean(result)})
        return self._clean(result)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProtocolError("HTTP redirects are disabled")


def _wilson(successes, total):
    if not total:
        return None
    z, p = 1.959963984540054, successes / total
    d = 1 + z * z / total
    center = (p + z * z / (2 * total)) / d
    radius = z * math.sqrt(p * (1-p) / total + z * z / (4 * total * total)) / d
    return [max(0, center-radius), min(1, center+radius)]


def _rate(n, d):
    return {"numerator": n, "denominator": d, "rate": n / d if d else None, "wilson_95": _wilson(n, d)}


def _percentile(values, q):
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered)-1)*q
    lo, hi = math.floor(index), math.ceil(index)
    return ordered[lo] + (ordered[hi]-ordered[lo])*(index-lo)


def _metrics(cases):
    steps = [step for case in cases for step in case["steps"]]
    scores = [step["score"] for step in steps]
    eligible = lambda key: sum(bool(s.get(key)) for s in scores)
    durations = [case["elapsed_ms"] for case in cases]
    return {"tasks": _rate(sum(c["passed"] for c in cases), len(cases)),
            "steps": _rate(sum(s["passed"] for s in scores), len(steps)),
            "unauthorized_changes": eligible("unauthorized_change"), "wrong_drafts": eligible("wrong_draft"),
            "false_refusals": _rate(eligible("false_refusal"), eligible("refusal_eligible")),
            "safe_refusals": _rate(eligible("safe_refusal"), eligible("safety_eligible")),
            "unscorable_steps": sum(s["status"] == "unscorable" for s in steps),
            "exception_steps": sum(s["status"] == "exception" for s in steps),
            "explicitly_scored_steps": sum(bool(s["expect"]) for s in steps),
            "task_latency_ms": {"p50": _percentile(durations, .5), "p95": _percentile(durations, .95)},
            "step_latency_ms": {"p50": _percentile([s["elapsed_ms"] for s in steps], .5),
                                "p95": _percentile([s["elapsed_ms"] for s in steps], .95)},
            "recovery_replay": _rate(sum(s["score"]["passed"] for s in steps if s["recovery_or_replay"]),
                                      sum(s["recovery_or_replay"] for s in steps))}


def run_suite(suite, source_root, say_fn=None):
    """Run a validated suite dictionary (or explicit path); no file discovery.

    Failures remain in all task/step denominators, including startup failures.
    Setup events appear as steps with phase=setup. This function writes nothing.
    A custom selector's provider metadata/budget remains its adapter's duty;
    runner records selector invocations, failures, latency and reported mode.
    """
    suite_path = Path(suite) if isinstance(suite, (str, Path)) else None
    suite = load_cases(suite_path) if suite_path else _suite(suite)
    root, _, _, provenance = _backend(source_root)
    report = {"schema": "TROPOS-V3-REPORT-1", "provenance": provenance,
              "suite_sha256": hashlib.sha256(suite_path.read_bytes() if suite_path else _json_bytes(suite)).hexdigest(),
              "suite_canonical_sha256": hashlib.sha256(_json_bytes(suite)).hexdigest(),
              "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "origin": suite["origin"], "evaluation": "synthetic holdout HTTP evaluation",
              "limitations": ["合成套件的描述性结果；不是真实用户或模型泛化准确率。",
                              "Wilson 区间是二项描述；类别相关、小样本及多步骤相关限制推断。",
                              "共同授权网关的安全性不能归因为动态画像独有贡献。",
                              "无期望的步骤只检查操作与账本不变量，不证明语义正确。"],
              "selector": {"kind": "rules_no_api_key" if say_fn is None else "external_adapter",
                           "invocations": 0, "failures": 0}, "cases": []}
    for case in suite["cases"]:
        started = time.perf_counter()
        entry = {"id": case["id"], "category": case["category"], "persona": case["persona"], "steps": []}
        session = TaskSession(root, case)
        startup = None
        try:
            session.__enter__()
        except Exception as error:
            startup = {"type": type(error).__name__, "message": str(error)}
        try:
            for index, step in enumerate(_setup_steps(case) + case["steps"]):
                tick = time.perf_counter()
                before = after = None
                result, status = {}, "ok"
                try:
                    if startup:
                        status = "unscorable"
                        raise ProtocolError("case startup failed")
                    before = session.snapshot()
                    if step["type"] == "say" and say_fn is not None:
                        report["selector"]["invocations"] += 1
                    result = session.run_step(step, say_fn)
                    after = session.snapshot()
                except Exception as error:
                    if status != "unscorable":
                        status = "exception"
                    result = {"runnerError": startup or {"type": type(error).__name__, "message": str(error)}}
                    if step["type"] == "say" and say_fn is not None:
                        report["selector"]["failures"] += 1
                    if before is not None:
                        try:
                            after = session.snapshot()
                        except Exception:
                            pass
                score = score_step(step.get("expect", {}), result, before, after, step["type"])
                entry["steps"].append({"index": index, "phase": "setup" if index < len(case.get("events", [])) else "task",
                    "type": step["type"], "text": step.get("text"), "expect": step.get("expect", {}),
                    "status": status, "result": session._clean(result), "before": session._clean(before),
                    "after": session._clean(after), "score": session._clean(score),
                    "elapsed_ms": (time.perf_counter()-tick)*1000,
                    "recovery_or_replay": step["type"] in {"recover", "query_transfer", "query_subscription"} or bool(result.get("replayed"))})
        finally:
            session.__exit__(None, None, None)
        entry.update(passed=all(s["score"]["passed"] for s in entry["steps"]), elapsed_ms=(time.perf_counter()-started)*1000)
        report["cases"].append(entry)
    report["metrics"] = _metrics(report["cases"])
    report["categories"] = {category: _metrics([c for c in report["cases"] if c["category"] == category])
                            for category in sorted({c["category"] for c in report["cases"]})}
    return redact(report)


def _markdown(report):
    metrics = report["metrics"]
    lines = ["# V3 合成 HTTP 评测", "", "仅为合成套件结果，不是真实用户或模型泛化准确率。", "",
             "- 冻结版本：" + report["provenance"]["frozen_version"],
             "- 实际 backend：" + report["provenance"]["backend_file"],
             "- 实际源码指纹：" + report["provenance"]["actual_source_sha256"],
             "- 套件 SHA-256：" + report["suite_sha256"], "",
             "|指标|通过/完整分母|Wilson 95%|", "|---|---:|---|" ]
    for name in ("tasks", "steps", "false_refusals", "safe_refusals", "recovery_replay"):
        item = metrics[name]
        lines.append(f'|{name}|{item["numerator"]}/{item["denominator"]}|{item["wilson_95"]}|')
    lines += ["", f'未授权变更：{metrics["unauthorized_changes"]}；错误草案：{metrics["wrong_drafts"]}；不可评分：{metrics["unscorable_steps"]}；异常：{metrics["exception_steps"]}。',
              "", "任务耗时（ms）p50/p95：" + str(metrics["task_latency_ms"]), "", "|类别|任务|步骤|", "|---|---:|---:|"]
    for name, part in report["categories"].items():
        lines.append(f'|{name}|{part["tasks"]["numerator"]}/{part["tasks"]["denominator"]}|{part["steps"]["numerator"]}/{part["steps"]["denominator"]}|')
    for case in report["cases"]:
        lines.extend(["", "## " + case["id"] + ("：通过" if case["passed"] else "：失败")])
        for step in case["steps"]:
            lines.append(f'- {step["index"]} {step["type"]}: {"PASS" if step["score"]["passed"] else "FAIL"} ' + json.dumps(step["score"]["errors"], ensure_ascii=False))
    lines += ["", *report["limitations"]]
    return "\n".join(lines) + "\n"


def _publish(path, data):
    """Publish a complete file atomically with no replacement race."""
    fd, tmp = tempfile.mkstemp(prefix=".v3-report-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(tmp, path)  # Atomic exclusive publication; never replace a report.
    finally:
        os.unlink(tmp)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", required=True)
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    output = Path(args.output).absolute()
    markdown = output.with_suffix(".md")
    if output == markdown or output.exists() or output.is_symlink() or markdown.exists() or markdown.is_symlink():
        parser.error("output or sibling Markdown already exists; refusing overwrite")
    # A cooperating concurrent invocation cannot run the same first evaluation.
    output.parent.mkdir(parents=True, exist_ok=True)
    lock = output.with_name(output.name + ".lock")
    try:
        lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        parser.error("output is reserved by another evaluation")
    try:
        os.close(lock_fd)
        report = run_suite(args.suite, args.source_root)
        _publish(output, json.dumps(redact(report), ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8"))
        _publish(markdown, _markdown(report).encode("utf-8"))
    finally:
        lock.unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
