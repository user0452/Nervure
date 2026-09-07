"""Deferred, event-driven long-term memory consolidation via restricted fork child.

The original per-turn ``maybe_extract_after_model_response`` path is preserved
for tests, but production wiring should use :meth:`mark_dirty` on
``TURN_STOPPED`` and let the four triggers (idle, full_compact,
session_close/switch, explicit) drive :meth:`consolidate`.

A persistent watermark (``last_ltm_consolidated_message_id``) ensures each
consolidation only processes messages after the previous one, and advances
only on successful completion (including "nothing worth saving" results).
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from core.runtime_state import RuntimeState
from services.memory.auto_store import LongTermMemoryStore
from services.subagents.types import SubagentRequest, SubagentResult
from services.observability import TraceRecorder

LONG_TERM_MEMORY_EXTRACTION_KEY = "long_term_memory_extraction"
WATERMARK_FILENAME = "ltm_watermark.json"

# Consolidation trigger identifiers (used in trace metadata).
TRIGGER_IDLE = "idle"
TRIGGER_FULL_COMPACT = "full_compact"
TRIGGER_SESSION_CLOSE = "session_close"
TRIGGER_SESSION_SWITCH = "session_switch"
TRIGGER_EXPLICIT = "explicit"


@dataclass(frozen=True)
class LongTermMemoryExtractionPolicy:
    enabled: bool = True
    max_turns: int = 5
    idle_debounce_seconds: float = 45.0
    context_prefix_messages: int = 3


@dataclass(frozen=True)
class LongTermMemoryExtractionJob:
    messages: tuple[dict[str, Any], ...]
    parent_session_id: str
    parent_tool_call_id: str
    allowed_memory_dir: str
    max_turns: int
    prompt: str


class LongTermMemorySubagentRunner(Protocol):
    async def run(self, request: SubagentRequest) -> SubagentResult: ...


class MessageStoreLike(Protocol):
    """Minimal interface the consolidation service needs from MessageStore."""

    def current_messages(self) -> tuple[dict[str, Any], ...]: ...

    def current_message_ids(self) -> tuple[str, ...]: ...

    @property
    def transcript_store(self) -> Any: ...


class LongTermMemoryExtractionService:
    """Coordinates deferred LTM consolidation with watermark + coalescing."""

    def __init__(
        self,
        store: LongTermMemoryStore,
        *,
        subagent_runner: LongTermMemorySubagentRunner,
        policy: LongTermMemoryExtractionPolicy | None = None,
        trace_recorder: TraceRecorder | None = None,
        message_store: MessageStoreLike | None = None,
        background_task_manager: Any | None = None,
    ) -> None:
        self.store = store
        self._subagent_runner = subagent_runner
        self._policy = policy or LongTermMemoryExtractionPolicy()
        self._trace_recorder = trace_recorder or TraceRecorder.noop()
        self._message_store = message_store
        self._background_task_manager = background_task_manager
        self._lock = asyncio.Lock()
        self._idle_timer: Any = None  # asyncio.TimerHandle | None
        self._pending_snapshot: tuple[tuple[dict[str, Any], ...], tuple[str, ...]] | None = None
        self._session_id: str | None = None
        self._scheduled_tasks: set[asyncio.Task[None]] = set()

    def bind_message_store(self, message_store: MessageStoreLike) -> None:
        """Rebind only after the previous session's consolidation is flushed."""
        self._cancel_idle_timer()
        self._message_store = message_store
        self._pending_snapshot = None
        self._session_id = None

    def bind_subagent_runner(self, runner: LongTermMemorySubagentRunner) -> None:
        self._subagent_runner = runner

    # ------------------------------------------------------------------
    # Legacy per-turn API (kept for backward-compatible tests)
    # ------------------------------------------------------------------

    async def maybe_extract_after_model_response(
        self,
        messages: tuple[dict[str, Any], ...],
        state: RuntimeState,
        *,
        assistant_message: dict[str, Any],
        tool_calls: tuple[Any, ...],
        usage: Any | None = None,
    ) -> None:
        """Immediately extract after a model response. Legacy; tests only."""
        _ = assistant_message, usage
        job = self.prepare_extraction_job(
            messages,
            state,
            tool_calls=tool_calls,
        )
        if job is None:
            return
        await self.run_extraction_job(job, state)

    def prepare_extraction_job(
        self,
        messages: tuple[dict[str, Any], ...],
        state: RuntimeState,
        *,
        tool_calls: tuple[Any, ...],
        _check_lock: bool = True,
    ) -> LongTermMemoryExtractionJob | None:
        decision = should_extract_long_term_memory(
            messages,
            state,
            tool_calls=tool_calls,
            enabled=self._policy.enabled,
        )
        _merge_metadata(
            state,
            {
                "last_decision": decision,
                "running": self._lock.locked(),
                "memory_dir": str(self.store.memory_dir),
            },
        )
        if decision != "extract":
            self._trace_recorder.event(
                "long_term_memory_extraction_decision",
                {"status": "skipped", "reason": decision},
            )
            if decision == "main_agent_memory_write":
                _advance_cursor(messages, state)
            return None
        if _check_lock and self._lock.locked():
            _merge_metadata(state, {"last_status": "skipped_running", "running": True})
            return None
        return LongTermMemoryExtractionJob(
            messages=messages,
            parent_session_id=state.session_id,
            parent_tool_call_id=f"long-term-memory-{state.turn_count}",
            allowed_memory_dir=str(self.store.memory_dir.resolve()),
            max_turns=self._policy.max_turns,
            prompt=_extraction_prompt(self.store, messages, state),
        )

    async def run_extraction_job(
        self,
        job: LongTermMemoryExtractionJob,
        state: RuntimeState,
    ) -> None:
        async with self._lock:
            await self._execute_extraction(job, state)

    async def _execute_extraction(
        self,
        job: LongTermMemoryExtractionJob,
        state: RuntimeState,
    ) -> None:
        """Run the extraction child without acquiring the lock."""
        _merge_metadata(
            state,
            {
                "last_status": "running",
                "last_started_at": _now(),
                "running": True,
            },
        )
        try:
            self.store.ensure_exists()
            request = SubagentRequest(
                prompt=job.prompt,
                subagent_type=None,
                parent_session_id=job.parent_session_id,
                parent_tool_call_id=job.parent_tool_call_id,
                metadata={
                    "purpose": "long_term_memory_extraction",
                    "allowed_memory_dir": job.allowed_memory_dir,
                    "max_turns": job.max_turns,
                },
            )
            result = await self._subagent_runner.run(request)
            if result.is_error:
                raise RuntimeError(result.final_text)
            _advance_cursor(job.messages, state)
            _merge_metadata(
                state,
                {
                    "last_status": "success",
                    "last_completed_at": _now(),
                    "last_result_session_id": result.session_id,
                    "running": False,
                },
            )
            self._trace_recorder.event(
                "long_term_memory_extraction_completed",
                {
                    "status": "success",
                    "memory_dir": self.store.memory_dir,
                    "child_session_id": result.session_id,
                },
            )
        except asyncio.CancelledError:
            _merge_metadata(
                state,
                {
                    "last_status": "killed",
                    "last_completed_at": _now(),
                    "running": False,
                },
            )
            self._trace_recorder.event(
                "long_term_memory_extraction_cancelled",
                {"memory_dir": self.store.memory_dir},
            )
            raise
        except Exception as exc:
            _merge_metadata(
                state,
                {
                    "last_status": "failed",
                    "last_completed_at": _now(),
                    "last_error_type": type(exc).__name__,
                    "running": False,
                },
            )
            self._trace_recorder.event(
                "long_term_memory_extraction_failed",
                {"error_type": type(exc).__name__, "memory_dir": self.store.memory_dir},
            )
            raise

    # ------------------------------------------------------------------
    # Deferred consolidation API
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._lock.locked()

    @property
    def has_pending(self) -> bool:
        return self._pending_snapshot is not None

    def mark_dirty(
        self,
        state: RuntimeState,
        *,
        messages: tuple[dict[str, Any], ...] | None = None,
        tool_calls: tuple[Any, ...] = (),
    ) -> None:
        """Called on ``TURN_STOPPED``.

        Marks unconsolidated messages and resets the idle debounce timer.
        If the main agent explicitly wrote memory this turn, triggers
        immediate consolidation instead of waiting for idle.
        """
        _ = messages, tool_calls
        if not self._policy.enabled:
            return
        if state.metadata.get("long_term_memory_extraction_agent") is True:
            return
        if state.metadata.get("is_fork_child") is True:
            return

        # Explicit memory write this turn -> consolidate immediately.
        if _main_agent_wrote_memory_this_turn(state):
            self._trace_recorder.event(
                "ltm_consolidation_scheduled",
                {"trigger": TRIGGER_EXPLICIT, "reason": "main_agent_memory_write"},
            )
            self.schedule_consolidation(trigger=TRIGGER_EXPLICIT, state=state)
            return

        self.schedule_idle(state)

    def schedule_idle(self, state: RuntimeState) -> None:
        """Reset the idle debounce timer; fires consolidation after idle seconds."""
        if not self._policy.enabled:
            return
        self._cancel_idle_timer()
        self._session_id = state.session_id
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._idle_timer = loop.call_later(
            self._policy.idle_debounce_seconds,
            self._on_idle_fire,
            state,
        )
        self._trace_recorder.event(
            "ltm_consolidation_scheduled",
            {
                "trigger": TRIGGER_IDLE,
                "debounce_seconds": self._policy.idle_debounce_seconds,
            },
        )

    def cancel_idle(self) -> None:
        """New foreground activity ends the previous idle interval."""
        self._cancel_idle_timer()

    def _on_idle_fire(self, state: RuntimeState) -> None:
        self._idle_timer = None
        if state.session_id != self._session_id:
            return  # stale timer from a previous session
        self.schedule_consolidation(trigger=TRIGGER_IDLE, state=state)

    def schedule_consolidation(
        self,
        *,
        trigger: str,
        state: RuntimeState,
        messages: tuple[dict[str, Any], ...] | None = None,
        message_ids: tuple[str, ...] | None = None,
    ) -> None:
        """Capture now and retain the task so session flush can drain it."""
        self._cancel_idle_timer()
        if messages is None or message_ids is None:
            messages, message_ids = self._snapshot(state)
        task = asyncio.create_task(
            self.consolidate(
                trigger=trigger,
                state=state,
                messages=messages,
                message_ids=message_ids,
            )
        )
        self._scheduled_tasks.add(task)
        task.add_done_callback(self._on_scheduled_done)

    def _on_scheduled_done(self, task: asyncio.Task[None]) -> None:
        self._scheduled_tasks.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            self._trace_recorder.event(
                "ltm_consolidation_failed",
                {"error_type": type(error).__name__},
            )

    def _cancel_idle_timer(self) -> None:
        if self._idle_timer is not None:
            self._idle_timer.cancel()
            self._idle_timer = None

    async def consolidate(
        self,
        *,
        trigger: str,
        state: RuntimeState,
        messages: tuple[dict[str, Any], ...] | None = None,
        message_ids: tuple[str, ...] | None = None,
    ) -> None:
        """Run one consolidation job; coalesces concurrent triggers.

        If a consolidation is already running, snapshots are coalesced and
        processed immediately after the current run finishes.
        """
        self._cancel_idle_timer()

        if messages is None or message_ids is None:
            messages, message_ids = self._snapshot(state)

        # Retain messages from a pending pre-compact snapshot even if a later
        # trigger sees a replacement chain with different UUIDs.
        if self._lock.locked():
            if self._pending_snapshot is not None:
                pending_messages, pending_ids = self._pending_snapshot
                known_ids = set(pending_ids)
                additions = [
                    (message, message_id)
                    for message, message_id in zip(messages, message_ids)
                    if message_id not in known_ids
                ]
                messages = pending_messages + tuple(item[0] for item in additions)
                message_ids = pending_ids + tuple(item[1] for item in additions)
            self._pending_snapshot = (messages, message_ids)
            self._trace_recorder.event(
                "ltm_consolidation_scheduled",
                {
                    "trigger": trigger,
                    "coalesced": True,
                    "running": True,
                    "pending": True,
                    "message_count": len(messages),
                },
            )
            return

        async with self._lock:
            while True:
                await self._run_one_consolidation(
                    trigger=trigger,
                    state=state,
                    messages=messages,
                    message_ids=message_ids,
                )
                if self._pending_snapshot is not None:
                    messages, message_ids = self._pending_snapshot
                    self._pending_snapshot = None
                    trigger = TRIGGER_IDLE  # follow-up is effectively idle
                    continue
                break

    async def flush(
        self,
        *,
        trigger: str,
        state: RuntimeState,
    ) -> None:
        """Wait for any running consolidation, then process latest messages.

        Used for session close / switch where we must finish before leaving.
        """
        self._cancel_idle_timer()
        while self._scheduled_tasks:
            await asyncio.gather(*tuple(self._scheduled_tasks), return_exceptions=True)
        async with self._lock:
            if self._pending_snapshot is not None:
                messages, message_ids = self._pending_snapshot
                self._pending_snapshot = None
                await self._run_one_consolidation(
                    trigger=trigger,
                    state=state,
                    messages=messages,
                    message_ids=message_ids,
                )
            messages, message_ids = self._snapshot(state)
            await self._run_one_consolidation(
                trigger=trigger,
                state=state,
                messages=messages,
                message_ids=message_ids,
            )

    def _snapshot(
        self, state: RuntimeState
    ) -> tuple[tuple[dict[str, Any], ...], tuple[str, ...]]:
        if self._message_store is not None:
            return (
                self._message_store.current_messages(),
                self._message_store.current_message_ids(),
            )
        return (), ()

    async def _run_one_consolidation(
        self,
        *,
        trigger: str,
        state: RuntimeState,
        messages: tuple[dict[str, Any], ...],
        message_ids: tuple[str, ...],
    ) -> None:
        """Execute a single consolidation job (caller holds the lock)."""
        start_time = time.monotonic()
        watermark_before = self._load_watermark(state)

        new_messages, new_ids, context_prefix = self._select_range(
            messages, message_ids, watermark_before
        )

        skip_reason = self._check_skip(trigger, state, new_messages)
        if skip_reason is not None:
            self._trace_recorder.event(
                "ltm_consolidation_skipped",
                {
                    "trigger": trigger,
                    "reason": skip_reason,
                    "watermark_before": watermark_before,
                    "message_count": len(new_messages),
                },
            )
            return

        self._trace_recorder.event(
            "ltm_consolidation_started",
            {
                "trigger": trigger,
                "watermark_before": watermark_before,
                "start_message_id": new_ids[0] if new_ids else None,
                "end_message_id": new_ids[-1] if new_ids else None,
                "message_count": len(new_messages),
                "context_prefix_count": len(context_prefix),
            },
        )

        # Send context prefix + new messages to the extraction child.
        child_messages = context_prefix + new_messages
        job = self.prepare_extraction_job(
            child_messages,
            state,
            tool_calls=(),
            _check_lock=False,
        )
        if job is None:
            # prepare_extraction_job recorded the skip decision. For
            # "success-like" skips (main_agent_memory_write, cursor_current),
            # the messages are effectively processed — advance the watermark
            # so they are not re-processed on the next consolidation.
            last_decision = _metadata(state).get("last_decision")
            if last_decision in ("main_agent_memory_write", "cursor_current"):
                watermark_after = new_ids[-1] if new_ids else watermark_before
                self._save_watermark(state, watermark_after)
                self._trace_recorder.event(
                    "ltm_consolidation_completed",
                    {
                        "trigger": trigger,
                        "watermark_before": watermark_before,
                        "watermark_after": watermark_after,
                        "message_count": len(new_messages),
                        "duration": round(time.monotonic() - start_time, 3),
                        "skip_decision": last_decision,
                    },
                )
            return

        try:
            await self._execute_extraction(job, state)
        except Exception:
            # _execute_extraction already recorded the failure trace.
            # Watermark must NOT advance on failure.
            duration = time.monotonic() - start_time
            self._trace_recorder.event(
                "ltm_consolidation_failed",
                {
                    "trigger": trigger,
                    "watermark_before": watermark_before,
                    "watermark_after": watermark_before,
                    "message_count": len(new_messages),
                    "duration": round(duration, 3),
                },
            )
            return

        # Success (including "nothing worth saving") -> advance watermark.
        watermark_after = new_ids[-1] if new_ids else watermark_before
        self._save_watermark(state, watermark_after)

        duration = time.monotonic() - start_time
        child_session_id = _metadata(state).get("last_result_session_id")
        self._trace_recorder.event(
            "ltm_consolidation_completed",
            {
                "trigger": trigger,
                "watermark_before": watermark_before,
                "watermark_after": watermark_after,
                "start_message_id": new_ids[0] if new_ids else None,
                "end_message_id": new_ids[-1] if new_ids else None,
                "message_count": len(new_messages),
                "duration": round(duration, 3),
                "child_session_id": child_session_id,
            },
        )

    def _check_skip(
        self,
        trigger: str,
        state: RuntimeState,
        new_messages: tuple[dict[str, Any], ...],
    ) -> str | None:
        _ = trigger
        if not self._policy.enabled:
            return "disabled"
        if state.metadata.get("long_term_memory_extraction_agent") is True:
            return "extraction_child"
        if state.metadata.get("is_fork_child") is True:
            return "fork_child"
        if not new_messages:
            return "no_new_messages"
        return None

    def _select_range(
        self,
        messages: tuple[dict[str, Any], ...],
        message_ids: tuple[str, ...],
        watermark: str | None,
    ) -> tuple[
        tuple[dict[str, Any], ...],
        tuple[str, ...],
        tuple[dict[str, Any], ...],
    ]:
        """Return ``(new_messages, new_ids, context_prefix)`` after watermark.

        If the watermark UUID is not found (e.g. compaction replaced the
        active chain), synthetic compact boundary/summary messages are skipped
        and consolidation starts from the first real message.
        """
        if not watermark:
            return messages, message_ids, ()

        try:
            idx = message_ids.index(watermark)
            start = idx + 1
        except ValueError:
            start = 0
            for i, msg in enumerate(messages):
                meta = msg.get("metadata") or {}
                if meta.get("is_compact_boundary") or meta.get("is_compact_summary"):
                    start = i + 1
                else:
                    break

        prefix_start = max(0, start - self._policy.context_prefix_messages)
        context_prefix = messages[prefix_start:start]
        return messages[start:], message_ids[start:], context_prefix

    # ------------------------------------------------------------------
    # Watermark persistence
    # ------------------------------------------------------------------

    def _load_watermark(self, state: RuntimeState) -> str | None:
        """Load watermark from in-memory cache, then file, then legacy cursor."""
        meta = _metadata(state)
        cached = meta.get("last_ltm_consolidated_message_id")
        if isinstance(cached, str) and cached:
            return cached

        path = self._watermark_path(state)
        if path is not None and path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                wm = (
                    data.get("last_ltm_consolidated_message_id")
                    if isinstance(data, dict)
                    else None
                )
                if isinstance(wm, str) and wm:
                    _merge_metadata(state, {"last_ltm_consolidated_message_id": wm})
                    return wm
            except (OSError, ValueError):
                pass

        cursor = meta.get("cursor")
        if isinstance(cursor, str) and cursor:
            return cursor

        return None

    def _save_watermark(self, state: RuntimeState, message_id: str) -> None:
        """Persist watermark to file and in-memory cache (and legacy cursor)."""
        _merge_metadata(
            state,
            {
                "last_ltm_consolidated_message_id": message_id,
                "cursor": message_id,
            },
        )
        path = self._watermark_path(state)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "last_ltm_consolidated_message_id": message_id,
                        "updated_at": _now(),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError:
            pass

    def _watermark_path(self, state: RuntimeState) -> Path | None:
        _ = state
        if self._message_store is not None:
            session_dir = getattr(self._message_store.transcript_store, "session_dir", None)
            if session_dir is not None:
                return Path(session_dir) / WATERMARK_FILENAME
        return None


