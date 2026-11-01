"""Per-account model selection without exposing provider credentials to clients."""
from __future__ import annotations

import os
import math
import re
import threading
import urllib.parse

from .model import IntentPlanner, ModelConfig
from .service import BankError, now


PROVIDER_DEFINITIONS = (
    ("rules", "本地规则", "不调用外部模型；使用可解释的本地规则理解请求。"),
    ("deepseek", "DeepSeek", "支持推理、结构化输出、工具调用及官方 Search；V4 Pro 可使用原生联网。"),
    ("openai", "OpenAI", "使用 Responses API 原生联网与来源；ChatGPT 网页会员不能代替 API Key。"),
    ("kimi", "Kimi", "支持长上下文、推理、工具调用及官方 Search Pro 联网检索。"),
    ("qwen", "千问（Qwen）", "使用阿里云百炼官方接口；新款 Qwen3 模型可启用 Responses 联网、网页提取与代码解释器，旧模型自动使用兼容接口。"),
    ("custom", "自定义兼容接口", "使用 OpenAI 兼容的 /chat/completions 服务。"),
)
CONFIGURABLE_PROVIDER_IDS = frozenset(row[0] for row in PROVIDER_DEFINITIONS if row[0] != "rules")


def _provider_for_url(base_url):
    host = (urllib.parse.urlsplit(base_url).hostname or "").lower()
    if "deepseek" in host:
        return "deepseek"
    if "openai" in host:
        return "openai"
    if "moonshot" in host or "kimi" in host:
        return "kimi"
    if "dashscope" in host or host.endswith(".maas.aliyuncs.com"):
        return "qwen"
    return "custom"


def _timeout(value):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 15.0
    return parsed if 1 <= parsed <= 60 else 15.0


MODEL_TEST_FAILURES = {
    "provider_auth_failed": "API Key 无效或已失效，请更换密钥后重试。",
    "provider_payment_required": "模型账户余额不足或尚未开通 API 计费。",
    "provider_access_denied": "当前 API Key 没有调用该模型的权限。",
    "provider_model_or_endpoint_not_found": "模型名或接口地址不存在，请按开放平台控制台核对。",
    "provider_rate_limited": "模型服务正在限流或额度已用完，请稍后重试。",
    "provider_server_error": "模型服务端暂时异常，请稍后重试。",
    "provider_unavailable": "无法连接模型服务，请检查网络、代理设置和接口地址。",
    "invalid_provider_json": "模型返回的内容不是有效 JSON。",
    "invalid_provider_response": "模型响应格式不符合兼容接口约定。",
    "incomplete_provider_response": "模型输出未完整结束，请重试或调高超时时间。",
    "provider_output_truncated": "模型输出达到长度上限而被截断，请重试。",
    "provider_content_filtered": "模型服务未返回完整内容，请调整请求后重试。",
    "provider_resource_unavailable": "模型服务本次资源不足，请稍后重试。",
    "provider_aborted": "模型服务中断了本次生成，请重试。",
}


def _runtime_failure(result):
    """Return a safe, user-facing reason without leaking provider bodies."""
    trace = result.get("trace") if isinstance(result, dict) else None
    if not isinstance(trace, list):
        return None, None
    reason = None
    for row in reversed(trace):
        if isinstance(row, dict) and isinstance(row.get("reason"), str):
            reason = row["reason"]
            break
    if not reason:
        return None, None
    # Advice can record the structured failure followed by the prose failure.
    # Prefer the final prose failure because it explains why no model answer
    # could ultimately be displayed.
    normalized = reason.rsplit(";prose:", 1)[-1]
    message = MODEL_TEST_FAILURES.get(normalized)
    if message is None:
        if normalized in {"invalid_conversation_text", "unsafe_conversation_text"}:
            message = "模型未返回可安全展示的正文。"
        elif normalized.startswith("invalid_"):
            message = "模型输出未通过本次格式或安全校验。"
        else:
            message = "模型调用本次未完成，已安全降级。"
    return normalized, message


def _test_failure_message(result, personal=False):
    trace = result.get("trace") if isinstance(result, dict) else None
    reason = trace[0].get("reason") if isinstance(trace, list) and trace and isinstance(trace[0], dict) else None
    detail = MODEL_TEST_FAILURES.get(reason, "模型未通过连接或结构化输出检查。")
    prefix = "个人模型" if personal else "模型"
    return f"{prefix}测试未通过：{detail} 当前选择未改变。", reason


