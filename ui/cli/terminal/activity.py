"""First-class terminal activity data and concise tool-call formatting.

The terminal has two user-facing levels for tool work:

* an activity group title and compact completion summary; and
* an optional list of tool names plus the most identifying argument.

This module deliberately does not inspect tool results.  Tool result bodies
belong to the runtime transcript/result store, not to the activity view.
Keeping the tool-name mapping here also prevents individual renderers from
growing their own argument heuristics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


ACTIVITY_FALLBACK_TITLE = "Working…"
MAX_ACTIVITY_ARGUMENT_LENGTH = 120
MAX_ACTIVITY_STRING_LENGTH = 90


@dataclass
class ActivityToolCall:
    """A level-two, result-free representation of one tool invocation."""

    call_id: str
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    #: Only the result status is retained.  The body stays in the ordinary
    #: tool-result checkpoint/transcript and is never part of activity state.
    result_is_error: bool | None = None
    started: bool = False

    @property
    def completed(self) -> bool:
        return self.result_is_error is not None


@dataclass
class ActivityGroup:
    """A level-one semantic activity group for one model invocation."""

    assistant_call_id: str
    model_turn_index: int
    title: str = ACTIVITY_FALLBACK_TITLE
    activity_id: str = ""
    expanded: bool = False
    status: str = "running"
    tools: list[ActivityToolCall] = field(default_factory=list)
    committed: bool = False

    @property
    def complete(self) -> bool:
        return bool(self.tools) and all(tool.completed for tool in self.tools)

    @property
    def error_count(self) -> int:
        return sum(1 for tool in self.tools if tool.result_is_error is True)

    @property
    def tool_count(self) -> int:
        return len(self.tools)

    @property
    def display_status(self) -> str:
        if self.error_count:
            return "error"
        if self.complete:
            return "completed"
        return self.status


_DISPLAY_NAMES = {
    "read": "Read",
    "read_file": "Read",
    "grep": "Search",
    "search": "Search",
    "glob": "Glob",
    "bash": "Bash",
    "edit": "Edit",
    "edit_file": "Edit",
    "write": "Write",
    "write_file": "Write",
    "agent": "Agent",
    "skill": "Skill",
}


def display_tool_name(tool_name: str) -> str:
    """Return the stable, human-facing name for a tool."""

    name = str(tool_name or "tool")
    return _DISPLAY_NAMES.get(name.casefold(), name)


def activity_title(metadata: dict[str, Any] | None = None) -> str:
    """Read an explicit semantic title, with a deterministic sentinel fallback.

    ``inferred_activity_title`` is applied by the reducer only when this
    explicit metadata path is absent; keeping that decision outside this
    accessor makes the provider-neutral control protocol easy to test.
    """

    metadata = metadata or {}
    for key in ("activity_title", "activity", "stage_title"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return _compact(value, limit=MAX_ACTIVITY_STRING_LENGTH)
    return ACTIVITY_FALLBACK_TITLE


def explicit_activity_id(metadata: dict[str, Any] | None = None) -> str | None:
    """Read an optional semantic activity identity from runtime metadata."""

    metadata = metadata or {}
    for key in ("activity_id", "activity_key", "stage_id"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def inferred_activity_title(tool_name: str, arguments: Any = None) -> str:
    """Provide a useful semantic phase when a provider has no metadata path."""

    name = str(tool_name or "").casefold()
    if name in {"glob", "grep", "search", "read", "read_file"}:
        return "正在分析项目结构"
    if name in {"edit", "edit_file", "write", "write_file"}:
        return "正在修改实现"
    if name == "bash":
        command = ""
        if isinstance(arguments, dict):
            command = str(arguments.get("command") or "").casefold()
        if any(token in command for token in ("pytest", "test", "check", "lint")):
            return "正在运行测试"
        return "正在执行命令"
    if name in {"agent", "skill"}:
        return "正在委派任务"
    return "正在处理任务"


def format_tool_arguments(
    tool_name: str,
    arguments: Any,
    *,
    include_key: bool = False,
    limit: int = MAX_ACTIVITY_ARGUMENT_LENGTH,
) -> str:
    """Format only the identifying arguments for one known tool.

    ``include_key`` is used by the legacy queued preview.  The expanded
    activity view leaves keys out where the tool has an obvious argument,
    producing lines such as ``Read core/loop.py`` and ``Search "foo"``.
    Unknown tools use sorted scalar/string fields, never a JSON dump.
    """

    if not isinstance(arguments, dict):
        return _format_scalar(arguments, limit=limit)

    name = str(tool_name or "").casefold()
    key: str | None = None
    if name in {"read", "read_file", "edit", "edit_file", "write", "write_file"}:
        key = "file_path" if "file_path" in arguments else "path"
    elif name in {"grep", "search"}:
        key = "pattern" if "pattern" in arguments else "query"
    elif name == "glob":
        key = "pattern"
    elif name == "bash":
        key = "command"
    elif name == "agent":
        key = "prompt"
    elif name == "skill":
        key = "skill"

    if key is not None and key in arguments:
        if name in {
            "read",
            "read_file",
            "edit",
            "edit_file",
            "write",
            "write_file",
            "glob",
        }:
            rendered = _truncate(_compact(str(arguments[key]), limit=MAX_ACTIVITY_STRING_LENGTH), limit)
        else:
            rendered = _format_scalar(arguments[key], limit=limit)
        if include_key:
            return _truncate(f"{key}={rendered}", limit)
        return rendered

    # Deterministic safe fallback: scalar values only, sorted by key.  Nested
    # objects and arrays are intentionally not rendered as raw JSON.
    parts: list[str] = []
    for raw_key in sorted(arguments, key=lambda value: str(value)):
        value = arguments[raw_key]
        if not _is_scalar(value):
            continue
        rendered = _format_scalar(value, limit=MAX_ACTIVITY_STRING_LENGTH)
        parts.append(f"{raw_key}={rendered}")
        if len(" ".join(parts)) >= limit:
            break
    return _truncate(" ".join(parts), limit)


def has_identifying_arguments(tool_name: str, arguments: Any) -> bool:
    """Return whether a call has enough scalar input for level two."""

    if not isinstance(arguments, dict):
        return False
    name = str(tool_name or "").casefold()
    known_keys = {
        "read": "file_path",
        "read_file": "file_path",
        "grep": "pattern",
        "search": "pattern",
        "glob": "pattern",
        "bash": "command",
        "edit": "file_path",
        "edit_file": "file_path",
        "write": "file_path",
        "write_file": "file_path",
        "agent": "prompt",
        "skill": "skill",
    }
    key = known_keys.get(name)
    if key is not None:
        return key in arguments and _is_scalar(arguments[key])
    return any(_is_scalar(value) for value in arguments.values())


def format_tool_call(tool_name: str, arguments: Any) -> str:
    """Format a level-two activity row without result content."""

    label = display_tool_name(tool_name)
    detail = format_tool_arguments(tool_name, arguments)
    return f"{label} {detail}".rstrip()


def format_activity_compact_tools(group: ActivityGroup) -> str:
    """Format the primary tool for a persistent collapsed Activity row."""

    primary = next((tool for tool in group.tools if not tool.completed), None)
    if primary is None and group.tools:
        primary = group.tools[0]
    if primary is None:
        return ""
    summary = format_tool_call(primary.tool_name, primary.arguments)
    overflow = max(0, group.tool_count - 1)
    if overflow:
        summary = f"{summary} +{overflow} more"
    return summary


def format_activity_summary(group: ActivityGroup) -> str:
    """Format the compatibility collapsed level-one line."""

    tools = f"{group.tool_count} tool" if group.tool_count == 1 else f"{group.tool_count} tools"
    suffix = f", {group.error_count} failed" if group.error_count else ""
    if group.complete:
        return f"✓ {group.title} · {tools}{suffix} — Completed"
    return f"● {group.title} · {tools}{suffix}"


def format_activity_details(group: ActivityGroup) -> list[str]:
    """Format the optional level-two rows, with no result bodies."""

    lines: list[str] = []
    for index, tool in enumerate(group.tools):
        branch = "└─" if index == len(group.tools) - 1 else "├─"
        lines.append(f"  {branch} {format_tool_call(tool.tool_name, tool.arguments)}")
    return lines


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (bool, int, float, str))


def _format_scalar(value: Any, *, limit: int) -> str:
    if isinstance(value, str):
        compact = _compact(value, limit=MAX_ACTIVITY_STRING_LENGTH)
        rendered = f'"{compact}"'
    elif value is None:
        rendered = "null"
    else:
        rendered = str(value)
    return _truncate(rendered, limit)


def _compact(value: str, *, limit: int) -> str:
    compact = " ".join(value.split())
    return _truncate(compact, limit)


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


__all__ = [
    "ACTIVITY_FALLBACK_TITLE",
    "ActivityGroup",
    "ActivityToolCall",
    "display_tool_name",
    "activity_title",
    "explicit_activity_id",
    "inferred_activity_title",
    "format_activity_compact_tools",
    "format_activity_details",
    "format_activity_summary",
    "format_tool_arguments",
    "format_tool_call",
    "has_identifying_arguments",
]
