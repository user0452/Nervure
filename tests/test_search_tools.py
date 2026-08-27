from __future__ import annotations

import asyncio
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
from typing import Any

import pytest

from core.runtime_state import RuntimeState
from services.guard import SandboxBoundary, SandboxGuard
from services.tools.executor import RegistryToolExecutor
from services.tools.registry import ToolRegistry
from services.tools.types import ToolCall, ToolExecutionResult, ToolRuntime
from tools.glob import descriptor as glob_descriptor
from tools.grep import descriptor as grep_descriptor
from tools.grep.tool import RipgrepResult, SubprocessRipgrepRunner, _handle_with_runner


class FakeRipgrepRunner:
    def __init__(self, result: RipgrepResult | None = None, *, raises: Exception | None = None) -> None:
        self.result = result or RipgrepResult(returncode=0, stdout="", stderr="")
        self.raises = raises
        self.calls: list[tuple[list[str], Path]] = []

    def run(self, args: list[str], cwd: Path) -> RipgrepResult:
        self.calls.append((args, cwd))
        if self.raises is not None:
            raise self.raises
        return self.result


def make_runtime(
    workspace: Path,
    *,
    denied_patterns: tuple[str, ...] = (),
) -> tuple[RegistryToolExecutor, RuntimeState, ToolRuntime]:
    guard = SandboxGuard(
        SandboxBoundary(cwd=workspace, denied_patterns=denied_patterns)
    )
    registry = ToolRegistry([glob_descriptor(), grep_descriptor()])
    state = RuntimeState()
    return (
        RegistryToolExecutor(registry, guard=guard),
        state,
        ToolRuntime(state=state, guard=guard),
    )


def execute_one(
    executor: RegistryToolExecutor,
    state: RuntimeState,
    name: str,
    tool_input: dict[str, Any],
) -> ToolExecutionResult:
    async def collect() -> list[ToolExecutionResult]:
        results: list[ToolExecutionResult] = []
        async for update in executor.execute(
            (ToolCall(id="call-1", name=name, input=tool_input),),
            state,
        ):
            if update.result is not None:
                results.append(update.result)
        return results

    return asyncio.run(collect())[0]


def test_registry_generates_search_tool_schemas_and_prompts() -> None:
    registry = ToolRegistry([grep_descriptor(), glob_descriptor()])

    schemas = registry.tool_schemas(RuntimeState())
    prompts = registry.tool_prompt_sections(RuntimeState())

    assert [schema["function"]["name"] for schema in schemas] == ["glob", "grep"]
    assert schemas[0]["function"]["parameters"]["additionalProperties"] is False
    assert schemas[1]["function"]["parameters"]["properties"]["-i"]["type"] == "boolean"
    assert len(prompts) == 2
    assert all(prompt.startswith("Purpose:") for prompt in prompts)
    assert "Find files by pathname pattern" in prompts[0]
    assert "Search file contents with ripgrep" in prompts[1]


def test_search_tools_classify_as_read_only_with_result_budgets() -> None:
    runtime = ToolRuntime(state=RuntimeState())

    glob_classification = glob_descriptor().classify_input(
        {"pattern": "**/*.py", "path": "src"},
        runtime,
    )
    grep_classification = grep_descriptor().classify_input(
        {"pattern": "ToolDescriptor", "path": "."},
        runtime,
    )

    assert glob_classification.read_only is True
    assert glob_classification.modifies_filesystem is False
    assert glob_classification.concurrency_safe is True
    assert glob_classification.targets[0].kind == "directory"
    assert glob_classification.targets[0].operation == "list"
    assert glob_classification.result_policy.max_result_size_chars == 100_000
    assert grep_classification.read_only is True
    assert grep_classification.modifies_filesystem is False
    assert grep_classification.concurrency_safe is True
    assert grep_classification.targets[0].operation == "read"
    assert grep_classification.result_policy.max_result_size_chars == 20_000
    assert grep_classification.result_policy.persist_when_exceeded is True