# ======================================================================
# Module-level helpers (kept for backward compatibility)
# ======================================================================


def should_extract_long_term_memory(
    messages: tuple[dict[str, Any], ...],
    state: RuntimeState,
    *,
    tool_calls: tuple[Any, ...],
    enabled: bool = True,
) -> str:
    if not enabled:
        return "disabled"
    if tool_calls:
        return "tool_use_continuation"
    if state.metadata.get("query_source") == "compact":
        return "compact"
    if state.metadata.get("is_fork_child") is True:
        return "fork_child"
    if state.metadata.get("long_term_memory_extraction_agent") is True:
        return "extraction_child"
    if _main_agent_wrote_memory_this_turn(state):
        return "main_agent_memory_write"
    if _latest_cursor(messages) == _metadata(state).get("cursor"):
        return "cursor_current"
    return "extract"


def _extraction_prompt(
    store: LongTermMemoryStore,
    messages: tuple[dict[str, Any], ...],
    state: RuntimeState,
) -> str:
    cursor = _metadata(state).get("cursor") or "(none)"
    catalog = "\n".join(
        f"- {item.relative_path}: {item.description} ({item.type})"
        for item in store.scan()[:200]
    )
    conversation = _format_messages_for_prompt(messages)
    return "\n".join(
        [
            "Update workspace-local Nervure long-term memory if the new conversation contains durable future-useful facts.",
            "",
            f"Memory directory: {store.memory_dir.resolve()}",
            f"Index file: {store.entrypoint_path.resolve()}",
            f"Last processed cursor: {cursor}",
            "",
            "Allowed memory types: user, feedback, project, reference.",
            "Rules:",
            "- Write only Markdown files under the memory directory.",
            "- Update existing topic files when possible; avoid duplicates.",
            "- Keep MEMORY.md as a concise index with one line per topic.",
            "- Do not save current task plans, short-term todos, secrets, or facts easily derived from repository files.",
            "- Do not investigate source files to verify the user. Base extraction on new conversation only.",
            "- If nothing should be saved, do not edit files and finish with a short statement.",
            "",
            "Existing memory catalog:",
            catalog or "(empty)",
            "",
            "Conversation to process:",
            conversation or "(empty)",
        ]
    )


