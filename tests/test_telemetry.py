"""Spans emitted per turn (in-memory exporter; no Azure)."""
from __future__ import annotations

import dataclasses
import importlib

import pytest

otel_sdk = pytest.importorskip("opentelemetry.sdk.trace")
from opentelemetry import trace as otel  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter  # noqa: E402

EXPORTER = InMemorySpanExporter()
if not isinstance(otel.get_tracer_provider(), otel_sdk.TracerProvider):
    provider = otel_sdk.TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(EXPORTER))
    otel.set_tracer_provider(provider)


def test_turn_route_and_tool_spans(monkeypatch):
    import agent.trace as tr
    importlib.reload(tr)
    import agent.core as core
    monkeypatch.setattr(core, "span", tr.span)
    monkeypatch.setattr(core, "trace", tr.trace)
    from agent import SkillAgent
    from agent.config import Settings
    from agent.fake_llm import FakeClient
    EXPORTER.clear()
    a = SkillAgent(dataclasses.replace(Settings.from_env(), fake_model=True), client=FakeClient(),
                   salesforce=False, snowflake=False)
    r = a.handle("s1", "What's the status of INC1001?")
    spans = {s.name: s for s in EXPORTER.get_finished_spans()}
    assert {"skill_agent.turn", "skill_agent.route", "skill_agent.tool"} <= set(spans)
    turn = spans["skill_agent.turn"]
    assert turn.attributes["skill_agent.skill"] == r.skill == "it-ticket"
    assert turn.attributes["skill_agent.tools"] == "get_ticket_status"
    assert spans["skill_agent.tool"].parent.span_id == turn.context.span_id
    # message text is not recorded by default
    events = [e for s in spans.values() for e in s.events]
    assert not any("INC1001" in str(v) for e in events for v in e.attributes.values()
                   if e.name == "final_answer")
