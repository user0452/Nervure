"""Context compaction preparer used before model calls."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import re
import uuid
from time import perf_counter
from typing import Any

from core.runtime_state import RuntimeState
from services.compaction.token_estimator import (
    estimate_messages_tokens,
    estimate_snapshot_tokens,
)
from services.compaction.types import (
    CompactionConfig,
    CompactionResult,
    CompactionTrigger,
)
from services.context.message_store import MessageStore
from services.context.current_model_context import CurrentModelContext
from services.context.projector import ContextProjector
from services.context.snapshot import ContextSnapshot, PreparedContext
from services.hooks import HookEvent, HookRegistry
from services.model.client import ModelClient
from services.model.stream import ModelStreamEvent
from services.model.types import ProviderError
from services.observability import TraceRecorder
from utils.toolResultStorage import ToolResultStorage

MICROCOMPACT_PLACEHOLDER = (
    "[Old tool result content cleared. Re-read the referenced file or rerun the "
    "tool if exact output is needed.]"
)


class ContextCompactionService:
    def __init__(
        self,
        *,
        config: CompactionConfig | None = None,
        message_store: MessageStore | None = None,
        result_store: ToolResultStorage | None = None,
        model_client: ModelClient | None = None,
        current_model_context: CurrentModelContext | None = None,
        hooks: HookRegistry | None = None,
        trace_recorder: TraceRecorder | None = None,
    ) -> None:
        self.config = config or CompactionConfig()
        self._message_store = message_store
        self._result_store = result_store
        self._model_client = model_client
        self._current_model_context = current_model_context
        self._hooks = hooks or HookRegistry()
        self._trace_recorder = trace_recorder or TraceRecorder.noop()

    def bind_runtime(
        self,
        *,
        message_store: MessageStore | None = None,
        result_store: ToolResultStorage | None = None,
        model_client: ModelClient | None = None,
        current_model_context: CurrentModelContext | None = None,
    ) -> None:
        if message_store is not None:
            self._message_store = message_store
        if result_store is not None:
            self._result_store = result_store
        if model_client is not None:
            self._model_client = model_client
        if current_model_context is not None:
            self._current_model_context = current_model_context

    async def prepare(
        self,
        messages: tuple[dict[str, Any], ...],
        state: RuntimeState,
    ) -> PreparedContext:
        result = await self.prepare_for_model(messages, state)
        if (
            result.token_after >= self.config.auto_compact_threshold_tokens
            and state.metadata.get("query_source") != "compact"
        ):
            compacted = await self.maybe_auto_compact(messages, state)
            if compacted is not None:
                return _prepared_context_from_result(compacted)
        return _prepared_context_from_result(result)

    async def prepare_for_model(
        self,
        messages: tuple[dict[str, Any], ...],
        state: RuntimeState,
    ) -> CompactionResult:
        """Run cheap model-visible projection without rewriting MessageStore."""

        token_before = estimate_messages_tokens(messages)
        projected = self._apply_tool_result_budget(messages)
        projected = self._snip(projected)
        projected = self._microcompact(projected)
        token_after = estimate_messages_tokens(projected)
        result = CompactionResult(
            trigger=CompactionTrigger.MICRO,
            messages=projected,
            token_before=token_before,
            token_after=token_after,
            transcript_refs=_stored_result_refs(projected),
            metadata={
                "cheap_pipeline": True,
                "auto_compact_threshold_tokens": self.config.auto_compact_threshold_tokens,
            },
        )
        state.metadata["last_compaction"] = {
            "trigger": result.trigger.value,
            "token_before": token_before,
            "token_after": token_after,
            "message_count_before": len(messages),
            "message_count_after": len(projected),
        }
        self._trace_recorder.event(
            "compact_prepare",
            {
                "trigger": result.trigger.value,
                "token_before": token_before,
                "token_after": token_after,
                "message_count_before": len(messages),
                "message_count_after": len(projected),
            },
        )
        return result

    async def maybe_auto_compact(
        self,
        messages: tuple[dict[str, Any], ...],
        state: RuntimeState,
    ) -> CompactionResult | None:
        prepared = await self.prepare_for_model(messages, state)
        if prepared.token_after < self.config.auto_compact_threshold_tokens:
            return None
        if _auto_compact_failures(state) >= self.config.max_consecutive_auto_compact_failures:
            self._trace_recorder.event(
                "compact_auto_decision",
                {
                    "decision": "skipped_circuit_open",
                    "failure_count": _auto_compact_failures(state),
                    "token_after": prepared.token_after,
                    "threshold": self.config.auto_compact_threshold_tokens,
                },
            )
            return None
        self._trace_recorder.event(
            "compact_auto_decision",
            {
                "decision": "compact",
                "token_after": prepared.token_after,
                "threshold": self.config.auto_compact_threshold_tokens,
            },
        )
        try:
            full_result = await self._full_compact(
                messages,
                state,
                trigger=CompactionTrigger.AUTO_FULL,
            )
            _reset_auto_compact_failures(state)
            return full_result
        except Exception as exc:
            _increment_auto_compact_failures(state)
            await self._compact_failed(
                state,
                trigger=CompactionTrigger.AUTO_FULL,
                error=exc,
                token_before=prepared.token_after,
                message_count=len(messages),
            )
            return None

    async def ensure_final_context_budget(
        self,
        snapshot: ContextSnapshot,
        state: RuntimeState,
    ) -> bool:
        """Compact raw history when the fully projected request is over budget."""

        projected_tokens = estimate_snapshot_tokens(snapshot)
        state.metadata["final_projected_context_tokens"] = projected_tokens
        self._trace_recorder.event(
            "final_context_budget",
            {
                "projected_tokens": projected_tokens,
                "threshold": self.config.auto_compact_threshold_tokens,
            },
        )
        if (
            projected_tokens < self.config.auto_compact_threshold_tokens
            or state.metadata.get("query_source") == "compact"
            or _auto_compact_failures(state)
            >= self.config.max_consecutive_auto_compact_failures
        ):
            return False
        messages = self._active_messages()
        try:
            raw_snapshot = await self._raw_compact_parent_snapshot(messages, state)
            await self._full_compact(
                messages,
                state,
                trigger=CompactionTrigger.AUTO_FULL,
                parent_snapshot=raw_snapshot,
            )
        except Exception as exc:
            _increment_auto_compact_failures(state)
            await self._compact_failed(
                state,
                trigger=CompactionTrigger.AUTO_FULL,
                error=exc,
                token_before=projected_tokens,
                message_count=len(messages),
            )
            return False
        _reset_auto_compact_failures(state)
        return True

    def validate_final_context_budget(
        self,
        snapshot: ContextSnapshot,
        state: RuntimeState,
    ) -> None:
        """Reject a fully projected request that still exceeds the trigger."""

        projected_tokens = estimate_snapshot_tokens(snapshot)
        state.metadata["final_projected_context_tokens"] = projected_tokens
        if projected_tokens >= self.config.auto_compact_threshold_tokens:
            raise ProviderError(
                "Final projected context remains over budget after compaction.",
                error_type="context_limit_exceeded",
                metadata={
                    "source": "final_context_budget",
                    "projected_tokens": projected_tokens,
                    "threshold": self.config.auto_compact_threshold_tokens,
                },
            )

    async def manual_compact(
        self,
        state: RuntimeState,
        *,
        focus: str | None = None,
    ) -> CompactionResult:
        messages = self._active_messages()
        try:
            return await self._full_compact(
                messages,
                state,
                trigger=CompactionTrigger.MANUAL,
                focus=focus,
            )
        except Exception as exc:
            await self._compact_failed(
                state,
                trigger=CompactionTrigger.MANUAL,
                error=exc,
                token_before=estimate_messages_tokens(messages),
                message_count=len(messages),
            )
            raise

    async def reactive_compact(
        self,
        state: RuntimeState,
        *,
        error: ProviderError,
    ) -> CompactionResult:
        messages = self._active_messages()
        try:
            return await self._full_compact(
                messages,
                state,
                trigger=CompactionTrigger.REACTIVE,
                focus=error.error_type or error.message,
            )
        except Exception as exc:
            await self._compact_failed(
                state,
                trigger=CompactionTrigger.REACTIVE,
                error=exc,
                token_before=estimate_messages_tokens(messages),
                message_count=len(messages),
            )
            raise

    def _active_messages(self) -> tuple[dict[str, Any], ...]:
        if self._message_store is None:
            raise RuntimeError("compaction requires a bound MessageStore")
        return self._message_store.current_messages()

    async def _full_compact(
        self,
        messages: tuple[dict[str, Any], ...],
        state: RuntimeState,
        *,
        trigger: CompactionTrigger,
        focus: str | None = None,
        parent_snapshot: ContextSnapshot | None = None,
    ) -> CompactionResult:
        started = perf_counter()
        token_before = estimate_messages_tokens(messages)
        boundary_id = _boundary_id()
        hook_metadata = await self._pre_compact(
            state,
            trigger=trigger,
            token_before=token_before,
            message_count=len(messages),
            focus=focus,
        )
        prompt = _compact_prompt(
            focus=focus,
            extra_instructions=hook_metadata.get("summary_instructions"),
        )
        if parent_snapshot is None:
            parent_snapshot = await self._compact_parent_snapshot(messages, state)
        compact_snapshot = _append_compact_instruction(
            parent_snapshot,
            prompt,
            max_output_tokens=self.config.compact_summary_reserve_tokens,
        )
        projected_tokens = _estimate_snapshot_input_tokens(compact_snapshot)
        self._trace_recorder.event(
            "compact_triggered",
            {
                "trigger": trigger.value,
                "context_window_tokens": self.config.context_window_tokens,
                "trigger_ratio": self.config.compact_trigger_ratio,
                "trigger_tokens": self.config.compact_trigger_tokens,
                "projected_tokens_before": projected_tokens,
            },
        )
        self._trace_recorder.event(
            "compact_request",
            {
                "trigger": trigger.value,
                "parent_snapshot_message_count": len(parent_snapshot.messages),
                "compact_request_message_count": len(compact_snapshot.messages),
                "estimated_input_tokens": projected_tokens,
                "requested_max_output_tokens": self.config.compact_summary_reserve_tokens,
            },
        )
        summary, usage = await self._request_compact_summary(compact_snapshot)
        tail = self._recent_tail(messages)
        compacted = _compact_messages(
            trigger=trigger,
            boundary_id=boundary_id,
            summary=summary,
            tail=tail,
            source="full",
        )
        token_after = estimate_messages_tokens(compacted)
        stored = self._replace_active_messages(
            compacted,
            trigger=trigger,
            boundary_id=boundary_id,
            metadata={**hook_metadata, "source": "full"},
        )
        compaction_result = CompactionResult(
            trigger=trigger,
            messages=tuple(stored),
            token_before=token_before,
            token_after=token_after,
            metadata={
                "boundary_id": boundary_id,
                "source": "full",
                "summary_output_tokens": usage.output_tokens if usage else 0,
                "recent_tail_tokens": estimate_messages_tokens(tail),
                "compacted_context_tokens": token_after,
                "compression_ratio": (
                    token_after / token_before if token_before else 0.0
                ),
            },
        )
        await self._post_compact(state, compaction_result, messages_before=len(messages))
        self._trace_recorder.event(
            "compact_completed",
            {
                "trigger": trigger.value,
                "summary_output_tokens": usage.output_tokens if usage else 0,
                "recent_tail_tokens": estimate_messages_tokens(tail),
                "compacted_context_tokens": token_after,
                "compression_ratio": (
                    token_after / token_before if token_before else 0.0
                ),
                **_usage_metadata(usage),
                "duration_ms": round((perf_counter() - started) * 1000, 3),
            },
        )
        return compaction_result

    async def _compact_parent_snapshot(
        self,
        messages: tuple[dict[str, Any], ...],
        state: RuntimeState,
    ) -> ContextSnapshot:
        if self._current_model_context is not None:
            snapshot = self._current_model_context.snapshot_copy()
            if snapshot is not None:
                return snapshot
        # Safe startup/recovery fallback: use cheap projected messages and no
        # synthetic system/tools. The normal path always prefers the real
        # parent request snapshot for prompt-cache reuse.
        prepared = await self.prepare_for_model(messages, state)
        return ContextSnapshot(system_prompt="", messages=prepared.messages)

    async def _raw_compact_parent_snapshot(
        self,
        messages: tuple[dict[str, Any], ...],
        state: RuntimeState,
    ) -> ContextSnapshot:
        prepared = await self.prepare_for_model(messages, state)
        return ContextSnapshot(system_prompt="", messages=prepared.messages)

    async def _request_compact_summary(
        self,
        snapshot: ContextSnapshot,
    ) -> tuple[str, Any | None]:
        if self._model_client is None:
            raise RuntimeError("full compact requires a bound ModelClient")
        completed: ModelStreamEvent | None = None
        async for event in self._model_client.stream(snapshot):
            if event.type == "error":
                raise RuntimeError(event.text or "compact model request failed")
            if event.type == "message_completed":
                completed = event
        if completed is None:
            raise RuntimeError("compact model request returned no completed message")
        tool_calls = completed.metadata.get("tool_calls")
        if tool_calls:
            raise RuntimeError("compact model request returned tool calls")
        if completed.output_interrupted:
            raise RuntimeError("compact model request output was interrupted")
        summary = _extract_summary(completed.final_text)
        if not summary:
            raise RuntimeError("compact model request returned an empty summary")
        return summary, completed.usage

    def _recent_tail(
        self,
        messages: tuple[dict[str, Any], ...],
    ) -> tuple[dict[str, Any], ...]:
        selected: list[dict[str, Any]] = []
        token_count = 0
        for message in reversed(messages):
            projected = deepcopy(message)
            next_tokens = estimate_messages_tokens([projected])
            if selected and token_count + next_tokens > self.config.recent_tail_budget_tokens:
                break
            selected.insert(0, projected)
            token_count += next_tokens
            if token_count >= self.config.recent_tail_budget_tokens:
                break
        start_index = max(0, len(messages) - len(selected))
        adjusted = ContextProjector().adjust_start_index_to_preserve_tool_pairs(
            messages,
            start_index,
        )
        # Keep the user message that started the newest turn whenever the
        # token budget cuts into an assistant/tool exchange.  This may add a
        # small amount beyond the nominal tail budget, but avoids leaving the
        # model with an orphaned tool exchange or no task anchor.
        while adjusted > 0 and messages[adjusted].get("role") != "user":
            adjusted -= 1
        return tuple(deepcopy(message) for message in messages[adjusted:])

    def _replace_active_messages(
        self,
        messages: tuple[dict[str, Any], ...],
        *,
        trigger: CompactionTrigger,
        boundary_id: str,
        metadata: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if self._message_store is None:
            raise RuntimeError("compaction requires a bound MessageStore")
        stored = self._message_store.replace_messages_for_compaction(
            messages,
            reason=trigger.value,
            metadata={"boundary_id": boundary_id, **metadata},
        )
        return stored

    async def _pre_compact(
        self,
        state: RuntimeState,
        *,
        trigger: CompactionTrigger,
        token_before: int,
        message_count: int,
        focus: str | None = None,
    ) -> dict[str, Any]:
        transcript_path = None
        if self._message_store is not None:
            transcript_path = self._message_store.transcript_store.messages_path
        result = await self._hooks.run(
            HookEvent.PRE_COMPACT,
            {
                "trigger": trigger.value,
                "token_before": token_before,
                "message_count": message_count,
                "transcript_path": transcript_path,
                "session_id": state.session_id,
                "turn_count": state.turn_count,
                "focus": focus,
            },
        )
        return dict(result.metadata)

    async def _post_compact(
        self,
        state: RuntimeState,
        result: CompactionResult,
        *,
        messages_before: int,
    ) -> None:
        state.metadata["last_compaction"] = {
            "trigger": result.trigger.value,
            "token_before": result.token_before,
            "token_after": result.token_after,
            "message_count_before": messages_before,
            "message_count_after": len(result.messages),
            "boundary_id": result.metadata.get("boundary_id"),
        }
        await self._hooks.run(
            HookEvent.POST_COMPACT,
            {
                "trigger": result.trigger.value,
                "token_before": result.token_before,
                "token_after": result.token_after,
                "messages_before": messages_before,
                "messages_after": len(result.messages),
                "boundary_id": result.metadata.get("boundary_id"),
                "session_id": state.session_id,
            },
        )

    async def _compact_failed(
        self,
        state: RuntimeState,
        *,
        trigger: CompactionTrigger,
        error: Exception,
        token_before: int,
        message_count: int,
    ) -> None:
        self._trace_recorder.event(
            "compact_failed",
            {
                "trigger": trigger.value,
                "error_type": type(error).__name__,
                "token_before": token_before,
                "message_count": message_count,
                "failure_count": _auto_compact_failures(state),
            },
        )
        await self._hooks.run(
            HookEvent.COMPACT_FAILED,
            {
                "trigger": trigger.value,
                "error_type": type(error).__name__,
                "token_before": token_before,
                "message_count": message_count,
                "session_id": state.session_id,
            },
        )

    def _apply_tool_result_budget(
        self,
        messages: tuple[dict[str, Any], ...],
    ) -> tuple[dict[str, Any], ...]:
        projected: list[dict[str, Any]] = []
        stored_count = 0
        for message in messages:
            next_message = deepcopy(message)
            if next_message.get("role") != "tool_result":
                projected.append(next_message)
                continue
            content = next_message.get("content")
            if not isinstance(content, str):
                projected.append(next_message)
                continue
            if len(content) <= self.config.tool_result_budget_chars:
                projected.append(next_message)
                continue

            preview = content[: self.config.tool_result_preview_chars]
            metadata = dict(next_message.get("metadata") or {})
            metadata.update(
                {
                    "result_truncated": True,
                    "original_size_chars": len(content),
                    "max_result_size_chars": self.config.tool_result_budget_chars,
                }
            )
            if self._result_store is not None:
                ref = self._result_store.persist_tool_result(
                    tool_call_id=str(next_message.get("tool_call_id", "")),
                    tool_name=str(next_message.get("tool_name", "")),
                    content=content,
                )
                next_message["content"] = self._result_store.format_model_reference(
                    ref,
                    preview=preview,
                )
                metadata.update(
                    self._result_store.stored_result_metadata(
                        ref,
                        max_result_size_chars=self.config.tool_result_budget_chars,
                    )
                )
                stored_count += 1
            else:
                next_message["content"] = preview
            next_message["metadata"] = metadata
            projected.append(next_message)

        self._trace_recorder.event(
            "compact_result_budget",
            {"stored_result_count": stored_count, "message_count": len(messages)},
        )
        return tuple(projected)

    def _snip(
        self,
        messages: tuple[dict[str, Any], ...],
    ) -> tuple[dict[str, Any], ...]:
        if len(messages) <= self.config.snip_max_messages:
            return tuple(deepcopy(message) for message in messages)
        return ContextProjector(max_messages=self.config.snip_max_messages).project(messages)

    def _microcompact(
        self,
        messages: tuple[dict[str, Any], ...],
    ) -> tuple[dict[str, Any], ...]:
        tool_result_indexes = [
            index
            for index, message in enumerate(messages)
            if message.get("role") == "tool_result"
        ]
        if self.config.microcompact_keep_recent <= 0:
            keep: set[int] = set()
        else:
            keep = set(tool_result_indexes[-self.config.microcompact_keep_recent :])
        compacted: list[dict[str, Any]] = []
        compacted_count = 0
        for index, message in enumerate(messages):
            next_message = deepcopy(message)
            if index in keep or next_message.get("role") != "tool_result":
                compacted.append(next_message)
                continue
            metadata = dict(next_message.get("metadata") or {})
            if metadata.get("result_stored") is True:
                compacted.append(next_message)
                continue
            content = next_message.get("content")
            if isinstance(content, str) and content:
                metadata.update(
                    {
                        "microcompacted": True,
                        "original_size_chars": len(content),
                    }
                )
                next_message["content"] = MICROCOMPACT_PLACEHOLDER
                next_message["metadata"] = metadata
                compacted_count += 1
            compacted.append(next_message)

        self._trace_recorder.event(
            "compact_micro",
            {
                "microcompacted_count": compacted_count,
                "keep_recent": self.config.microcompact_keep_recent,
            },
        )
        return tuple(compacted)


def _stored_result_refs(messages: tuple[dict[str, Any], ...]) -> tuple[str, ...]:
    refs: list[str] = []
    for message in messages:
        metadata = message.get("metadata")
        if not isinstance(metadata, dict):
            continue
        ref = metadata.get("stored_result_path")
        if isinstance(ref, str):
            refs.append(ref)
    return tuple(refs)


def _prepared_context_from_result(result: CompactionResult) -> PreparedContext:
    return PreparedContext(
        messages=result.messages,
        usage_hints={
            "compaction_trigger": result.trigger.value,
            "token_before": result.token_before,
            "token_after": result.token_after,
            **result.metadata,
        },
        transcript_refs=result.transcript_refs,
    )


def _append_compact_instruction(
    snapshot: ContextSnapshot,
    instruction: str,
    *,
    max_output_tokens: int,
) -> ContextSnapshot:
    """Append one user instruction while preserving the parent's prefix."""

    usage_hints = deepcopy(snapshot.usage_hints)
    request_overrides = dict(usage_hints.get("request_overrides") or {})
    request_overrides["max_output_tokens"] = max_output_tokens
    usage_hints["request_overrides"] = request_overrides
    messages = tuple(deepcopy(snapshot.messages)) + (
        {
            "role": "user",
            "content": instruction,
            "metadata": {"is_compact_instruction": True},
        },
    )
    return replace(snapshot, messages=messages, usage_hints=usage_hints)


