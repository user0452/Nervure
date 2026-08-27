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
from tools.repo_map import descriptor as repo_map_descriptor
from tools.repo_map import tool as repo_map_tool


def _execute(
    workspace: Path,
    tool_input: dict[str, Any] | None = None,
    *,
    denied_patterns: tuple[str, ...] = (),
) -> ToolExecutionResult:
    guard = SandboxGuard(
        SandboxBoundary(cwd=workspace, denied_patterns=denied_patterns)
    )
    executor = RegistryToolExecutor(
        ToolRegistry([repo_map_descriptor()]),
        guard=guard,
    )
    state = RuntimeState()

    async def collect() -> list[ToolExecutionResult]:
        results: list[ToolExecutionResult] = []
        async for update in executor.execute(
            (ToolCall(id="call-repo-map", name="repo_map", input=tool_input or {}),),
            state,
        ):
            if update.result is not None:
                results.append(update.result)
        return results

    return asyncio.run(collect())[0]


def test_repo_map_extracts_python_paths_and_symbols(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "core").mkdir(parents=True)
    (workspace / "core" / "loop.py").write_text(
        '"""Agent loop module.\n\nMore detail."""\n'
        "class AgentLoop:\n"
        "    async def stream(self, prompt: str, limit: int = 1) -> str:\n"
        "        return prompt\n"
        "    def helper(self, value):\n"
        "        return value\n"
        "\n"
        "def build(name: str = 'default') -> AgentLoop:\n"
        "    return AgentLoop()\n"
        "\n"
        "async def watch() -> None:\n"
        "    return None\n",
        encoding="utf-8",
    )

    result = _execute(workspace)

    assert result.is_error is False
    assert "core/" in result.content
    assert "loop.py" in result.content
    assert "class AgentLoop" in result.content
    assert "async def stream(self: str" not in result.content
    assert "async def stream(self, prompt: str, limit: int = 1) -> str" in result.content
    assert "def helper(self, value)" in result.content
    assert "def build(name: str = 'default') -> AgentLoop" in result.content
    assert "async def watch() -> None" in result.content
    assert "doc: Agent loop module." in result.content
    assert "C:\\" not in result.content


def test_repo_map_skips_generated_and_legacy_state_directories(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "src").mkdir()
    (workspace / "src" / "main.py").write_text("def main(): pass\n", encoding="utf-8")
    excluded = (
        ".git", ".nervure", ".onecode", ".venv", "venv", "__pycache__",
        "node_modules", "build", "dist", "cache",
    )
    for name in excluded:
        directory = workspace / name
        directory.mkdir()
        (directory / "hidden.py").write_text("class Hidden: pass\n", encoding="utf-8")

    result = _execute(workspace)

    assert result.is_error is False
    assert "src/" in result.content and "main.py (path: src/main.py)" in result.content
    assert "Hidden" not in result.content
    assert all(f"{name}/" not in result.content for name in excluded)


def test_repo_map_syntax_error_is_a_warning_not_a_failure(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    (workspace / "valid.py").write_text("class Valid: pass\n", encoding="utf-8")

    result = _execute(workspace)

    assert result.is_error is False
    assert "valid.py" in result.content
    assert "class Valid" in result.content
    assert "class Broken" not in result.content
    assert "syntax error at line" in result.content
    assert result.metadata["warning_count"] == 1


def test_repo_map_output_order_is_deterministic(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "z").mkdir(parents=True)
    (workspace / "a").mkdir()
    (workspace / "z" / "z.py").write_text("class Z: pass\n", encoding="utf-8")
    (workspace / "a" / "a.py").write_text("class A: pass\n", encoding="utf-8")
    (workspace / "root.py").write_text("def root(): pass\n", encoding="utf-8")

    first = _execute(workspace).content
    second = _execute(workspace).content

    assert first == second
    assert first.index("a/") < first.index("z/") < first.index("root.py")


def test_repo_map_applies_file_symbol_and_output_budgets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "symbols.py").write_text(
        "\n".join(f"def function_{index}(): pass" for index in range(10)),
        encoding="utf-8",
    )
    monkeypatch.setattr(repo_map_tool, "MAX_SYMBOLS", 2)
    monkeypatch.setattr(repo_map_tool, "MAX_OUTPUT_CHARS", 260)

    result = _execute(workspace)

    assert result.is_error is False
    assert result.metadata["truncated"] is True
    assert "max_symbols" in result.metadata["truncation_reasons"]
    assert "output character budget" in result.content or len(result.content) <= 260


def test_repo_map_limits_number_of_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for index in range(3):
        (workspace / f"file_{index}.py").write_text(
            f"class File{index}: pass\n",
            encoding="utf-8",
        )
    monkeypatch.setattr(repo_map_tool, "MAX_FILES", 2)

    result = _execute(workspace)

    assert result.is_error is False
    assert result.metadata["files_included"] == 2
    assert result.metadata["truncated"] is True
    assert "max_files" in result.metadata["truncation_reasons"]
    assert "File0" in result.content
    assert "File2" not in result.content


def test_repo_map_max_depth_is_workspace_relative(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "one" / "two").mkdir(parents=True)
    (workspace / "root.py").write_text("class Root: pass\n", encoding="utf-8")
    (workspace / "one" / "one.py").write_text("class One: pass\n", encoding="utf-8")
    (workspace / "one" / "two" / "two.py").write_text("class Two: pass\n", encoding="utf-8")

    result = _execute(workspace, {"max_depth": 1})

    assert "root.py" in result.content
    assert "one.py" in result.content
    assert "two.py" not in result.content


def test_repo_map_is_registered_provider_visible_and_read_only() -> None:
    descriptor = repo_map_descriptor()
    registry = ToolRegistry([descriptor])
    state = RuntimeState()
    schema = registry.tool_schemas(state)[0]
    classification = descriptor.classify_input({}, ToolRuntime(state=state))

    assert schema["function"]["name"] == "repo_map"
    assert schema["function"]["parameters"] == descriptor.input_schema
    assert registry.tool_prompt_sections(state) == (descriptor.prompt,)
    assert classification.read_only is True
    assert classification.modifies_filesystem is False
    assert classification.concurrency_safe is True
    assert classification.targets[0].kind == "directory"
    assert classification.targets[0].operation == "list"
    assert classification.result_policy.persist_when_exceeded is False


def test_repo_map_does_not_cross_external_guard_boundary(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (outside / "secret.py").write_text("class Secret: pass\n", encoding="utf-8")

    result = _execute(workspace, {"path": str(outside)})

    assert result.is_error is True
    assert json.loads(result.content)["error"] == "permission_ask_required"
