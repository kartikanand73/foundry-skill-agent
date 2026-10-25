"""Command-line chat. Usage: python cli.py   (type /reset to clear history, /quit to exit)"""
from __future__ import annotations

import sys
import uuid

from agent import SkillAgent


def main() -> None:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    agent = SkillAgent()
    session = str(uuid.uuid4())
    mode = "OFFLINE scripted model" if agent.s.fake_model else f"model deployment '{agent.s.model}'"
    print(f"Skill agent ready ({mode}). Ask about HR policies or IT tickets. /reset, /quit\n")
    while True:
        try:
            msg = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not msg:
            continue
        if msg in ("/quit", "/exit"):
            break
        if msg == "/reset":
            agent.reset(session)
            print("(history cleared)\n")
            continue
        reply = agent.handle(session, msg)
        print(f"agent> {reply.text}\n")


if __name__ == "__main__":
    main()
