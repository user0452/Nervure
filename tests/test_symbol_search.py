from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from core.runtime_state import RuntimeState
from services.guard import SandboxBoundary, SandboxGuard
from services.tools.executor import RegistryToolExecutor
from services.tools.registry import ToolRegistry
from services.tools.types import ToolCall, ToolExecutionResult, ToolRuntime
from tools.symbol_search import descriptor as symbol_search_descriptor
from tools.symbol_search import tool as symbol_search_tool


def _execute(
    workspace: Path,
    tool_input: dict[str, Any] | None = None,
) -> ToolExecutionResult:
    guard = SandboxGuard(SandboxBoundary(cwd=workspace))
    executor = RegistryToolExecutor(
        ToolRegistry([symbol_search_descriptor()]),
        guard=guard,
    )
    state = RuntimeState()

    async def collect() -> list[ToolExecutionResult]:
        results: list[ToolExecutionResult] = []
        async for update in executor.execute(
            (ToolCall(id="call-symbol-search", name="symbol_search", input=tool_input or {}),),
            state,
        ):
            if update.result is not None:
                results.append(update.result)
        return results

    return asyncio.run(collect())[0]


def _write_fixture(workspace: Path) -> None:
    (workspace / "taskflow").mkdir(parents=True)
    (workspace / "taskflow" / "scheduler.py").write_text(
        "class TaskScheduler:\n"
        "    def retry_task(self, task_id: str) -> Task:\n"
        "        return task_id\n"
        "\n"
        "    async def retry_async(self, task_id: str) -> Task:\n"
        "        return task_id\n"
        "\n"
        "def retry_state(task: Task) -> State:\n"
        "    return task\n"
        "\n"
        "async def retry_worker(task: Task) -> None:\n"
        "    return None\n",
        encoding="utf-8",
    )
    (workspace / "taskflow" / "state.py").write_text(
        "def retry_state_from_snapshot(snapshot: dict) -> State:\n"
        "    return snapshot\n",
        encoding="utf-8",
    )


def test_symbol_search_extracts_definition_kinds_lines_and_signatures(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_fixture(workspace)

    result = _execute(workspace, {"query": "retry"})

    assert result.is_error is False
    assert "taskflow/scheduler.py:" in result.content
    assert "method TaskScheduler.retry_task" in result.content
    assert "async_method TaskScheduler.retry_async" in result.content
    assert "function retry_state" in result.content
    assert "async_function retry_worker" in result.content
    assert "def retry_task(self, task_id: str) -> Task" in result.content
    assert "C:\\" not in result.content
    assert result.metadata["match_count"] == 5


def test_symbol_search_exact_qualified_name_ranks_before_prefix_and_substring(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_fixture(workspace)

    exact = _execute(workspace, {"query": "TaskScheduler.retry_task"})
    assert exact.content.splitlines()[1].startswith("1. taskflow/scheduler.py:")
    assert "method TaskScheduler.retry_task" in exact.content.splitlines()[2]

    prefix = _execute(workspace, {"query": "retry"})
    lines = prefix.content.splitlines()
    assert lines[1].startswith("1. ")
    assert "scheduler.py" in lines[1]

    substring = _execute(workspace, {"query": "snapshot"})
    assert "function retry_state_from_snapshot" in substring.content


def test_symbol_search_is_deterministic_and_supports_relative_path(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_fixture(workspace)

    first = _execute(workspace, {"query": "retry", "path": "taskflow"}).content
    second = _execute(workspace, {"query": "retry", "path": "taskflow"}).content

    assert first == second
    assert "taskflow/scheduler.py" in first
    assert "taskflow/state.py" in first


def test_symbol_search_skips_excluded_dirs_and_syntax_errors(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "valid.py").write_text("class Valid: pass\n", encoding="utf-8")
    (workspace / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    for directory_name in (".git", ".nervure", ".onecode", ".venv", "venv", "__pycache__", "node_modules", "build", "dist", "cache"):
        directory = workspace / directory_name
        directory.mkdir()
        (directory / "hidden.py").write_text("class Hidden: pass\n", encoding="utf-8")

    result = _execute(workspace, {"query": ""})
    assert result.is_error is True

    result = _execute(workspace, {"query": "Valid"})
    assert result.is_error is False
    assert "class Valid" in result.content
    assert "Hidden" not in result.content
    assert "syntax error at line" in result.content
    assert result.metadata["warning_count"] == 1


def test_symbol_search_respects_max_results_and_output_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "symbols.py").write_text(
        "\n".join(f"def retry_{index}(value: str) -> str: return value" for index in range(8)),
        encoding="utf-8",
    )
    monkeypatch.setattr(symbol_search_tool, "MAX_OUTPUT_CHARS", 180)

    result = _execute(workspace, {"query": "retry", "max_results": 2})

    assert result.is_error is False
    assert result.metadata["match_count"] == 2
    assert result.metadata["truncated"] is True
    assert "max_results" in result.metadata["truncation_reasons"]
    assert len(result.content) <= 180


def test_symbol_search_rejects_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (outside / "secret.py").write_text("class Secret: pass\n", encoding="utf-8")

    result = _execute(workspace, {"query": "Secret", "path": str(outside)})

    assert result.is_error is True
    assert json.loads(result.content)["error"] == "permission_ask_required"


def test_symbol_search_is_provider_visible_read_only_and_concurrency_safe() -> None:
    descriptor = symbol_search_descriptor()
    registry = ToolRegistry([descriptor])
    state = RuntimeState()
    schema = registry.tool_schemas(state)[0]
    classification = descriptor.classify_input(
        {"query": "retry"},
        ToolRuntime(state=state),
    )

    assert schema["function"]["name"] == "symbol_search"
    assert schema["function"]["parameters"] == descriptor.input_schema
    assert registry.tool_prompt_sections(state) == (descriptor.prompt,)
    assert classification.read_only is True
    assert classification.modifies_filesystem is False
    assert classification.concurrency_safe is True
    assert classification.targets[0].kind == "directory"
    assert classification.targets[0].operation == "list"
    assert classification.result_policy.persist_when_exceeded is False
    assert "known or suspected" in descriptor.description
    assert "repo_map" in descriptor.description
    assert "grep" in descriptor.description
    assert "prefer this over guessing" in descriptor.prompt
    assert "not a mandatory step" in descriptor.prompt


def test_symbol_search_schema_requires_only_query() -> None:
    descriptor = symbol_search_descriptor()
    assert descriptor.input_schema["required"] == ["query"]
    assert set(descriptor.input_schema["properties"]) == {"query", "path", "max_results"}
