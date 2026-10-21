"""Runs the required demo cases plus the skill hot-reload demo.

    python scripts/demo.py            # against your Azure model (uses .env / az login)
    SKILL_AGENT_FAKE_MODEL=1 python scripts/demo.py   # offline scripted model

The hot-reload step edits a *copy* of skills/ in a temp folder so your repo stays clean;
the agent object is NOT recreated between the two requests.
"""
from __future__ import annotations

import dataclasses
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent import SkillAgent  # noqa: E402
from agent.config import Settings  # noqa: E402


def say(agent: SkillAgent, sid: str, msg: str) -> None:
    print(f"\nyou>   {msg}", flush=True)
    r = agent.handle(sid, msg)
    print(f"agent> {r.text}", flush=True)


def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    skills = tmp / "skills"
    shutil.copytree(Settings.from_env().skills_dir, skills)
    agent = SkillAgent(dataclasses.replace(Settings.from_env(), skills_dir=skills))

    print("=== 1. HR policy ===");            say(agent, "s1", "What is our vacation policy?")
    print("\n=== 2. Ticket status ===");     say(agent, "s2", "What is the status of INC1001?")
    print("\n=== 3. Missing ID + follow-up ==="); say(agent, "s3", "Check my ticket.")
    say(agent, "s3", "It's INC1003")
    print("\n=== 4. Unknown ticket ===");    say(agent, "s4", "What's the status of INC9999?")
    print("\n=== 5. Unrelated ===");         say(agent, "s5", "What's the weather in Columbus?")
    print("\n=== 6. Needs both skills ==="); say(agent, "s6", "How many holidays do we get, and what's up with INC1002?")

    print("\n=== 7. Hot reload: edit it-ticket SKILL.md on disk (no restart) ===")
    f = skills / "it-ticket" / "SKILL.md"
    text = f.read_text().replace("version: 1.0.0", "version: 1.1.0")
    text += '6. End every answer with: "Need anything else? Reply with another ticket ID."\n'
    f.write_text(text)
    print(f"(edited {f.name}: version 1.1.0 + new rule 6)")
    say(agent, "s7", "What is the status of INC1002?")
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
