"""Per-question model usage and cost.

Every chat.completions response carries exact token counts (`usage`). The agent adds them up for
one user question (router call + skill calls) and prices them with per-1M-token rates.

Rates come from env so they can follow your deployment and Azure price changes. Defaults are the
gpt-5 Global Standard list prices (USD): input $1.25, cached input $0.125, output $10.00 per 1M.
Reasoning tokens are billed as output and are already included in completion_tokens.

Not included: Snowflake credits, Salesforce, hosted-agent compute, APIM, App Service.
"""
from __future__ import annotations

import contextvars
import os
from dataclasses import asdict, dataclass


def _rate(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class Usage:
    model_calls: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0

    def add(self, resp) -> None:
        u = getattr(resp, "usage", None)
        self.model_calls += 1
        if u is None:
            return
        self.input_tokens += u.prompt_tokens or 0
        self.output_tokens += u.completion_tokens or 0
        pd = getattr(u, "prompt_tokens_details", None)
        cd = getattr(u, "completion_tokens_details", None)
        self.cached_input_tokens += (getattr(pd, "cached_tokens", 0) or 0) if pd else 0
        self.reasoning_tokens += (getattr(cd, "reasoning_tokens", 0) or 0) if cd else 0

    @property
    def cost_usd(self) -> float:
        pin = _rate("MODEL_PRICE_INPUT_PER_1M", 1.25)
        pcached = _rate("MODEL_PRICE_CACHED_INPUT_PER_1M", 0.125)
        pout = _rate("MODEL_PRICE_OUTPUT_PER_1M", 10.0)
        uncached = max(self.input_tokens - self.cached_input_tokens, 0)
        return round((uncached * pin + self.cached_input_tokens * pcached + self.output_tokens * pout) / 1e6, 6)

    def to_dict(self) -> dict:
        return {**asdict(self), "cost_usd": self.cost_usd}


# The Usage for the question currently being handled (set per turn; copied into worker threads).
current: contextvars.ContextVar[Usage | None] = contextvars.ContextVar("skill_agent_usage", default=None)


def record(resp):
    u = current.get()
    if u is not None:
        u.add(resp)
    return resp
