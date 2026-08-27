"""LLM side-query selector for relevant long-term memories."""

from __future__ import annotations

import hashlib
import json
from time import perf_counter
from typing import Any

from core.runtime_state import RuntimeState
from services.context.snapshot import ContextSnapshot
from services.memory.types import LongTermMemoryFile
from services.model.client import ModelClient
from services.observability import TraceRecorder

SELECTOR_SYSTEM_PROMPT = (
    "Select at most five long-term memory files relevant to the current turn. "
    "Return only JSON: {\"selected_memories\": [\"relative/path.md\"]}. "
    "Use only filenames present in the catalog."
)
SELECTOR_MAX_OUTPUT_TOKENS = 256


def catalog_fingerprint(catalog: tuple[LongTermMemoryFile, ...]) -> str:
    """Return a stable fingerprint for the selector-visible catalog metadata."""

    rows = (
        "\0".join(
            (
                item.relative_path,
                item.name,
                item.description,
                item.type,
                repr(item.mtime),
            )
        )
        for item in sorted(catalog, key=lambda value: value.relative_path)
    )
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()[:16]


class RelevantMemorySelector:
    def __init__(
        self,
        model_client: ModelClient,
        *,
        trace_recorder: TraceRecorder | None = None,
        max_items: int = 5,
    ) -> None:
        self._model_client = model_client
        self._trace_recorder = trace_recorder or TraceRecorder.noop()
        self._max_items = max(1, max_items)

    async def select(
        self,
        messages: tuple[dict[str, Any], ...],
        state: RuntimeState,
        catalog: tuple[LongTermMemoryFile, ...],
    ) -> tuple[LongTermMemoryFile, ...]:
        if not catalog:
            self._trace_recorder.event(
                "long_term_memory_selector_skipped",
                {
                    "user_turn_id": getattr(state, "user_turn_id", None),
                    "catalog_count": 0,
                    "catalog_fingerprint": catalog_fingerprint(catalog),
                    "selected_count": 0,
                    "selected_paths": (),
                    "cache_hit": False,
                    "reason": "empty_catalog",
                },
            )
            return ()
        started_at = perf_counter()
        fingerprint = catalog_fingerprint(catalog)
        started_attributes = {
            "user_turn_id": getattr(state, "user_turn_id", None),
            "catalog_count": len(catalog),
            "catalog_fingerprint": fingerprint,
            "cache_hit": False,
            **_provider_attributes(self._model_client),
        }
        if self._trace_recorder.is_debug:
            started_attributes["selector_input_metadata"] = {
                "message_count": len(messages),
                "recent_message_count": min(8, len(messages)),
                "catalog_count": len(catalog),
                "catalog_paths": tuple(item.relative_path for item in catalog),
                "message_roles": tuple(message.get("role") for message in messages[-8:]),
            }
        self._trace_recorder.event(
            "long_term_memory_selector_started",
            started_attributes,
        )
        allowed = {item.relative_path: item for item in catalog}
        usage = None
        stop_reason = None
        final_text = ""
        reasoning_text = ""
        try:
            snapshot = ContextSnapshot(
                system_prompt=SELECTOR_SYSTEM_PROMPT,
                messages=(
                    {
                        "role": "user",
                        "content": _selector_payload(messages, catalog),
                    },
                ),
                tool_schemas=(),
                usage_hints={
                    "request_overrides": {
                        # This is intentionally attached to the selector
                        # snapshot, never to RuntimeState/main-agent metadata.
                        "max_output_tokens": SELECTOR_MAX_OUTPUT_TOKENS,
                    }
                },
                transition=state.last_transition.value if state.last_transition else None,
            )
            snapshot.usage_hints["structured_output"] = _selector_structured_output(self._max_items)
            async for event in self._model_client.stream(snapshot):
                if event.usage is not None:
                    usage = event.usage
                if event.stop_reason is not None:
                    stop_reason = event.stop_reason
                if event.reasoning_text:
                    reasoning_text += event.reasoning_text
                if event.type == "message_completed":
                    final_text = event.final_text or _message_text(event.assistant_message)
                    reasoning_text = event.reasoning_text or reasoning_text
                    if event.usage is not None:
                        usage = event.usage
            selected, parse_success = self._parse_selection_result(final_text, allowed)
        except Exception as exc:
            self._trace_recorder.event(
                "long_term_memory_selector_failed",
                {
                    **started_attributes,
                    "duration_ms": _duration_ms(started_at),
                    "stop_reason": stop_reason,
                    "parse_success": False,
                    "final_text_chars": len(final_text),
                    "selected_count": 0,
                    "selected_paths": (),
                    "error_type": type(exc).__name__,
                    **_exception_attributes(exc),
                    **_usage_attributes(usage),
                },
            )
            return ()
        common_attributes = {
            **started_attributes,
            "duration_ms": _duration_ms(started_at),
            "stop_reason": stop_reason,
            "parse_success": parse_success,
            "final_text_chars": len(final_text),
            **_usage_attributes(usage),
        }
        if self._trace_recorder.is_debug:
            common_attributes["assistant_visible_text"] = final_text
            if reasoning_text:
                common_attributes["provider_reasoning_text"] = reasoning_text
                common_attributes["reasoning_text_status"] = "available"
            elif usage is not None and getattr(usage, "reasoning_tokens", None) is not None:
                common_attributes["reasoning_text_status"] = "not_returned"
            else:
                common_attributes["reasoning_text_status"] = "adapter_unavailable"
        if not parse_success:
            self._trace_recorder.event(
                "long_term_memory_selector_parse_failed",
                {
                    **common_attributes,
                    "selected_count": 0,
                    "selected_paths": (),
                },
            )
        self._trace_recorder.event(
            "long_term_memory_selector_completed",
            {
                **common_attributes,
                "selected_count": len(selected),
                "selected_paths": tuple(item.relative_path for item in selected),
            },
        )
        return selected

    def _parse_selection(
        self,
        text: str,
        allowed: dict[str, LongTermMemoryFile],
    ) -> tuple[LongTermMemoryFile, ...]:
        selected, _ = self._parse_selection_result(text, allowed)
        return selected

    def _parse_selection_result(
        self,
        text: str,
        allowed: dict[str, LongTermMemoryFile],
    ) -> tuple[tuple[LongTermMemoryFile, ...], bool]:
        try:
            parsed = json.loads(text.strip())
        except json.JSONDecodeError:
            return (), False
        if not isinstance(parsed, dict):
            return (), False
        raw = parsed.get("selected_memories")
        if not isinstance(raw, list):
            return (), False
        selected: list[LongTermMemoryFile] = []
        for item in raw:
            if not isinstance(item, str):
                continue
            memory = allowed.get(item)
            if memory is None or memory in selected:
                continue
            selected.append(memory)
            if len(selected) >= self._max_items:
                break
        return tuple(selected), True