def _estimate_snapshot_input_tokens(snapshot: ContextSnapshot) -> int:
    """Estimate provider-visible input without counting output reserve hints."""

    return estimate_snapshot_tokens(
        replace(snapshot, usage_hints={}, transcript_refs=())
    )


def _usage_metadata(usage: Any | None) -> dict[str, int]:
    if usage is None:
        return {}
    return {
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "cache_read_input_tokens": int(
            getattr(usage, "cache_read_input_tokens", 0) or 0
        ),
        "uncached_input_tokens": int(
            max(
                0,
                (getattr(usage, "input_tokens", 0) or 0)
                - (getattr(usage, "cache_read_input_tokens", 0) or 0),
            )
        ),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
    }


def _compact_messages(
    *,
    trigger: CompactionTrigger,
    boundary_id: str,
    summary: str,
    tail: tuple[dict[str, Any], ...],
    source: str,
) -> tuple[dict[str, Any], ...]:
    boundary = {
        "role": "user",
        "content": (
            f"[Compact boundary: trigger={trigger.value}, "
            f"boundary_id={boundary_id}, source={source}]"
        ),
        "metadata": {
            "is_compact_boundary": True,
            "compact_boundary_id": boundary_id,
            "compact_trigger": trigger.value,
            "compact_source": source,
        },
    }
    summary_message = {
        "role": "user",
        "content": (
            "This session is being continued from a compacted context.\n\n"
            "Summary:\n"
            f"{summary.strip()}"
        ),
        "metadata": {
            "is_compact_summary": True,
            "compact_boundary_id": boundary_id,
            "compact_trigger": trigger.value,
            "compact_source": source,
        },
    }
    return (boundary, summary_message, *tuple(deepcopy(message) for message in tail))


