"""Skill Loader — Doc §3.

Parses SKILL.md files (YAML frontmatter + Markdown body), exposes a
SkillRegistry with hot-reload support (POST /skills/reload, Doc §3.3).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import frontmatter

logger = logging.getLogger(__name__)


@dataclass
class Skill:
    """A single SKILL.md file parsed into a Python object (Doc §3.2)."""
    name: str
    description: str
    tools: list[str] = field(default_factory=list)
    body: str = ""
    file_path: Path | None = None

    @classmethod
    def from_file(cls, path: Path) -> "Skill":
        post = frontmatter.load(path)
        meta = post.metadata
        name = meta.get("name")
        if not name:
            raise ValueError(f"SKILL.md missing 'name': {path}")
        return cls(
            name=name,
            description=(meta.get("description") or "").strip(),
            tools=list(meta.get("tools") or []),
            body=post.content.strip(),
            file_path=path,
        )


class SkillRegistry:
    """Loads every SKILL.md file from a directory. Supports hot reload."""

    def __init__(self, skills_dir: Path):
        self.skills_dir = Path(skills_dir)
        self._skills: dict[str, Skill] = {}

    def load_all(self) -> dict[str, Skill]:
        self._skills.clear()
        if not self.skills_dir.exists():
            logger.warning("[skills] directory not found: %s", self.skills_dir)
            return {}
        for path in sorted(self.skills_dir.glob("*.md")):
            try:
                skill = Skill.from_file(path)
                self._skills[skill.name] = skill
                logger.info("[skills] loaded %s (%d tools)", skill.name, len(skill.tools))
            except Exception as e:
                logger.error("[skills] failed to parse %s: %s", path.name, e)
        return self._skills

    def reload(self) -> int:
        """Hot reload — implements the 'golden rule' from Doc §3.3."""
        self.load_all()
        return len(self._skills)

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def list_all(self) -> list[Skill]:
        return list(self._skills.values())

    def names(self) -> list[str]:
        return list(self._skills.keys())
