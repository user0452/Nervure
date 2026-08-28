from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from core.runtime_state import RuntimeState
from services.checkpoints import CheckpointRestoreError, CheckpointStore
from services.guard import SandboxBoundary, SandboxGuard
from services.hooks import HookEvent, HookRegistry
from services.memory.instruction_loader import InstructionMemoryLoader
from services.skills import SkillCommand, SkillRegistry
from services.subagents import get_agent_profile
from services.tools.registry import ToolRegistry
from services.tools.executor import RegistryToolExecutor
from services.tools.types import ToolCall, ToolCallClassification, ToolDescriptor, ToolExecutionResult, ToolRuntime, ToolTarget


def _descriptor(name: str, description: str, hint: str = "") -> ToolDescriptor:
    return ToolDescriptor(
        name=name,
        description=description,
        search_hint=hint,
        input_schema={"type": "object"},
        handler=lambda _input, runtime: ToolExecutionResult(runtime.tool_call_id, name, "ok"),
    )


def test_project_instruction_entrypoint_budget_and_trace(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    (workspace / ".nervure" / "rules").mkdir(parents=True)
    (workspace / ".nervure" / "instructions.md").write_text("root instruction " * 20, encoding="utf-8")
    (workspace / ".nervure" / "rules" / "a.md").write_text("rule A", encoding="utf-8")

    result = InstructionMemoryLoader(workspace, home=tmp_path / "home", max_tokens=10).load(RuntimeState(), workspace)

    assert result.files[0].path.name == "instructions.md"
    assert result.truncated is True
    assert result.token_count <= 10
    assert "truncated" in result.rendered_text


def test_skill_registry_validates_duplicates_and_searches() -> None:
    skill = SkillCommand("review", "Review a diff", "Check correctness.", "project", when_to_use="review code")
    registry = SkillRegistry((skill,))

    assert registry.load_skill("/review") == skill
    assert registry.search_skills("code") == (skill,)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(skill)
    with pytest.raises(ValueError, match="missing a description"):
        registry.register(SkillCommand("invalid", "", "body", "project"))


def test_agent_profiles_define_isolated_capability_sets() -> None:
    explore = get_agent_profile("ExploreAgent")
    implement = get_agent_profile("ImplementAgent")

    assert explore is not None and explore.permission == "readonly"
    assert "edit_file" not in explore.tools
    assert implement is not None and "edit_file" in implement.tools
    assert get_agent_profile("does-not-exist") is None


def test_hooks_honor_priority_disable_and_isolate_errors() -> None:
    hooks = HookRegistry()
    observed: list[str] = []
    hooks.register(HookEvent.BEFORE_TOOL_CALL, lambda payload: observed.append("low"), priority=1, name="low")
    hooks.register(HookEvent.BEFORE_TOOL_CALL, lambda payload: observed.append("high"), priority=10, name="high")
    hooks.register(HookEvent.BEFORE_TOOL_CALL, lambda payload: (_ for _ in ()).throw(RuntimeError("boom")), priority=5, name="bad")
    assert hooks.set_enabled(HookEvent.BEFORE_TOOL_CALL, "low", False) is True

    result = asyncio.run(hooks.run(HookEvent.BEFORE_TOOL_CALL, {}))

    assert observed == ["high"]
    assert result.metadata["hook_errors"] == ["boom"]


def test_checkpoint_store_restores_files_only_after_confirmation(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("before", encoding="utf-8")
    store = CheckpointStore(tmp_path / ".nervure" / "checkpoints")
    checkpoint = store.create(session_id="s", tool_call_id="c", tool_name="edit_file", paths=(target,))
    target.write_text("after", encoding="utf-8")

    with pytest.raises(CheckpointRestoreError, match="confirmation"):
        store.restore(checkpoint.id, session_id="s")
    store.restore(checkpoint.id, session_id="s", confirmed=True)

    assert target.read_text(encoding="utf-8") == "before"
    assert store.list("s")[0].id == checkpoint.id


def test_executor_creates_checkpoint_before_file_mutation(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("before", encoding="utf-8")

    def handler(_input: dict[str, object], runtime: ToolRuntime) -> ToolExecutionResult:
        target.write_text("after", encoding="utf-8")
        return ToolExecutionResult(runtime.tool_call_id, "mutate", "ok")

    descriptor = ToolDescriptor(
        name="mutate", description="mutate a file", input_schema={"type": "object"}, handler=handler,
        classify_input=lambda _input, _runtime: ToolCallClassification(
            modifies_filesystem=True, targets=(ToolTarget("file", "write", "target.txt"),),
        ),
    )
    state = RuntimeState(session_id="session")
    state.metadata["workspace"] = str(tmp_path)
    store = CheckpointStore(tmp_path / ".nervure" / "checkpoints")

    async def run() -> None:
        executor = RegistryToolExecutor(
            ToolRegistry((descriptor,)),
            guard=SandboxGuard(SandboxBoundary(cwd=tmp_path)),
            checkpoint_store=store,
        )
        async for _update in executor.execute((ToolCall("call", "mutate", {}),), state):
            pass

    asyncio.run(run())
    checkpoint_id = state.metadata["checkpoints"][0]
    store.restore(checkpoint_id, session_id="session", confirmed=True)
    assert target.read_text(encoding="utf-8") == "before"
