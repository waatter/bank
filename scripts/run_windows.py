"""Persistent, windowless launcher for the local Windows demo service."""
from __future__ import annotations

import contextlib
import os
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
LOCAL = ROOT / ".local"
LOCAL.mkdir(parents=True, exist_ok=True)
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

MODEL_ENV = {
    "TROPOS_LLM_API_KEY", "DEEPSEEK_API_KEY", "TROPOS_LLM_BASE_URL", "TROPOS_LLM_MODEL", "TROPOS_LLM_TIMEOUT",
    "TROPOS_DEEPSEEK_API_KEY", "TROPOS_DEEPSEEK_BASE_URL", "TROPOS_DEEPSEEK_MODEL", "TROPOS_DEEPSEEK_TIMEOUT",
    "TROPOS_OPENAI_API_KEY", "TROPOS_OPENAI_BASE_URL", "TROPOS_OPENAI_MODEL", "TROPOS_OPENAI_TIMEOUT",
    "TROPOS_KIMI_API_KEY", "TROPOS_KIMI_BASE_URL", "TROPOS_KIMI_MODEL", "TROPOS_KIMI_TIMEOUT",
    "TROPOS_QWEN_API_KEY", "TROPOS_QWEN_BASE_URL", "TROPOS_QWEN_MODEL", "TROPOS_QWEN_TIMEOUT",
    "TROPOS_CUSTOM_LLM_API_KEY", "TROPOS_CUSTOM_LLM_BASE_URL", "TROPOS_CUSTOM_LLM_MODEL", "TROPOS_CUSTOM_LLM_TIMEOUT",
    "TROPOS_AUTH_MASTER_KEY", "TROPOS_PUBLIC_URL", "TROPOS_SITE_ADDRESS",
    "TROPOS_SMTP_HOST", "TROPOS_SMTP_PORT", "TROPOS_SMTP_USERNAME", "TROPOS_SMTP_PASSWORD",
    "TROPOS_SMTP_FROM", "TROPOS_SMTP_SSL", "TROPOS_SMTP_STARTTLS",
}


def load_model_environment(path: Path) -> None:
    """Read only the documented model variables; never execute .env as code."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)", line)
        if not match or match.group(1) not in MODEL_ENV:
            continue
        key, value = match.groups()
        if value.startswith(("'", '"')):
            quote = value[0]
            end = value.find(quote, 1)
            if end < 0 or (value[end + 1:].strip() and not value[end + 1:].lstrip().startswith("#")):
                raise SystemExit(".env 的引号格式不正确，请检查本地配置。")
            value = value[1:end]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        os.environ.setdefault(key, value)


load_model_environment(ROOT / ".env")

from backend.server import main  # noqa: E402


if __name__ == "__main__":
    with (LOCAL / "server.out.log").open("a", encoding="utf-8") as output, \
            (LOCAL / "server.err.log").open("a", encoding="utf-8") as errors, \
            contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
        lan_config = LOCAL / "lan-access.json"
        arguments = ["--host", "0.0.0.0" if lan_config.is_file() else "127.0.0.1",
                     "--port", "8765", "--db", str(LOCAL / "tropos.sqlite3")]
        if lan_config.is_file():
            arguments += ["--lan-config", str(lan_config)]
        else:
            arguments += ["--model-admin"]
        main(arguments)