def test_invalid_search_inputs_fail_before_handler(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    executor, state, _ = make_runtime(workspace)

    cases = [
        ("glob", {"pattern": ""}),
        ("glob", {"pattern": "*.py", "offset": -1}),
        ("grep", {"pattern": "x", "output_mode": "bad"}),
        ("grep", {"pattern": "x", "-i": "yes"}),
        ("grep", {"pattern": "x", "output_mode": "count", "-C": 2}),
        ("grep", {"pattern": "x", "extra": True}),
    ]

    for name, tool_input in cases:
        result = execute_one(executor, state, name, tool_input)
        assert result.is_error is True
        assert json.loads(result.content)["error"] == "invalid_tool_input"


def test_glob_returns_filtered_paginated_files(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "old.py").write_text("old", encoding="utf-8")
    (workspace / "new.py").write_text("new", encoding="utf-8")
    (workspace / "secret.py").write_text("secret", encoding="utf-8")
    (workspace / "notes.txt").write_text("notes", encoding="utf-8")
    executor, state, _ = make_runtime(workspace, denied_patterns=("secret.py",))

    result = execute_one(
        executor,
        state,
        "glob",
        {"pattern": "*.py", "path": ".", "head_limit": 1},
    )

    assert result.is_error is False
    assert "secret.py" not in result.content
    assert result.metadata["filtered_count"] == 1
    assert result.metadata["num_files"] == 1
    assert result.metadata["total_matches_before_pagination"] == 2
    assert result.metadata["truncated"] is True


def test_glob_excludes_generated_directories_but_explicit_root_remains_searchable(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "src").mkdir()
    (workspace / "src" / "app.py").write_text("app", encoding="utf-8")
    for directory, filename in (
        (".git", "config"),
        (".nervure", "state"),
        ("__pycache__", "cache.pyc"),
        ("src/.pytest_cache", "lastfailed"),
    ):
        target = workspace / directory
        target.mkdir(parents=True)
        (target / filename).write_text("internal", encoding="utf-8")

    executor, state, _ = make_runtime(workspace)
    broad = execute_one(executor, state, "glob", {"pattern": "**/*"})

    assert "src/app.py" in broad.content
    for excluded in (".git/config", ".nervure/state", "__pycache__/cache.pyc", "src/.pytest_cache/lastfailed"):
        assert excluded not in broad.content

    explicit = execute_one(
        executor,
        state,
        "glob",
        {"pattern": "*", "path": ".nervure"},
    )
    assert "state" in explicit.content


def test_search_root_guard_blocks_before_handler(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    executor, state, _ = make_runtime(workspace)

    glob_result = execute_one(
        executor,
        state,
        "glob",
        {"pattern": "*.py", "path": str(outside)},
    )
    grep_result = execute_one(
        executor,
        state,
        "grep",
        {"pattern": "x", "path": str(outside)},
    )

    assert json.loads(glob_result.content)["error"] == "permission_ask_required"
    assert json.loads(grep_result.content)["error"] == "permission_ask_required"


def test_grep_files_with_matches_filters_denied_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "public.txt").write_text("needle", encoding="utf-8")
    (workspace / "secret.txt").write_text("needle", encoding="utf-8")
    _, _, runtime = make_runtime(workspace, denied_patterns=("secret.txt",))
    runner = FakeRipgrepRunner(
        RipgrepResult(returncode=0, stdout="public.txt\nsecret.txt\n", stderr="")
    )

    result = _handle_with_runner(
        {"pattern": "needle", "path": ".", "output_mode": "files_with_matches"},
        runtime,
        runner,
    )

    assert result.is_error is False
    assert "public.txt" in result.content
    assert "secret.txt" not in result.content
    assert result.metadata["filtered_count"] == 1
    assert result.metadata["num_files"] == 1
    assert "-l" in runner.calls[0][0]


def test_grep_content_mode_rewrites_paths_and_line_numbers(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "src").mkdir()
    (workspace / "src" / "app.py").write_text("needle\n", encoding="utf-8")
    _, _, runtime = make_runtime(workspace)
    runner = FakeRipgrepRunner(
        RipgrepResult(returncode=0, stdout="src/app.py:1:needle\n", stderr="")
    )

    result = _handle_with_runner(
        {"pattern": "needle", "path": ".", "output_mode": "content"},
        runtime,
        runner,
    )

    assert result.is_error is False
    assert result.content == "src/app.py:1:needle"
    assert result.metadata["mode"] == "content"
    assert result.metadata["num_lines"] == 1
    assert "-n" in runner.calls[0][0]


def test_grep_content_mode_handles_hyphenated_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "my-file.py").write_text("needle\n", encoding="utf-8")
    _, _, runtime = make_runtime(workspace)
    runner = FakeRipgrepRunner(
        RipgrepResult(returncode=0, stdout="my-file.py:1:needle\n", stderr="")
    )

    result = _handle_with_runner(
        {"pattern": "needle", "path": ".", "output_mode": "content"},
        runtime,
        runner,
    )

    assert result.is_error is False
    assert result.content == "my-file.py:1:needle"