def _compact_prompt(
    *,
    focus: str | None,
    extra_instructions: Any,
) -> str:
    instructions = str(extra_instructions).strip() if extra_instructions else ""
    focus_text = focus.strip() if isinstance(focus, str) and focus.strip() else ""
    lines = [
        "Compact the current conversation into a high-information continuation state.",
        "Do not call tools. This is one text-only summarization request; return only the summary.",
        "The system prompt, tool definitions/schemas, fixed harness instructions, "
        "and runtime-injected instruction/long-term memory content are input prefix "
        "only and must not be copied into the summary.",
        "Do not summarize unrelated chatter, full tool results, shell logs, or hidden reasoning.",
        "Include these sections:",
        "# Task",
        "# Current State",
        "# Important Decisions",
        "# Files",
        "# Code / Architecture",
        "# Errors / Findings",
        "# Verification",
        "# User Constraints",
        "# Next Step",
    ]
    if focus_text:
        lines.append(f"Focus: {focus_text}")
    if instructions:
        lines.append(f"Additional summary instructions: {instructions}")
    lines.append("Wrap the final summary in <summary>...</summary> if useful.")
    return "\n".join(lines)


def _extract_summary(text: str) -> str:
    without_analysis = re.sub(
        r"<analysis>.*?</analysis>",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    ).strip()
    match = re.search(
        r"<summary>(.*?)</summary>",
        without_analysis,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match:
        return match.group(1).strip()
    return without_analysis


def _boundary_id() -> str:
    return uuid.uuid4().hex[:12]


def _auto_compact_failures(state: RuntimeState) -> int:
    value = state.metadata.get("auto_compact_failure_count", 0)
    return value if isinstance(value, int) else 0


def _increment_auto_compact_failures(state: RuntimeState) -> None:
    state.metadata["auto_compact_failure_count"] = _auto_compact_failures(state) + 1


def _reset_auto_compact_failures(state: RuntimeState) -> None:
    state.metadata["auto_compact_failure_count"] = 0
