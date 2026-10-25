"""Streamlit chat UI for the skill agent: threads in the sidebar, each thread = one hosted session.

Browser -> (Entra sign-in, App Service) -> this app -> APIM -> Foundry hosted agent.
Threads live in this browser session's memory (st.session_state).
"""
from __future__ import annotations

import time
import uuid

import streamlit as st

from apim_client import ApimClient, ApimError

SKILL_BADGES = {
    "hr-policy": ("HR policy", ":blue"),
    "it-ticket": ("IT ticket", ":orange"),
    "salesforce-agentforce": ("Salesforce", ":violet"),
    "snowflake-cortex": ("Snowflake", ":green"),
    "clarify": ("Clarifying", ":gray"),
    "unsupported": ("Out of scope", ":gray"),
    "error": ("Error", ":red"),
}

st.set_page_config(page_title="Skill Agent", page_icon="💬", layout="wide")


# ---------- state ----------
def _new_thread() -> str:
    tid = uuid.uuid4().hex[:12]
    st.session_state.threads[tid] = {"title": "New chat", "session_id": None, "messages": [],
                                     "created": time.time()}
    st.session_state.active = tid
    return tid


if "threads" not in st.session_state:
    st.session_state.threads = {}
    _new_thread()


@st.cache_resource
def _client() -> ApimClient | None:
    try:
        return ApimClient.from_env()
    except ApimError:
        return None


def _signed_in_user() -> str | None:
    try:  # App Service authentication injects this header after Entra sign-in
        return st.context.headers.get("X-Ms-Client-Principal-Name")
    except Exception:
        return None


def _badge(skill: str) -> str:
    label, color = SKILL_BADGES.get(skill, (skill or "agent", ":gray"))
    return f"{color}-badge[{label}]"


# ---------- sidebar: threads ----------
with st.sidebar:
    st.title("💬 Skill Agent")
    user = _signed_in_user()
    if user:
        st.caption(f"Signed in as {user}")
    if st.button("➕ New chat", use_container_width=True):
        _new_thread()
        st.rerun()
    st.divider()
    st.caption("Threads")
    ordered = sorted(st.session_state.threads.items(), key=lambda kv: kv[1]["created"], reverse=True)
    for tid, t in ordered:
        c1, c2 = st.columns([0.82, 0.18])
        is_active = tid == st.session_state.active
        if c1.button(("▸ " if is_active else "") + t["title"], key=f"open_{tid}",
                     use_container_width=True, type="primary" if is_active else "secondary"):
            st.session_state.active = tid
            st.rerun()
        with c2.popover("⋯"):
            new_title = st.text_input("Rename", value=t["title"], key=f"title_{tid}")
            if st.button("Save", key=f"save_{tid}"):
                t["title"] = new_title.strip() or t["title"]
                st.rerun()
            if st.button("Delete", key=f"del_{tid}"):
                del st.session_state.threads[tid]
                if not st.session_state.threads:
                    _new_thread()
                elif st.session_state.active == tid:
                    st.session_state.active = max(st.session_state.threads,
                                                  key=lambda k: st.session_state.threads[k]["created"])
                st.rerun()
    st.divider()
    st.caption("Ask about HR policies, IT tickets, Salesforce accounts or Snowflake claims metrics.")


# ---------- main: active thread ----------
thread = st.session_state.threads[st.session_state.active]
st.subheader(thread["title"])

for m in thread["messages"]:
    with st.chat_message(m["role"]):
        if m["role"] == "assistant" and m.get("skill"):
            st.markdown(_badge(m["skill"]))
        st.markdown(m["content"])
        if m.get("tool_calls"):
            with st.expander("Details"):
                st.json(m["tool_calls"])

prompt = st.chat_input("Ask the agent…")
if prompt:
    if not thread["messages"]:
        thread["title"] = (prompt[:40] + "…") if len(prompt) > 40 else prompt
    thread["messages"].append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    client = _client()
    with st.chat_message("assistant"):
        if client is None:
            msg = {"role": "assistant", "content": "The app isn't configured to reach the agent yet.",
                   "skill": "error"}
        else:
            with st.spinner("Thinking…"):
                try:
                    res = client.chat(prompt, session_id=thread["session_id"], user=_signed_in_user())
                    thread["session_id"] = res.session_id
                    msg = {"role": "assistant", "content": res.text or "_(no answer)_", "skill": res.skill,
                           "tool_calls": res.tool_calls}
                except ApimError as e:
                    msg = {"role": "assistant", "content": f"⚠️ {e}", "skill": "error"}
        st.markdown(_badge(msg["skill"]))
        st.markdown(msg["content"])
        if msg.get("tool_calls"):
            with st.expander("Details"):
                st.json(msg["tool_calls"])
    thread["messages"].append(msg)
    st.rerun()