def test_grep_count_mode_reports_match_totals(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.txt").write_text("needle\nneedle\n", encoding="utf-8")
    (workspace / "b.txt").write_text("needle\n", encoding="utf-8")
    _, _, runtime = make_runtime(workspace)
    runner = FakeRipgrepRunner(
        RipgrepResult(returncode=0, stdout="a.txt:2\nb.txt:1\n", stderr="")
    )

    result = _handle_with_runner(
        {"pattern": "needle", "path": ".", "output_mode": "count"},
        runtime,
        runner,
    )

    assert result.is_error is False
    assert "Found 3 matches in 2 files" in result.content
    assert result.metadata["num_matches"] == 3
    assert "-c" in runner.calls[0][0]


def test_grep_ripgrep_errors_are_structured(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _, _, runtime = make_runtime(workspace)

    missing = _handle_with_runner(
        {"pattern": "needle", "path": "."},
        runtime,
        FakeRipgrepRunner(raises=FileNotFoundError("missing rg")),
    )
    bad_regex = _handle_with_runner(
        {"pattern": "[", "path": "."},
        runtime,
        FakeRipgrepRunner(RipgrepResult(returncode=2, stdout="", stderr="regex parse error")),
    )

    assert json.loads(missing.content)["error"] == "ripgrep_not_found"
    assert json.loads(bad_regex.content)["error"] == "ripgrep_error"
    assert bad_regex.metadata["returncode"] == 2


def test_subprocess_ripgrep_runner_decodes_bytes_without_locale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(args, **kwargs):
        assert "text" not in kwargs
        assert kwargs["capture_output"] is True
        return SimpleNamespace(
            returncode=0,
            stdout=b"a.txt:1:\xe4\xb8\xad\xe6\x96\x87\nbad:\xff\n",
            stderr=b"",
        )

    monkeypatch.setattr(shutil, "which", lambda name: "rg.exe")
    monkeypatch.setattr("tools.grep.tool.subprocess.run", fake_run)

    result = SubprocessRipgrepRunner().run(["needle", "."], tmp_path)

    assert result.returncode == 0
    assert "a.txt:1:" in result.stdout
    assert "bad:" in result.stdout


def test_grep_real_ripgrep_smoke(tmp_path: Path) -> None:
    if shutil.which("rg") is None:
        pytest.skip("ripgrep is not available on PATH")

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.py").write_text("class Needle:\n    pass\n", encoding="utf-8")
    (workspace / "b.txt").write_text("Needle\n", encoding="utf-8")
    executor, state, _ = make_runtime(workspace)

    result = execute_one(
        executor,
        state,
        "grep",
        {
            "pattern": "Needle",
            "path": ".",
            "glob": "*.py",
            "output_mode": "files_with_matches",
        },
    )

    assert result.is_error is False
    assert "a.py" in result.content
    assert "b.txt" not in result.content


def test_grep_excludes_generated_directories_by_default(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "src").mkdir()
    (workspace / "src" / "app.py").write_text("needle\n", encoding="utf-8")
    (workspace / ".nervure").mkdir()
    (workspace / ".nervure" / "state").write_text("needle\n", encoding="utf-8")
    _, _, runtime = make_runtime(workspace)
    runner = FakeRipgrepRunner(
        RipgrepResult(
            returncode=0,
            stdout="src/app.py:1:needle\n",
            stderr="",
        )
    )

    result = _handle_with_runner(
        {"pattern": "needle", "path": ".", "output_mode": "content"},
        runtime,
        runner,
    )

    assert "src/app.py:1:needle" in result.content
    assert ".nervure/state" not in result.content
    args = runner.calls[0][0]
    assert any(
        args[index : index + 2] == ["--glob", "!**/.nervure/**"]
        for index in range(len(args) - 1)
    )


def test_grep_excludes_nested_generated_directories_but_explicit_root_is_searchable(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    nested = workspace / "src" / ".nervure"
    nested.mkdir(parents=True)
    (nested / "nested.txt").write_text("needle\n", encoding="utf-8")
    explicit = workspace / ".nervure"
    explicit.mkdir()
    (explicit / "state.txt").write_text("needle\n", encoding="utf-8")
    (workspace / ".config").write_text("needle\n", encoding="utf-8")
    _, _, runtime = make_runtime(workspace)

    broad_runner = FakeRipgrepRunner(
        RipgrepResult(returncode=0, stdout=".config:1:needle\n", stderr="")
    )
    broad = _handle_with_runner(
        {"pattern": "needle", "path": ".", "output_mode": "content"},
        runtime,
        broad_runner,
    )
    assert ".config:1:needle" in broad.content
    args = broad_runner.calls[0][0]
    assert ["--glob", "!**/.nervure/**"] in [
        args[index : index + 2] for index in range(len(args) - 1)
    ]

    explicit_runner = FakeRipgrepRunner(
        RipgrepResult(returncode=0, stdout="state.txt:1:needle\n", stderr="")
    )
    explicit_result = _handle_with_runner(
        {"pattern": "needle", "path": ".nervure", "output_mode": "content"},
        runtime,
        explicit_runner,
    )
    assert explicit_result.is_error is False
    assert "state.txt:1:needle" in explicit_result.content
    explicit_args = explicit_runner.calls[0][0]
    assert ["--glob", "!**/.nervure/**"] not in [
        explicit_args[index : index + 2]
        for index in range(len(explicit_args) - 1)
    ]
