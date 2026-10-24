"""OFFLINE SCRIPTED MODEL - for tests and demos without Azure access only.

It mimics the OpenAI chat.completions API and returns real openai response types, so
the agent runtime exercises exactly the same code path (structured routing, function
calling, tool results) as with Azure. Its *decisions* are crude scripted rules - that is
fine for a test double, but it is NOT how routing works with the real model.
Enable with SKILL_AGENT_FAKE_MODEL=1.
"""
from __future__ import annotations

import json
import re
import time
import uuid

from openai.types.chat import ChatCompletion


def _completion(message: dict) -> ChatCompletion:
    return ChatCompletion.model_validate({
        "id": "fake-" + uuid.uuid4().hex[:8], "object": "chat.completion",
        "created": int(time.time()), "model": "fake-scripted",
        "choices": [{"index": 0, "finish_reason": "tool_calls" if message.get("tool_calls") else "stop",
                     "message": {"role": "assistant", **message}}],
    })


def _text(messages, role) -> str:
    return " ".join(m.get("content") or "" for m in messages if m.get("role") == role)


class _Completions:
    def __init__(self):
        self.calls: list[dict] = []   # recorded requests, so tests can inspect prompts

    def create(self, *, model, messages, response_format=None, tools=None, tool_choice=None, **_):
        self.calls.append({"messages": messages, "tools": tools, "response_format": response_format})
        if response_format:
            return self._route(messages)
        return self._skill(messages, tools or [], tool_choice)

    # Router: pick a skill from the catalog it was given.
    def _route(self, messages):
        last = messages[-1]["content"].lower()
        context = _text(messages[1:-1], "assistant").lower()
        hr = any(w in last for w in ("vacation", "pto", "holiday", "time off", "leave", "policy"))
        it = any(w in last for w in ("ticket", "inc", "incident")) or (
            re.search(r"\binc\d+", last) or ("ticket id" in context and re.search(r"\d{3,}", last)))
        sn = any(w in last for w in ("snowflake", "cortex", "revenue", "claims", "pmpm", "metric"))
        if sn and not (hr or it):
            return _completion({"content": json.dumps({"skill": "snowflake-cortex", "reason": "scripted rule",
                                                       "clarification": ""})})
        sf = any(w in last for w in ("salesforce", "account", "opportunit", "pipeline", "agentforce", "crm"))
        if sf and not (hr or it):
            return _completion({"content": json.dumps({"skill": "salesforce-agentforce", "reason": "scripted rule",
                                                       "clarification": ""})})
        if hr and it:
            pick, q = "clarify", "Do you want help with an HR policy or with an IT ticket first?"
        elif hr:
            pick, q = "hr-policy", ""
        elif it:
            pick, q = "it-ticket", ""
        else:
            pick, q = "unsupported", ""
        return _completion({"content": json.dumps({"skill": pick, "reason": "scripted rule", "clarification": q})})

    # Skill execution: call the one available tool, then answer from its result.
    def _skill(self, messages, tools, tool_choice):
        system = messages[0]["content"]
        sign_off = re.search(r'End every answer with: "([^"]+)"', system)
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        tool_names = [t["function"]["name"] for t in tools]

        if tool_msgs:
            answer = self._answer(json.loads(tool_msgs[-1]["content"]))
        elif "get_ticket_status" in tool_names and tool_choice != "none":
            m = re.findall(r"\bINC\d+\b", _text(messages, "user").upper())
            if not m:
                answer = "Sure - what is your ticket ID? It looks like INC1001."
            else:
                return self._call("get_ticket_status", {"ticket_id": m[-1]})
        elif "ask_cortex_agent" in tool_names and tool_choice != "none":
            return self._call("ask_cortex_agent", {"question": messages[-1]["content"]})
        elif "ask_agentforce" in tool_names and tool_choice != "none":
            return self._call("ask_agentforce", {"question": messages[-1]["content"]})
        elif "lookup_policy" in tool_names and tool_choice != "none":
            last = messages[-1]["content"].lower()
            topic = "holiday" if "holiday" in last else "vacation"
            return self._call("lookup_policy", {"topic": topic})
        else:
            answer = "I don't have enough information to answer."
        if sign_off:
            answer += f"\n\n{sign_off.group(1)}"
        return _completion({"content": answer})

    def _call(self, name, args):
        return _completion({"content": None, "tool_calls": [{
            "id": "call_" + uuid.uuid4().hex[:8], "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}]})

    @staticmethod
    def _answer(r: dict) -> str:
        if "error" in r:
            if r["error"].startswith("Snowflake"):
                return f"Snowflake is unavailable right now ({r['error']})"
            if "Salesforce" in r["error"]:
                return f"Salesforce is unavailable right now ({r['error']})"
            return f"I couldn't complete the lookup: {r['error']}"
        if str(r.get("source", "")).startswith("Snowflake"):
            extra = f" (SQL: {r['sql'][0]})" if r.get("sql") else ""
            return f"From Snowflake: {r['answer'] or 'no data returned.'}{extra}"
        if "source" in r and "answer" in r:
            if not r.get("content_safe", True):
                return "From Salesforce: the response was withheld by Salesforce safety checks."
            return f"From Salesforce: {r['answer'] or 'no matching data was found.'}"
        if "ticket_id" in r:
            if not r.get("found"):
                return f"I couldn't find a ticket with ID {r['ticket_id']}. Please double-check the ID."
            return (f"{r['ticket_id']} - {r['summary']}\nStatus: {r['status']}\n"
                    f"Latest update: {r['latest_update']}")
        if not r.get("found"):
            return "I couldn't find a policy on that topic. Please contact HR."
        p = r["results"][0]
        return f"{p['text']}\n\n(Source: {p['title']}, {p['source_id']})"


class _Chat:
    def __init__(self):
        self.completions = _Completions()


class FakeClient:
    def __init__(self):
        self.chat = _Chat()
