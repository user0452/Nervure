"""Explicit capability profiles layered on top of the existing child runtime."""

from __future__ import annotations

from dataclasses import dataclass

from services.subagents.types import AgentDefinition


@dataclass(frozen=True)
class AgentProfile:
    name: str
    purpose: str
    system_prompt: str
    tools: tuple[str, ...]
    permission: str
    model_preference: str | None = None
    max_budget: int | None = None
    output_format: str = "concise findings"

    def to_definition(self) -> AgentDefinition:
        return AgentDefinition(
            agent_type=self.name,
            when_to_use=self.purpose,
            system_prompt=self.system_prompt,
            tools=self.tools,
            disallowed_tools=("agent",),
            max_turns=self.max_budget,
            model=self.model_preference,
            read_only=self.permission == "readonly",
        )


BUILT_IN_PROFILES: dict[str, AgentProfile] = {
    "ExploreAgent": AgentProfile(
        name="ExploreAgent", purpose="repository exploration", permission="readonly",
        tools=("read_file", "grep", "glob", "repo_map", "symbol_search"), max_budget=12,
        system_prompt="You are ExploreAgent. Inspect the repository read-only and return exact, concise findings with paths.",
    ),
    "ImplementAgent": AgentProfile(
        name="ImplementAgent", purpose="scoped implementation", permission="standard",
        tools=("read_file", "grep", "glob", "edit_file", "write_file", "bash"), max_budget=20,
        system_prompt="You are ImplementAgent. Make scoped changes, validate them, and report changed files and checks.",
    ),
    "ReviewAgent": AgentProfile(
        name="ReviewAgent", purpose="change review", permission="readonly",
        tools=("read_file", "grep", "glob", "bash"), max_budget=12,
        system_prompt="You are ReviewAgent. Review the available change set read-only and return prioritized actionable findings.",
    ),
}


def get_agent_profile(name: str | None) -> AgentProfile | None:
    return BUILT_IN_PROFILES.get(name or "")
