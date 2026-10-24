"""Real Python tools. Results come from the JSON files in data/, never from the model."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx


class ToolArgumentError(ValueError):
    pass


def _load(path: Path, key: str) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)[key]


# ---------- tool implementations ----------

def lookup_policy(data_dir: Path, topic: str) -> dict:
    words = set(re.findall(r"[a-z]+", topic.lower()))
    matches = []
    for p in _load(data_dir / "policies.json", "policies"):
        tags = {w for t in p["topics"] for w in t.split()}
        title = set(re.findall(r"[a-z]+", p["title"].lower()))
        if words & (tags | title):
            matches.append({"title": p["title"], "source_id": p["id"], "text": p["text"]})
    if not matches:
        return {"found": False, "topic": topic, "results": [],
                "message": "No policy matched this topic. Available topics: vacation/PTO, holidays."}
    return {"found": True, "topic": topic, "results": matches}


def get_ticket_status(data_dir: Path, ticket_id: str) -> dict:
    for t in _load(data_dir / "tickets.json", "tickets"):
        if t["ticket_id"] == ticket_id:
            return {"found": True, **t}
    return {"found": False, "ticket_id": ticket_id,
            "message": f"No ticket with ID {ticket_id} exists."}


def ask_agentforce(ctx: "ToolContext", question: str) -> dict:
    """Forward the question to the Salesforce Agentforce agent (real HTTPS call)."""
    if not ctx.salesforce:
        return {"error": "Salesforce is not configured for this agent "
                         "(set SALESFORCE_MY_DOMAIN, SALESFORCE_CLIENT_ID, SALESFORCE_AGENTFORCE_ID)."}
    from .salesforce import SalesforceError
    try:
        return {"found": True, **ctx.salesforce.ask(ctx.session_key, question)}
    except SalesforceError as e:
        return {"error": f"Salesforce: {e}"}
    except httpx.TimeoutException:
        return {"error": "Salesforce: no response in time."}
    except httpx.HTTPError as e:
        return {"error": f"Salesforce: could not connect ({type(e).__name__})."}


def ask_cortex_agent(ctx: "ToolContext", question: str) -> dict:
    """Forward the question to the Snowflake Cortex Agent (real HTTPS call)."""
    if not ctx.snowflake:
        return {"error": "Snowflake: not configured for this agent (set SNOWFLAKE_ACCOUNT_URL, "
                         "SNOWFLAKE_AGENT_DATABASE, SNOWFLAKE_AGENT_SCHEMA, SNOWFLAKE_AGENT_NAME)."}
    from .snowflake import SnowflakeError
    try:
        return {"found": True, **ctx.snowflake.ask(ctx.session_key, question)}
    except SnowflakeError as e:
        return {"error": f"Snowflake: {e}"}
    except httpx.TimeoutException:
        return {"error": "Snowflake: no response in time."}
    except httpx.HTTPError as e:
        return {"error": f"Snowflake: could not connect ({type(e).__name__})."}


# ---------- argument validation ----------

def _validate_topic(args: dict) -> dict:
    topic = args.get("topic")
    if not isinstance(topic, str) or not topic.strip():
        raise ToolArgumentError("'topic' must be a non-empty string")
    if len(topic) > 100:
        raise ToolArgumentError("'topic' is too long (max 100 chars)")
    return {"topic": topic.strip()}


def _validate_ticket(args: dict) -> dict:
    tid = args.get("ticket_id")
    if not isinstance(tid, str):
        raise ToolArgumentError("'ticket_id' must be a string")
    tid = tid.strip().upper()
    if not re.fullmatch(r"INC\d{4,8}", tid):
        raise ToolArgumentError("'ticket_id' must look like INC1001")
    return {"ticket_id": tid}


def _validate_question(args: dict) -> dict:
    q = args.get("question")
    if not isinstance(q, str) or not q.strip():
        raise ToolArgumentError("'question' must be a non-empty string")
    if len(q) > 2000:
        raise ToolArgumentError("'question' is too long (max 2000 chars)")
    return {"question": q.strip()}


# ---------- registry ----------

@dataclass(frozen=True)
class ToolContext:
    """Per-request context handed to tools. Never given to the model."""
    data_dir: Path
    session_key: str = ""
    salesforce: Any = None   # AgentforceClient | None
    snowflake: Any = None    # CortexAgentClient | None

@dataclass(frozen=True)
class ToolSpec:
    name: str
    schema: dict                      # OpenAI function-calling schema
    validate: Callable[[dict], dict]
    run: Callable[..., dict]


REGISTRY: dict[str, ToolSpec] = {
    "lookup_policy": ToolSpec(
        name="lookup_policy",
        schema={"type": "function", "function": {
            "name": "lookup_policy",
            "description": "Look up company HR policies by topic, e.g. 'vacation' or 'holiday'.",
            "strict": True,
            "parameters": {"type": "object", "additionalProperties": False, "required": ["topic"],
                           "properties": {"topic": {"type": "string", "description": "Short policy topic"}}},
        }},
        validate=_validate_topic,
        run=lambda ctx, **a: lookup_policy(ctx.data_dir, **a),
    ),
    "get_ticket_status": ToolSpec(
        name="get_ticket_status",
        schema={"type": "function", "function": {
            "name": "get_ticket_status",
            "description": "Get the status of an IT incident ticket by its ID, e.g. INC1001.",
            "strict": True,
            "parameters": {"type": "object", "additionalProperties": False, "required": ["ticket_id"],
                           "properties": {"ticket_id": {"type": "string", "description": "Ticket ID like INC1001"}}},
        }},
        validate=_validate_ticket,
        run=lambda ctx, **a: get_ticket_status(ctx.data_dir, **a),
    ),
    "ask_agentforce": ToolSpec(
        name="ask_agentforce",
        schema={"type": "function", "function": {
            "name": "ask_agentforce",
            "description": "Ask the company's Salesforce Agentforce agent a question about CRM data "
                           "(accounts, contacts, opportunities, cases). Returns the agent's answer.",
            "strict": True,
            "parameters": {"type": "object", "additionalProperties": False, "required": ["question"],
                           "properties": {"question": {"type": "string",
                               "description": "A complete, self-contained question including any names/IDs "
                                              "mentioned earlier in the conversation."}}},
        }},
        validate=_validate_question,
        run=ask_agentforce,
    ),
    "ask_cortex_agent": ToolSpec(
        name="ask_cortex_agent",
        schema={"type": "function", "function": {
            "name": "ask_cortex_agent",
            "description": "Ask the company's Snowflake Cortex Agent an analytics question about data in the "
                           "Snowflake warehouse. Returns its answer, the SQL it ran and result tables.",
            "strict": True,
            "parameters": {"type": "object", "additionalProperties": False, "required": ["question"],
                           "properties": {"question": {"type": "string",
                               "description": "A complete, self-contained analytics question including any "
                                              "filters, time periods or names mentioned earlier."}}},
        }},
        validate=_validate_question,
        run=ask_cortex_agent,
    ),
}


def execute(name: str, raw_args: str, allowed: tuple[str, ...], ctx: "ToolContext | Path") -> tuple[dict, dict | None]:
    """Validate and run a tool call. Returns (result, validated_args). Errors become structured results."""
    if isinstance(ctx, Path):
        ctx = ToolContext(data_dir=ctx)
    if name not in allowed or name not in REGISTRY:
        return {"error": f"tool '{name}' is not permitted for this skill"}, None
    try:
        args: Any = json.loads(raw_args or "{}")
        if not isinstance(args, dict):
            raise ToolArgumentError("arguments must be a JSON object")
        clean = REGISTRY[name].validate(args)
    except (json.JSONDecodeError, ToolArgumentError) as e:
        return {"error": f"invalid arguments: {e}"}, None
    try:
        return REGISTRY[name].run(ctx, **clean), clean
    except (OSError, KeyError, json.JSONDecodeError) as e:
        return {"error": f"tool failed: {type(e).__name__}"}, clean
