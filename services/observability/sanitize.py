"""Trace metadata sanitizer.

Trace 只记录运行事实摘要；这里集中裁剪敏感字段，避免调用点各自判断。
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import re
from typing import Any

MAX_KEYS = 20
MAX_STRING_CHARS = 240
MAX_DEPTH = 2
REDACTED = "[redacted]"
_SENSITIVE_TEXT_PATTERNS = (
    re.compile(
        r"(?i)(api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|cookie|password|secret)\s*[:=]\s*([^\s,;]+)"
    ),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"),
)

SENSITIVE_KEY_PARTS = (
    "key",
    "token",
    "secret",
    "password",
    "authorization",
    "header",
    "cookie",
    "bearer",
    "credential",
    "env",
    "content",
    "prompt",
    "stdout",
    "stderr",
    "old_string",
    "new_string",
)
SAFE_COUNTER_KEYS = {
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "uncached_input_tokens",
    "reasoning_tokens",
    "reasoning_tokens_status",
    "visible_output_tokens",
    "estimated_tokens",
    "stdout_chars",
    "stderr_chars",
    "content_chars",
}
SAFE_DEBUG_TEXT_KEYS = {
    "user_prompt",
    "assistant_visible_text",
    "provider_reasoning_text",
    "tool_arguments",
    "tool_result_text",
}
SAFE_HASH_KEYS = {
    "system_prompt_hash",
    "tool_schema_hash",
}


def sanitize_attributes(
    attributes: Mapping[str, Any] | None,
    *,
    workspace: Path | None = None,
    debug: bool = False,
) -> dict[str, Any]:
    if attributes is None:
        return {}
    key_limit = 50 if debug else MAX_KEYS
    return {
        str(key)[:MAX_STRING_CHARS]: _sanitize_value(
            str(key),
            value,
            workspace=workspace,
            depth=0,
            debug=debug,
            key_limit=key_limit,
        )
        for index, (key, value) in enumerate(attributes.items())
        if index < key_limit
    }


def _sanitize_value(
    key: str,
    value: Any,
    *,
    workspace: Path | None,
    depth: int,
    debug: bool,
    key_limit: int,
) -> Any:
    lowered_key = key.lower()
    if _is_sensitive_key(lowered_key):
        return REDACTED
    if _looks_like_path_key(lowered_key):
        return _sanitize_path_value(value, workspace)
    if isinstance(value, str):
        return _truncate(_redact_sensitive_text(value), debug=debug)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, Path):
        return _sanitize_path(value, workspace)
    if depth >= (6 if debug else MAX_DEPTH):
        return "[max_depth]"
    if isinstance(value, Mapping):
        return {
            str(child_key)[:MAX_STRING_CHARS]: _sanitize_value(
                str(child_key),
                child_value,
                workspace=workspace,
                depth=depth + 1,
                debug=debug,
                key_limit=key_limit,
            )
            for index, (child_key, child_value) in enumerate(value.items())
            if index < key_limit
        }
    if isinstance(value, (list, tuple)):
        return [
            _sanitize_value(
                key,
                item,
                workspace=workspace,
                depth=depth + 1,
                debug=debug,
                key_limit=key_limit,
            )
            for item in list(value)[:MAX_KEYS]
        ]
    return f"[{type(value).__name__}]"


def _is_sensitive_key(lowered_key: str) -> bool:
    if (
        lowered_key in SAFE_COUNTER_KEYS
        or lowered_key in SAFE_DEBUG_TEXT_KEYS
        or lowered_key in SAFE_HASH_KEYS
    ):
        return False
    return any(part in lowered_key for part in SENSITIVE_KEY_PARTS)


def _looks_like_path_key(lowered_key: str) -> bool:
    return lowered_key == "path" or lowered_key.endswith("_path")


def _sanitize_path_value(value: Any, workspace: Path | None) -> Any:
    if isinstance(value, (str, Path)):
        return _sanitize_path(Path(value), workspace)
    return "[path]"


def _sanitize_path(path: Path, workspace: Path | None) -> str:
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        resolved = path.expanduser().absolute()

    if workspace is not None:
        try:
            workspace_resolved = workspace.expanduser().resolve()
            return str(resolved.relative_to(workspace_resolved))
        except (OSError, ValueError):
            pass
    suffix = resolved.suffix
    return f"[external_path]{suffix}" if suffix else "[external_path]"


def _truncate(value: str, *, debug: bool = False) -> str:
    if debug or len(value) <= MAX_STRING_CHARS:
        return value
    return f"{value[:MAX_STRING_CHARS]}..."


def _redact_sensitive_text(value: str) -> str:
    redacted = value
    for pattern in _SENSITIVE_TEXT_PATTERNS:
        if pattern.groups == 2:
            redacted = pattern.sub(lambda match: f"{match.group(1)}=[redacted]", redacted)
        else:
            redacted = pattern.sub("Bearer [redacted]", redacted)
    return redacted