class ModelCenter:
    """Resolve one safe planner per synthetic account.

    Provider keys live only in process environment and the private planner objects.
    The public contract projects metadata and never serializes a ModelConfig.
    """

    def __init__(self, bank, default_planner, allow_runtime_config=False):
        self.bank = bank
        self.default_planner = default_planner
        self.allow_runtime_config = bool(allow_runtime_config)
        # BYOK credentials are intentionally process-memory only and partitioned
        # by authenticated demo account. They are never written to SQLite.
        self._personal = {}
        self._personal_lock = threading.RLock()
        self.providers = {}
        for provider_id, label, description in PROVIDER_DEFINITIONS:
            self.providers[provider_id] = {
                "id": provider_id, "label": label, "description": description,
                "planner": None, "model": None, "host": None, "missing": [], "source": None,
            }
        self.providers["rules"].update(planner=IntentPlanner(ModelConfig()), model="确定性规则", host="本机", source="builtin")
        self._load_environment()
        self.default_provider = "rules"
        if default_planner.mode == "llm":
            provider_id = _provider_for_url(default_planner.config.base_url)
            self._install(provider_id, default_planner, source="legacy_environment")
            self.default_provider = provider_id
        with bank._db(True) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS model_preferences(
                user_id TEXT PRIMARY KEY, provider TEXT NOT NULL, updated_at TEXT NOT NULL
            )""")

    def _install(self, provider_id, planner, source="environment"):
        parsed = urllib.parse.urlsplit(planner.config.base_url)
        self.providers[provider_id].update(
            planner=planner, model=planner.config.model,
            host=parsed.hostname or "自定义地址", missing=[], source=source)

    def _environment_config(self, provider_id, prefix, default_base="", default_model=""):
        key = os.getenv(prefix + "_API_KEY", "").strip()
        base = os.getenv(prefix + "_BASE_URL", default_base).strip().rstrip("/")
        model = os.getenv(prefix + "_MODEL", default_model).strip()
        missing = []
        if not key:
            missing.append("API Key")
        if not base:
            missing.append("接口地址")
        if not model:
            missing.append("模型名")
        self.providers[provider_id]["missing"] = missing
        if not missing:
            timeout = _timeout(os.getenv(prefix + "_TIMEOUT", os.getenv("TROPOS_LLM_TIMEOUT", "15")))
            self._install(provider_id, IntentPlanner(ModelConfig(key, base, model, timeout)), source="environment")

    def _load_environment(self):
        self._environment_config("deepseek", "TROPOS_DEEPSEEK", "https://api.deepseek.com", "deepseek-flash")
        # Generic DEEPSEEK_API_KEY remains compatible with the existing launcher.
        if self.providers["deepseek"]["planner"] is None and os.getenv("DEEPSEEK_API_KEY"):
            self._install("deepseek", IntentPlanner(ModelConfig(
                os.getenv("DEEPSEEK_API_KEY", ""),
                os.getenv("TROPOS_DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
                os.getenv("TROPOS_DEEPSEEK_MODEL", "deepseek-flash"),
                _timeout(os.getenv("TROPOS_LLM_TIMEOUT", "15")))), source="environment")
        self._environment_config("openai", "TROPOS_OPENAI", "https://api.openai.com/v1")
        self._environment_config("kimi", "TROPOS_KIMI", "https://api.moonshot.cn/v1", "kimi-k3")
        self._environment_config("qwen", "TROPOS_QWEN", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus")
        self._environment_config("custom", "TROPOS_CUSTOM_LLM")

    def _selected(self, user_id):
        with self.bank._db() as db:
            self.bank._user(db, user_id)
            row = db.execute("SELECT provider FROM model_preferences WHERE user_id=?", (user_id,)).fetchone()
        return row["provider"] if row else self.default_provider

    @staticmethod
    def _personal_id(provider_id):
        return "mine-" + provider_id

    @staticmethod
    def _personal_base(selection):
        return selection[5:] if isinstance(selection, str) and selection.startswith("mine-") else None

    def _personal_provider(self, user_id, provider_id):
        with self._personal_lock:
            return self._personal.get(user_id, {}).get(provider_id)

    def _resolve(self, user_id, selection):
        personal_id = self._personal_base(selection)
        if personal_id is not None:
            return self._personal_provider(user_id, personal_id)
        return self.providers.get(selection)

    def planner_for(self, user_id):
        selected = self._selected(user_id)
        provider = self._resolve(user_id, selected)
        return provider["planner"] if provider and provider["planner"] is not None else self.providers["rules"]["planner"]

    def status(self, user_id):
        selected = self._selected(user_id)
        chosen = self._resolve(user_id, selected)
        effective = selected if chosen and chosen["planner"] is not None else "rules"
        rows = []
        local_capabilities = {
            "text": "enabled", "structuredOutput": "enabled", "safeFunctions": "enabled",
            "multiTurn": "typed_context", "streaming": "not_enabled", "vision": "not_enabled",
            "fileInput": "not_enabled", "codeExecution": "not_enabled", "protocol": "local_rules",
            "reasoning": "deterministic", "webSearch": "not_enabled",
            "sourceCitations": "not_enabled", "context": "typed_only",
        }
        for provider_id, _label, _description in PROVIDER_DEFINITIONS:
            provider = self.providers[provider_id]
            rows.append({
                "id": provider_id, "label": provider["label"], "description": provider["description"],
                "configured": provider["planner"] is not None,
                "selected": provider_id == selected,
                "effective": provider_id == effective,
                "model": provider["model"], "host": provider["host"],
                "missing": list(provider["missing"]), "external": provider_id != "rules",
                "source": provider["source"],
                "capabilities": (local_capabilities if provider_id == "rules" else
                                 provider["planner"].capabilities() if provider["planner"] is not None else {}),
                "scope": "local" if provider_id == "rules" else "platform",
                "billing": "none" if provider_id == "rules" else "platform",
                "baseUrl": (provider["planner"].config.base_url
                            if self.allow_runtime_config and provider["planner"] is not None and provider_id != "rules" else None),
            })
        for provider_id, label, _description in PROVIDER_DEFINITIONS[1:]:
            provider = self._personal_provider(user_id, provider_id)
            rows.append({
                "id": self._personal_id(provider_id), "providerId": provider_id,
                "label": label, "description": "使用你自己的 API Key，仅供当前登录账户调用。",
                "configured": provider is not None,
                "selected": self._personal_id(provider_id) == selected,
                "effective": self._personal_id(provider_id) == effective,
                "model": provider["model"] if provider else None,
                "host": provider["host"] if provider else None,
                "missing": [] if provider else ["你的 API Key"], "external": True,
                "source": "personal_memory" if provider else None,
                "capabilities": provider["planner"].capabilities() if provider else {},
                "scope": "personal", "billing": "user",
                "baseUrl": provider["planner"].config.base_url if provider else None,
            })
        return {
            "version": "TROPOS-MODEL-CENTER-1", "selectedProvider": selected,
            "effectiveProvider": effective, "providers": rows,
            "canConfigure": self.allow_runtime_config,
            "canConfigurePersonal": True,
            "configuration": {
                "scope": "process" if self.allow_runtime_config else "server_admin",
                "message": ("平台模型可由本机管理员配置；你也可以接入自己的 API Key。个人密钥仅在当前服务进程中按账户隔离保存，重启后自动清除。"
                            if self.allow_runtime_config else
                            "平台模型由管理员在服务器配置；你也可以接入自己的 API Key，调用费用由你的模型账户承担。个人密钥仅在当前服务进程中按账户隔离保存。"),
            },
            "privacy": {
                "sent": "普通办理只发送当前消息；请求生活或财务方案时，经你授权后还会发送服务端生成的脱敏汇总。明确要求联网时，会调用当前供应商的官方搜索能力，并把来源摘要交给所选模型归纳。",
                "notSent": "不发送原始流水备注、商户明细、收款账号、会话令牌、用户标识或 API Key。",
                "boundary": "模型可以解释意图、检索网页并提出方案；网页结果不能直接触发操作，金额上限、执行前核验与逐笔确认仍由 TROPOS 服务端完成。",
            },
        }

    @staticmethod
    def _validate_config(api_key, base_url, model, timeout):
        if not isinstance(api_key, str) or not 8 <= len(api_key.strip()) <= 4096:
            raise BankError(400, "INVALID_MODEL_KEY", "API Key 格式不正确。")
        if not isinstance(base_url, str) or not 8 <= len(base_url.strip()) <= 500:
            raise BankError(400, "INVALID_MODEL_URL", "请填写完整的模型接口地址。")
        base_url = base_url.strip().rstrip("/")
        # Accept either an API root or a full OpenAI-compatible method URL.
        # Persisting the root avoids accidentally requesting
        # /chat/completions/chat/completions later.
        for suffix in ("/chat/completions", "/responses"):
            if base_url.endswith(suffix):
                base_url = base_url[:-len(suffix)]
                break
        parsed = urllib.parse.urlsplit(base_url)
        loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if (parsed.scheme != "https" and not (parsed.scheme == "http" and loopback)) or not parsed.hostname:
            raise BankError(400, "INVALID_MODEL_URL", "模型接口必须使用 HTTPS；本机兼容服务可使用回环 HTTP。")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise BankError(400, "INVALID_MODEL_URL", "模型接口地址不能包含账号、查询参数或片段。")
        if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9._:/-]{1,128}", model.strip()):
            raise BankError(400, "INVALID_MODEL_NAME", "模型名格式不正确。")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 1 <= timeout <= 60:
            raise BankError(400, "INVALID_MODEL_TIMEOUT", "超时时间须为 1 至 60 秒。")
        return api_key.strip(), base_url, model.strip(), float(timeout)

    def configure(self, user_id, provider_id, payload):
        if not self.allow_runtime_config:
            raise BankError(403, "MODEL_ADMIN_DISABLED", "当前站点未开放网页模型配置，请联系服务器管理员。")
        if provider_id not in CONFIGURABLE_PROVIDER_IDS:
            raise BankError(400, "INVALID_MODEL_PROVIDER", "请选择可配置的外部模型服务。")
        api_key, base_url, model, timeout = self._validate_config(
            payload.get("apiKey"), payload.get("baseUrl"), payload.get("model"), payload.get("timeout"))
        self._install(provider_id, IntentPlanner(ModelConfig(api_key, base_url, model, timeout)), source="runtime")
        with self.bank._db(True) as db:
            self.bank._user(db, user_id)
            self.bank._audit(db, user_id, "METIS", "更新模型服务配置",
                             f"已在当前服务进程配置 {self.providers[provider_id]['label']}；未记录 API Key")
        return self.status(user_id)

    def configure_personal(self, user_id, provider_id, payload):
        if provider_id not in CONFIGURABLE_PROVIDER_IDS:
            raise BankError(400, "INVALID_MODEL_PROVIDER", "请选择可配置的外部模型服务。")
        api_key, base_url, model, timeout = self._validate_config(
            payload.get("apiKey"), payload.get("baseUrl"), payload.get("model"), payload.get("timeout"))
        planner = IntentPlanner(ModelConfig(api_key, base_url, model, timeout))
        parsed = urllib.parse.urlsplit(base_url)
        entry = {
            "id": self._personal_id(provider_id), "label": self.providers[provider_id]["label"],
            "description": "使用你自己的 API Key，仅供当前登录账户调用。",
            "planner": planner, "model": model, "host": parsed.hostname or "自定义地址",
            "missing": [], "source": "personal_memory",
        }
        with self._personal_lock:
            self._personal.setdefault(user_id, {})[provider_id] = entry
        with self.bank._db(True) as db:
            self.bank._user(db, user_id)
            self.bank._audit(db, user_id, "METIS", "接入个人模型服务",
                             f"已在当前服务进程为本账户接入 {entry['label']}；未记录 API Key")
        return self.status(user_id)

    def delete_personal(self, user_id, provider_id):
        if provider_id not in CONFIGURABLE_PROVIDER_IDS:
            raise BankError(400, "INVALID_MODEL_PROVIDER", "请选择可配置的外部模型服务。")
        with self._personal_lock:
            owned = self._personal.get(user_id, {})
            existed = owned.pop(provider_id, None) is not None
            if not owned:
                self._personal.pop(user_id, None)
        selection = self._personal_id(provider_id)
        with self.bank._db(True) as db:
            self.bank._user(db, user_id)
            current = db.execute("SELECT provider FROM model_preferences WHERE user_id=?", (user_id,)).fetchone()
            if current and current["provider"] == selection:
                db.execute("UPDATE model_preferences SET provider=?,updated_at=? WHERE user_id=?",
                           ("rules", now(), user_id))
            if existed:
                self.bank._audit(db, user_id, "METIS", "移除个人模型服务",
                                 f"已从当前服务进程移除本账户的 {self.providers[provider_id]['label']} 密钥")
        return self.status(user_id)

    def test(self, user_id, provider_id):
        if not self.allow_runtime_config:
            raise BankError(403, "MODEL_ADMIN_DISABLED", "当前站点未开放网页模型测试，请联系服务器管理员。")
        self._selected(user_id)
        provider = self.providers.get(provider_id)
        if not provider or provider_id == "rules":
            raise BankError(400, "INVALID_MODEL_PROVIDER", "请选择一个外部模型服务。")
        if provider["planner"] is None:
            raise BankError(409, "MODEL_PROVIDER_NOT_CONFIGURED", "请先配置该模型服务。")
        result = provider["planner"].answer_conversation("请只用一句话说明你能提供哪些只读帮助。", [])
        ok = result.get("mode") == "llm_chat"
        message, reason = _test_failure_message(result) if not ok else ("连接与自由对话检查均已通过。", None)
        return {"ok": ok, "provider": provider_id, "mode": result.get("mode"),
                "reason": reason, "message": message}

    def test_personal(self, user_id, provider_id):
        if provider_id not in CONFIGURABLE_PROVIDER_IDS:
            raise BankError(400, "INVALID_MODEL_PROVIDER", "请选择一个外部模型服务。")
        provider = self._personal_provider(user_id, provider_id)
        if provider is None:
            raise BankError(409, "MODEL_PROVIDER_NOT_CONFIGURED", "请先接入你自己的模型服务。")
        result = provider["planner"].answer_conversation("请只用一句话说明你能提供哪些只读帮助。", [])
        ok = result.get("mode") == "llm_chat"
        message, reason = _test_failure_message(result, personal=True) if not ok else ("个人模型连接与自由对话检查均已通过。", None)
        return {"ok": ok, "provider": self._personal_id(provider_id), "mode": result.get("mode"),
                "reason": reason, "message": message}

    def decorate(self, user_id, result):
        selected = self._selected(user_id)
        chosen = self._resolve(user_id, selected)
        effective = selected if chosen and chosen["planner"] is not None else "rules"
        provider = self._resolve(user_id, effective)
        used = str(result.get("mode", "")).startswith("llm")
        failure_reason, failure_message = _runtime_failure(result) if not used else (None, None)
        result["modelRuntime"] = {
            "selectedProvider": selected, "effectiveProvider": effective,
            "label": provider["label"], "model": provider["model"],
            "scope": "personal" if self._personal_base(effective) is not None else ("local" if effective == "rules" else "platform"),
            "usedForThisMessage": used,
        }
        if failure_reason:
            result["modelRuntime"].update(
                failureReason=failure_reason, failureMessage=failure_message)
        return result

    def select(self, user_id, provider_id):
        if not isinstance(provider_id, str):
            raise BankError(400, "INVALID_MODEL_PROVIDER", "请选择模型中心列出的服务。")
        provider = self._resolve(user_id, provider_id)
        if provider is None:
            personal_id = self._personal_base(provider_id)
            message = "请先接入你自己的模型 API Key。" if personal_id is not None else "该平台模型尚未由管理员配置。"
            raise BankError(409, "MODEL_PROVIDER_NOT_CONFIGURED", message)
        if provider["planner"] is None:
            raise BankError(409, "MODEL_PROVIDER_NOT_CONFIGURED", "该模型尚未由服务端管理员配置，不能选择。",
                            {"missing": list(provider["missing"])})
        with self.bank._db(True) as db:
            self.bank._user(db, user_id)
            current = db.execute("SELECT provider FROM model_preferences WHERE user_id=?", (user_id,)).fetchone()
            if not current or current["provider"] != provider_id:
                db.execute("INSERT INTO model_preferences(user_id,provider,updated_at) VALUES(?,?,?) "
                           "ON CONFLICT(user_id) DO UPDATE SET provider=excluded.provider,updated_at=excluded.updated_at",
                           (user_id, provider_id, now()))
                self.bank._audit(db, user_id, "METIS", "切换意图理解服务",
                                 f"用户选择 {provider['label']}；只影响后续新消息的理解，不授予金融操作权限")
            snapshot = self.bank._snapshot(db, user_id)
        result = self.status(user_id)
        result["snapshot"] = snapshot
        return result
