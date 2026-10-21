"""Real Python tools. Results come from the JSON files in data/, never from the model."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


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


# ---------- registry ----------

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
        run=lookup_policy,
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
        run=get_ticket_status,
    ),
}


def execute(name: str, raw_args: str, allowed: tuple[str, ...], data_dir: Path) -> tuple[dict, dict | None]:
    """Validate and run a tool call. Returns (result, validated_args). Errors become structured results."""
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
        return REGISTRY[name].run(data_dir, **clean), clean
    except (OSError, KeyError, json.JSONDecodeError) as e:
        return {"error": f"tool failed: {type(e).__name__}"}, clean
