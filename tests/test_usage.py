"""Per-question token usage and cost."""
from __future__ import annotations

import dataclasses
from types import SimpleNamespace as NS

from agent import SkillAgent
from agent.config import Settings
from agent.fake_llm import FakeClient
from agent.usage import Usage


def test_cost_math_default_gpt5_rates(monkeypatch):
    for k in ("MODEL_PRICE_INPUT_PER_1M", "MODEL_PRICE_CACHED_INPUT_PER_1M", "MODEL_PRICE_OUTPUT_PER_1M"):
        monkeypatch.delenv(k, raising=False)
    u = Usage()
    u.add(NS(usage=NS(prompt_tokens=4000, completion_tokens=1000,
                      prompt_tokens_details=NS(cached_tokens=1000),
                      completion_tokens_details=NS(reasoning_tokens=600))))
    # 3000 uncached * 1.25 + 1000 cached * 0.125 + 1000 out * 10  (per 1M)
    assert u.cost_usd == round((3000 * 1.25 + 1000 * 0.125 + 1000 * 10) / 1e6, 6)
    assert u.to_dict()["reasoning_tokens"] == 600 and u.model_calls == 1


def test_rates_from_env(monkeypatch):
    monkeypatch.setenv("MODEL_PRICE_INPUT_PER_1M", "0.25")
    monkeypatch.setenv("MODEL_PRICE_OUTPUT_PER_1M", "2")
    u = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    assert u.cost_usd == 2.25


def test_missing_usage_counts_call_only():
    u = Usage()
    u.add(NS(usage=None))
    assert u.model_calls == 1 and u.cost_usd == 0


def test_reply_carries_usage_for_whole_question():
    a = SkillAgent(dataclasses.replace(Settings.from_env(), fake_model=True), client=FakeClient(),
                   salesforce=False, snowflake=False)
    r = a.handle("s", "What's the status of INC1001?")
    # router + tool request + final answer = 3 calls x (1000 in, 200 out) in the fake model
    assert r.usage["model_calls"] == 3
    assert r.usage["input_tokens"] == 3000 and r.usage["output_tokens"] == 600
    assert r.usage["cost_usd"] > 0
    # a second question starts from zero (not cumulative)
    assert a.handle("s", "And INC1002?").usage["model_calls"] == 3
