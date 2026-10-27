"""Persistent, windowless launcher for the local Windows demo service."""
from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
LOCAL = ROOT / ".local"
LOCAL.mkdir(parents=True, exist_ok=True)
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

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
        main(arguments)
