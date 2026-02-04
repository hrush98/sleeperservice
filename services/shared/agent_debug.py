"""
Tiny NDJSON logger for Cursor debug mode.

Writes one JSON object per line to: /home/hmrush/Desktop/sleeperservice/.cursor/debug.log

NOTE: Do not log secrets (API keys, tokens, PII).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

_DEBUG_LOG_PATH = Path("/home/hmrush/Desktop/sleeperservice/.cursor/debug.log")


def agent_log(
    *,
    location: str,
    message: str,
    data: dict[str, Any] | None = None,
    hypothesis_id: str = "H?",
    run_id: str = "pre-fix",
    session_id: str = "debug-session",
) -> None:
    payload = {
        "sessionId": session_id,
        "runId": run_id,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data or {},
        "timestamp": int(time.time() * 1000),
    }
    try:
        _DEBUG_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _DEBUG_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, separators=(",", ":")) + "\n")
    except (OSError, ValueError, TypeError):
        # Never let debug logging break runtime paths.
        return