def _format_messages_for_prompt(messages: tuple[dict[str, Any], ...]) -> str:
    """Render messages as ``role: content`` lines for the extraction prompt."""
    lines: list[str] = []
    for message in messages:
        role = str(message.get("role", "unknown"))
        content = message.get("content")
        if isinstance(content, list):
            rendered = " ".join(
                str(block.get("text", "")) if isinstance(block, dict) else str(block)
                for block in content
            )
        else:
            rendered = str(content or "")
        rendered = rendered.replace("\r\n", "\n").strip()
        if len(rendered) > 2000:
            rendered = rendered[:2000] + "...[truncated]"
        lines.append(f"[{role}]: {rendered}")
    return "\n".join(lines)



def _main_agent_wrote_memory_this_turn(state: RuntimeState) -> bool:
    writes = state.metadata.get("long_term_memory_writes", ())
    try:
        return any(
            isinstance(item, dict) and item.get("turn_count") == state.turn_count
            for item in writes
        )
    except TypeError:
        return False


def _latest_cursor(messages: tuple[dict[str, Any], ...]) -> str:
    if not messages:
        return ""
    message = messages[-1]
    metadata = message.get("metadata")
    if isinstance(metadata, dict):
        for key in ("message_uuid", "uuid"):
            value = metadata.get(key)
            if isinstance(value, str) and value:
                return value
    return f"message-{len(messages)}"


def _advance_cursor(messages: tuple[dict[str, Any], ...], state: RuntimeState) -> None:
    _merge_metadata(state, {"cursor": _latest_cursor(messages)})


def _metadata(state: RuntimeState) -> dict[str, Any]:
    value = state.metadata.get(LONG_TERM_MEMORY_EXTRACTION_KEY)
    if isinstance(value, dict):
        return value
    value = {}
    state.metadata[LONG_TERM_MEMORY_EXTRACTION_KEY] = value
    return value


def _merge_metadata(state: RuntimeState, updates: dict[str, Any]) -> None:
    value = dict(_metadata(state))
    value.update(updates)
    state.metadata[LONG_TERM_MEMORY_EXTRACTION_KEY] = value


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
