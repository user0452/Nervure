from __future__ import annotations

import asyncio
import io
from pathlib import Path

from core.stream_events import AgentEvent
from services.tools.types import ToolCall, ToolExecutionResult
from rich.console import Console
from ui.cli.terminal.activity import (
    ActivityGroup,
    format_activity_details,
    format_activity_summary,
    format_tool_arguments,
    format_tool_call,
)
from ui.cli.terminal.stream_reducer import reduce_stream_event
from ui.cli.terminal.stream_state import CliStreamUiState, CommitKind
from ui.cli.terminal.stream_view import render_stream_body_ansi
from ui.cli.terminal.stream_session import StreamingSession
from ui.cli.terminal import static_output as static_output_module
from ui.cli.theme import RICH_THEME


def _attr(**extra: object) -> dict[str, object]:
    return {
        "assistant_call_id": "ac1",
        "model_turn_index": 1,
        **extra,
    }


def _plain(value: object) -> str:
    return getattr(value, "value", str(value))


def test_known_tool_arguments_use_one_identifying_argument() -> None:
    assert format_tool_call("read_file", {"file_path": "core/loop.py", "limit": 50}) == (
        "Read core/loop.py"
    )
    assert format_tool_call("grep", {"pattern": "PermissionMode", "path": "core"}) == (
        'Search "PermissionMode"'
    )
    assert format_tool_call("glob", {"pattern": "services/**/*.py"}) == (
        "Glob services/**/*.py"
    )
    assert format_tool_call("bash", {"command": "pytest tests/test_runtime_hitl.py -q"}) == (
        "Bash \"pytest tests/test_runtime_hitl.py -q\""
    )
    assert format_tool_arguments("edit_file", {"file_path": "core/runtime_state.py"}) == (
        "core/runtime_state.py"
    )


def test_unknown_tool_arguments_are_scalar_and_deterministic() -> None:
    arguments = {"z": "last", "nested": {"secret": "not rendered"}, "a": 3}
    assert format_tool_arguments("mcp_custom", arguments) == 'a=3 z="last"'
    assert "secret" not in format_tool_arguments("mcp_custom", arguments)


def test_activity_group_is_collapsed_by_default_and_expands_to_level_two() -> None:
    state = CliStreamUiState()
    calls = [
        ("c1", "read_file", {"file_path": "core/loop.py"}),
        ("c2", "grep", {"pattern": "PermissionMode"}),
        ("c3", "read_file", {"file_path": "core/runtime_state.py"}),
    ]
    for call_id, name, arguments in calls:
        reduce_stream_event(
            state,
            AgentEvent(
                type="tool_call_ready",
                metadata=_attr(
                    tool_call=ToolCall(id=call_id, name=name, input=arguments),
                ),
            ),
        )
        reduce_stream_event(
            state,
            AgentEvent(
                type="tool_started",
                metadata=_attr(tool_call_id=call_id, tool_name=name),
            ),
        )

    collapsed = _plain(render_stream_body_ansi(state, width=120))
    assert "正在分析项目结构" in collapsed
    assert "3 tools" in collapsed
    assert "core/loop.py" not in collapsed
    assert "PermissionMode" not in collapsed

    state.activities_expanded = True
    expanded = _plain(render_stream_body_ansi(state, width=120))
    assert "Read core/loop.py" in expanded
    assert 'Search "PermissionMode"' in expanded
    assert "Read core/runtime_state.py" in expanded
    assert "content" not in expanded


def test_activity_summary_is_visible_between_call_declaration_and_tool_start() -> None:
    state = CliStreamUiState()
    reduce_stream_event(
        state,
        AgentEvent(
            type="tool_call_ready",
            metadata=_attr(
                tool_call=ToolCall(
                    id="pending",
                    name="read_file",
                    input={"file_path": "README.md"},
                )
            ),
        ),
    )
    rendered = _plain(render_stream_body_ansi(state, width=120))
    assert state.activity_enabled
    assert "正在分析项目结构" in rendered
    assert "1 tool" in rendered
    assert "README.md" not in rendered


def test_completed_activity_stages_one_collapsed_checkpoint_without_result_body() -> None:
    state = CliStreamUiState()
    call = ToolCall(id="c1", name="read_file", input={"file_path": "core/loop.py"})
    reduce_stream_event(state, AgentEvent(type="tool_call_ready", metadata=_attr(tool_call=call)))
    reduce_stream_event(
        state,
        AgentEvent(type="tool_started", metadata=_attr(tool_call_id="c1", tool_name="read_file")),
    )
    reduce_stream_event(
        state,
        AgentEvent(
            type="tool_result",
            result=ToolExecutionResult(
                tool_call_id="c1",
                tool_name="read_file",
                content="SECRET RESULT BODY",
            ),
            metadata=_attr(tool_call_id="c1"),
        ),
    )
    activity_commits = [
        commit
        for commit in state.pending_static_commits
        if commit.kind == CommitKind.ACTIVITY_GROUP
    ]
    assert len(activity_commits) == 1
    assert "SECRET RESULT BODY" not in format_activity_summary(activity_commits[0].payload)
    assert format_activity_details(activity_commits[0].payload) == ["  └─ Read core/loop.py"]


def test_streaming_session_commits_group_summary_instead_of_result_body(tmp_path: Path) -> None:
    buffer = io.StringIO()
    static_output_module.reset_static_console()
    static_output_module._STATIC_CONSOLE = Console(  # noqa: SLF001
        file=buffer,
        force_terminal=True,
        color_system="standard",
        width=100,
        theme=RICH_THEME,
    )
    session = StreamingSession(workspace=tmp_path)
    call = ToolCall(id="c1", name="read_file", input={"file_path": "core/loop.py"})
    reduce_stream_event(session.state, AgentEvent(type="tool_call_ready", metadata=_attr(tool_call=call)))
    reduce_stream_event(
        session.state,
        AgentEvent(type="tool_started", metadata=_attr(tool_call_id="c1", tool_name="read_file")),
    )
    reduce_stream_event(
        session.state,
        AgentEvent(
            type="tool_result",
            result=ToolExecutionResult(
                tool_call_id="c1",
                tool_name="read_file",
                content="SECRET RESULT BODY",
            ),
            metadata=_attr(tool_call_id="c1"),
        ),
    )
    session._commit_pending_to_coordinator()
    asyncio.run(session.coordinator.flush_ready_checkpoints())
    output = buffer.getvalue()
    assert "正在分析项目结构" in output
    assert "core/loop.py" not in output
    assert "SECRET RESULT BODY" not in output
    assert "[read_file]" not in output