def _selector_structured_output(max_items: int) -> dict[str, Any]:
    return {
        "name": "long_term_memory_selection",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "selected_memories": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": max_items,
                }
            },
            "required": ["selected_memories"],
            "additionalProperties": False,
        },
    }


def _selector_payload(
    messages: tuple[dict[str, Any], ...],
    catalog: tuple[LongTermMemoryFile, ...],
) -> str:
    recent = messages[-8:]
    conversation = "\n".join(
        f"{message.get('role', 'message')}: {_preview(_message_text(message), 500)}"
        for message in recent
    )
    catalog_lines = "\n".join(
        (
            f"- {item.relative_path} | name={item.name} | type={item.type} | "
            f"description={item.description} | mtime={item.mtime}"
        )
        for item in catalog[:200]
    )
    return f"Recent conversation:\n{conversation}\n\nMemory catalog:\n{catalog_lines}"


def _message_text(message: dict[str, Any] | None) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
        return " ".join(parts)
    return "" if content is None else str(content)


def _preview(text: str, limit: int) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else compact[: limit - 3].rstrip() + "..."


def _duration_ms(started_at: float) -> float:
    return round((perf_counter() - started_at) * 1000, 3)


def _provider_attributes(model_client: Any) -> dict[str, Any]:
    config = getattr(model_client, "config", None)
    attributes: dict[str, Any] = {}
    for key in ("provider_id", "model"):
        value = getattr(config, key, None)
        if value is not None:
            attributes[key] = value
    return attributes


def _exception_attributes(error: Exception) -> dict[str, Any]:
    attributes: dict[str, Any] = {}
    for key in ("provider_id", "status_code", "retryable", "retry_after_seconds"):
        value = getattr(error, key, None)
        if value is not None:
            attributes[key] = value
    return attributes


def _usage_attributes(usage: Any | None) -> dict[str, Any]:
    if usage is None:
        return {"reasoning_tokens_status": "adapter_unavailable"}
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    attributes = {
        "input_tokens": input_tokens,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": int(
            getattr(usage, "cache_creation_input_tokens", 0) or 0
        ),
        "uncached_input_tokens": max(0, input_tokens - cache_read),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
    }
    reasoning_tokens = getattr(usage, "reasoning_tokens", None)
    if isinstance(reasoning_tokens, int) and reasoning_tokens >= 0:
        attributes["reasoning_tokens"] = reasoning_tokens
        attributes["reasoning_tokens_status"] = "available"
    else:
        attributes["reasoning_tokens_status"] = "adapter_unavailable"
    visible_output_tokens = getattr(usage, "visible_output_tokens", None)
    if isinstance(visible_output_tokens, int) and visible_output_tokens >= 0:
        attributes["visible_output_tokens"] = visible_output_tokens
    return attributes
