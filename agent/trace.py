"""Concise development traces on stderr. Never pass credentials or tokens in here."""
from __future__ import annotations

import json
import os
import sys

ENABLED = os.environ.get("SKILL_AGENT_TRACE", "1").lower() not in ("0", "false", "no")


def trace(event: str, **fields) -> None:
    if not ENABLED:
        return
    parts = []
    for k, v in fields.items():
        s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        if len(s) > 160:
            s = s[:157] + "..."
        parts.append(f"{k}={s}")
    print(f"[trace] {event:<14} " + " ".join(parts), file=sys.stderr, flush=True)
