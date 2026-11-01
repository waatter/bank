#!/bin/sh
set -eu
TASK_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$TASK_ROOT"
# Parse the project's .env as data; do not source/execute arbitrary shell content.
# Only model configuration variables are accepted. Existing process values win.
TROPOS_PYTHON=python3
if [ -x "$TASK_ROOT/.venv/bin/python" ]; then
  TROPOS_PYTHON="$TASK_ROOT/.venv/bin/python"
fi
exec "$TROPOS_PYTHON" - "$@" <<'PY'
import os
import pathlib
import re
import sys

allowed = {
    "TROPOS_LLM_API_KEY", "DEEPSEEK_API_KEY", "TROPOS_LLM_BASE_URL", "TROPOS_LLM_MODEL", "TROPOS_LLM_TIMEOUT",
    "TROPOS_DEEPSEEK_API_KEY", "TROPOS_DEEPSEEK_BASE_URL", "TROPOS_DEEPSEEK_MODEL", "TROPOS_DEEPSEEK_TIMEOUT",
    "TROPOS_OPENAI_API_KEY", "TROPOS_OPENAI_BASE_URL", "TROPOS_OPENAI_MODEL", "TROPOS_OPENAI_TIMEOUT",
    "TROPOS_KIMI_API_KEY", "TROPOS_KIMI_BASE_URL", "TROPOS_KIMI_MODEL", "TROPOS_KIMI_TIMEOUT",
    "TROPOS_CUSTOM_LLM_API_KEY", "TROPOS_CUSTOM_LLM_BASE_URL", "TROPOS_CUSTOM_LLM_MODEL", "TROPOS_CUSTOM_LLM_TIMEOUT",
}
path = pathlib.Path(".env")
if path.is_file():
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)", line)
        if not match or match[1] not in allowed:
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
os.execv(sys.executable, [sys.executable, "-m", "backend.server", *sys.argv[1:]])
PY
