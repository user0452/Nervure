from __future__ import annotations

from core.runtime_state import RuntimeState
from services.background_tasks import BackgroundTaskManager
from services.tools.registry import ToolRegistry
from tools.agent import descriptor as agent_descriptor
from ui.cli.app import _subagent_descriptors, _subagent_disabled


class _Runner:
    async def run(self, request):  # pragma: no cover - descriptor construction only
        raise AssertionError(request)


def _visible_agent_names(monkeypatch, tmp_path):
    manager = BackgroundTaskManager(workspace=tmp_path)
    registry = ToolRegistry(_subagent_descriptors(_Runner(), manager))
    state = RuntimeState()
    return (
        {descriptor.name for descriptor in registry.visible_descriptors(state)},
        registry.tool_schemas(state),
        registry.tool_prompt_sections(state),
    )


def test_subagent_off_hides_agent_from_schema_and_prompt(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("NERVURE_DISABLE_SUBAGENT", "1")

    assert _subagent_disabled() is True
    names, schemas, prompts = _visible_agent_names(monkeypatch, tmp_path)

    assert "agent" not in names
    assert all(schema["function"]["name"] != "agent" for schema in schemas)
    assert all("Delegate a bounded task" not in prompt for prompt in prompts)


def test_subagent_on_keeps_existing_agent_schema_and_prompt(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("NERVURE_DISABLE_SUBAGENT", raising=False)
    monkeypatch.delenv("NERVURE_DISABLE_AGENT_TOOL", raising=False)

    assert _subagent_disabled() is False
    names, schemas, prompts = _visible_agent_names(monkeypatch, tmp_path)

    assert "agent" in names
    assert any(schema["function"]["name"] == "agent" for schema in schemas)
    assert agent_descriptor(_Runner()).prompt in prompts


def test_agent_tool_alias_also_disables_registration(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("NERVURE_DISABLE_AGENT_TOOL", "true")
    monkeypatch.delenv("NERVURE_DISABLE_SUBAGENT", raising=False)

    assert _subagent_disabled() is True
    names, _, _ = _visible_agent_names(monkeypatch, tmp_path)
    assert "agent" not in names
