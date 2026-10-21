"""Skill packages on disk: skills/<name>/SKILL.md with YAML frontmatter.

Everything is read from disk per request, so editing a SKILL.md takes effect on the
next message without restarting or rebuilding anything.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


class SkillError(Exception):
    pass


@dataclass(frozen=True)
class CatalogEntry:
    name: str
    description: str


@dataclass(frozen=True)
class Skill:
    name: str
    version: str
    description: str
    allowed_tools: tuple[str, ...]
    instructions: str  # markdown body (frontmatter removed)


def _parse(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise SkillError(f"{path} has no frontmatter")
    try:
        _, fm, body = text.split("---", 2)
        meta = yaml.safe_load(fm) or {}
    except (ValueError, yaml.YAMLError) as e:
        raise SkillError(f"{path}: bad frontmatter ({e})") from e
    if not isinstance(meta, dict):
        raise SkillError(f"{path}: frontmatter must be a mapping")
    return meta, body.strip()


class SkillStore:
    def __init__(self, skills_dir: Path):
        self.skills_dir = skills_dir

    def catalog(self) -> list[CatalogEntry]:
        """Names and one-line descriptions only. Full instructions are NOT included."""
        entries = []
        for f in sorted(self.skills_dir.glob("*/SKILL.md")):
            try:
                meta, _ = _parse(f)
            except SkillError:
                continue  # a broken package is simply not offered
            name = str(meta.get("name") or f.parent.name)
            if name != f.parent.name:
                continue  # name must match folder; keeps loading unambiguous
            entries.append(CatalogEntry(name, str(meta.get("description", "")).strip()))
        return entries

    def load(self, name: str) -> Skill:
        """Load one skill's full package. `name` must already be validated against the catalog."""
        path = self.skills_dir / name / "SKILL.md"
        if not path.is_file():
            raise SkillError(f"skill '{name}' not found")
        meta, body = _parse(path)
        tools = meta.get("allowed_tools") or []
        if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
            raise SkillError(f"skill '{name}': allowed_tools must be a list of names")
        return Skill(
            name=name,
            version=str(meta.get("version", "unversioned")),
            description=str(meta.get("description", "")),
            allowed_tools=tuple(tools),
            instructions=body,
        )
