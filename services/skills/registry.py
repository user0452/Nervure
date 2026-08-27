"""Validated, searchable registry for reusable Nervure skills."""

from __future__ import annotations

from dataclasses import dataclass

from services.skills.types import SkillCommand


@dataclass(frozen=True)
class SkillValidation:
    ok: bool
    message: str | None = None


class SkillRegistry:
    """In-memory skill capability index.

    Filesystem loaders remain responsible for discovery and precedence. This
    object gives runtime composition and callers a deterministic API for
    listing, searching, loading, and validating the resulting capabilities.
    """

    def __init__(self, skills: tuple[SkillCommand, ...] = ()) -> None:
        self._skills: dict[str, SkillCommand] = {}
        for skill in skills:
            self.register(skill)

    def register(self, skill: SkillCommand) -> None:
        validation = self.validate(skill)
        if not validation.ok:
            raise ValueError(validation.message)
        if skill.name in self._skills:
            raise ValueError(f"Skill already registered: {skill.name}")
        self._skills[skill.name] = skill

    def list_skills(self) -> tuple[SkillCommand, ...]:
        return tuple(self._skills[name] for name in sorted(self._skills))

    def search_skills(self, query: str) -> tuple[SkillCommand, ...]:
        terms = tuple(part.lower() for part in query.split() if part.strip())
        if not terms:
            return self.list_skills()
        return tuple(
            skill
            for skill in self.list_skills()
            if all(
                term in " ".join(
                    (skill.name, skill.description, skill.when_to_use or "")
                ).lower()
                for term in terms
            )
        )

    def load_skill(self, name: str) -> SkillCommand | None:
        return self._skills.get(name.strip().lstrip("/"))

    @staticmethod
    def validate(skill: SkillCommand) -> SkillValidation:
        if not skill.name or any(char.isspace() for char in skill.name):
            return SkillValidation(False, "Skill name must be a non-empty command name.")
        if not skill.description.strip():
            return SkillValidation(False, f"Skill {skill.name} is missing a description.")
        if not skill.content.strip():
            return SkillValidation(False, f"Skill {skill.name} is empty.")
        if skill.context not in {"inline", "fork"}:
            return SkillValidation(False, f"Skill {skill.name} has unsupported context.")
        return SkillValidation(True)
