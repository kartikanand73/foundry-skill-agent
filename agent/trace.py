"""Traces: concise lines on stderr + OpenTelemetry spans/events (exported to Application Insights).

On Foundry Hosted Agents the protocol runtime already configures the OpenTelemetry exporter when the
project has Application Insights connected (APPLICATIONINSIGHTS_CONNECTION_STRING is injected), so
this module only *emits* spans. Locally, without opentelemetry or an exporter, spans are no-ops.

Never pass credentials or tokens in here. Message/answer text is not recorded on spans unless
SKILL_AGENT_TRACE_CONTENT=1.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys

ENABLED = os.environ.get("SKILL_AGENT_TRACE", "1").lower() not in ("0", "false", "no")
RECORD_CONTENT = os.environ.get("SKILL_AGENT_TRACE_CONTENT", "0").lower() in ("1", "true", "yes")
_CONTENT_FIELDS = {"text", "args", "reason"}   # may contain user/business content

try:
    from opentelemetry import trace as _otel
    _tracer = _otel.get_tracer("skill_agent")
except ImportError:  # pragma: no cover - opentelemetry is optional locally
    _otel = None
    _tracer = None


def _fmt(v, limit: int = 160) -> str:
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return s if len(s) <= limit else s[: limit - 3] + "..."


def trace(event: str, **fields) -> None:
    """stderr line + an event on the current span."""
    if _otel is not None:
        attrs = {f"skill_agent.{k}": _fmt(v, 256) for k, v in fields.items()
                 if RECORD_CONTENT or k not in _CONTENT_FIELDS}
        _otel.get_current_span().add_event(event, attributes=attrs)
    if not ENABLED:
        return
    parts = [f"{k}={_fmt(v)}" for k, v in fields.items()]
    print(f"[trace] {event:<14} " + " ".join(parts), file=sys.stderr, flush=True)


@contextlib.contextmanager
def span(name: str, **attrs):
    """Child span (no-op without opentelemetry). Yields a setter for more attributes."""
    if _tracer is None:
        yield lambda **_: None
        return
    with _tracer.start_as_current_span(name) as sp:
        def set_attrs(**kv):
            for k, v in kv.items():
                if v is not None:
                    sp.set_attribute(f"skill_agent.{k}", v if isinstance(v, (str, bool, int, float)) else _fmt(v))
        set_attrs(**attrs)
        yield set_attrs


def instrument_http() -> None:
    """Dependency spans for httpx (model, Salesforce, Snowflake calls) if the instrumentor is installed."""
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        HTTPXClientInstrumentor().instrument()
    except Exception:  # pragma: no cover
        pass
