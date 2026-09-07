"""Tests for deferred / event-driven LTM consolidation (A-K scenarios)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from core.runtime_state import RuntimeState
from services.memory.auto_store import LongTermMemoryStore
from services.memory.extraction import (
    LONG_TERM_MEMORY_EXTRACTION_KEY,
    LongTermMemoryExtractionPolicy,
    LongTermMemoryExtractionService,
    TRIGGER_EXPLICIT,
    TRIGGER_FULL_COMPACT,
    TRIGGER_IDLE,
    TRIGGER_SESSION_CLOSE,
    TRIGGER_SESSION_SWITCH,
)
from services.observability import TraceRecorder
from services.subagents.types import SubagentResult


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


class FakeRunner:
    """Records subagent requests; configurable success/failure."""

    def __init__(self, *, fail: bool = False) -> None:
        self.requests: list[Any] = []
        self._fail = fail
        self._barrier: asyncio.Event | None = None

    async def run(self, request: Any) -> SubagentResult:
        self.requests.append(request)
        if self._barrier is not None:
            await self._barrier.wait()
        if self._fail:
            return SubagentResult(
                agent_type="fork",
                session_id="child-fail",
                final_text="extraction failed",
                is_error=True,
            )
        return SubagentResult(
            agent_type="fork",
            session_id="child-ok",
            final_text="ok",
        )

    def hold(self) -> asyncio.Event:
        self._barrier = asyncio.Event()
        return self._barrier


class FakeTranscriptStore:
    def __init__(self, session_dir: Path) -> None:
        self.session_dir = session_dir


class FakeMessageStore:
    """Minimal MessageStore-like object for consolidation tests."""

    def __init__(self, session_dir: Path) -> None:
        self._messages: list[dict[str, Any]] = []
        self._ids: list[str] = []
        self.transcript_store = FakeTranscriptStore(session_dir)

    def append(self, role: str, content: str, *, message_id: str | None = None) -> None:
        mid = message_id or f"msg-{len(self._ids) + 1}"
        self._messages.append(
            {"role": role, "content": content, "metadata": {"message_uuid": mid}}
        )
        self._ids.append(mid)

    def append_compact_boundary(self) -> None:
        mid = f"boundary-{len(self._ids) + 1}"
        self._messages.append(
            {
                "role": "assistant",
                "content": "[compact boundary]",
                "metadata": {"message_uuid": mid, "is_compact_boundary": True},
            }
        )
        self._ids.append(mid)

    def append_compact_summary(self) -> None:
        mid = f"summary-{len(self._ids) + 1}"
        self._messages.append(
            {
                "role": "assistant",
                "content": "[compact summary]",
                "metadata": {"message_uuid": mid, "is_compact_summary": True},
            }
        )
        self._ids.append(mid)

    def current_messages(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._messages)

    def current_message_ids(self) -> tuple[str, ...]:
        return tuple(self._ids)


class RecordingTraceRecorder(TraceRecorder):
    def __init__(self) -> None:
        super().__init__(session_id="test")
        self.events: list[tuple[str, dict[str, Any]]] = []

    def event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        self.events.append((name, dict(attributes or {})))


def _make_service(
    tmp_path: Path,
    *,
    runner: FakeRunner | None = None,
    policy: LongTermMemoryExtractionPolicy | None = None,
    message_store: FakeMessageStore | None = None,
) -> tuple[LongTermMemoryExtractionService, FakeRunner, FakeMessageStore, RecordingTraceRecorder]:
    store = LongTermMemoryStore(tmp_path / "repo")
    runner = runner or FakeRunner()
    trace = RecordingTraceRecorder()
    ms = message_store or FakeMessageStore(tmp_path / "session")
    service = LongTermMemoryExtractionService(
        store,
        subagent_runner=runner,
        policy=policy or LongTermMemoryExtractionPolicy(idle_debounce_seconds=0.02),
        trace_recorder=trace,
        message_store=ms,
    )
    return service, runner, ms, trace


def _state() -> RuntimeState:
    state = RuntimeState()
    state.turn_count = 1
    return state


def _event_names(trace: RecordingTraceRecorder) -> list[str]:
    return [name for name, _ in trace.events]


# ---------------------------------------------------------------------------
# A. Normal single turn: no immediate extraction
# ---------------------------------------------------------------------------


def test_a_normal_turn_does_not_immediately_extract(tmp_path):
    service, runner, ms, trace = _make_service(tmp_path)
    state = _state()
    ms.append("user", "hello")
    ms.append("assistant", "hi there")

    service.mark_dirty(state)

    # No extraction child should have been created.
    assert runner.requests == []


# ---------------------------------------------------------------------------
# B. Five consecutive turns: no five extraction children
# ---------------------------------------------------------------------------


def test_b_five_turns_do_not_spawn_five_children(tmp_path):
    service, runner, ms, trace = _make_service(tmp_path)
    state = _state()

    for i in range(5):
        state.turn_count = i + 1
        ms.append("user", f"question {i}")
        ms.append("assistant", f"answer {i}")
        service.mark_dirty(state)

    assert runner.requests == []


# ---------------------------------------------------------------------------
# C. Idle debounce: multiple activities reset timer; only one consolidation
# ---------------------------------------------------------------------------


def test_c_idle_debounce_resets_and_fires_once(tmp_path):
    async def _body():
        service, runner, ms, trace = _make_service(
            tmp_path,
            policy=LongTermMemoryExtractionPolicy(idle_debounce_seconds=0.05),
        )
        state = _state()

        for i in range(3):
            ms.append("user", f"q{i}")
            ms.append("assistant", f"a{i}")
            service.mark_dirty(state)
            await asyncio.sleep(0.01)

        await asyncio.sleep(0.15)

        assert len(runner.requests) == 1
        completed = [e for e in _event_names(trace) if e == "ltm_consolidation_completed"]
        assert len(completed) == 1

    _run(_body())


# ---------------------------------------------------------------------------
# D. Full compact: pre-compact messages are consolidated
# ---------------------------------------------------------------------------


def test_d_full_compact_consolidates_pre_compact_messages(tmp_path):
    async def _body():
        service, runner, ms, trace = _make_service(tmp_path)
        state = _state()

        for i in range(5):
            ms.append("user", f"pre-compact user {i}")
            ms.append("assistant", f"pre-compact assistant {i}")

        await service.consolidate(
            trigger=TRIGGER_FULL_COMPACT,
            state=state,
            messages=ms.current_messages(),
            message_ids=ms.current_message_ids(),
        )

        assert len(runner.requests) == 1
        job_prompt = runner.requests[0].prompt
        assert "pre-compact" in job_prompt

        meta = state.metadata[LONG_TERM_MEMORY_EXTRACTION_KEY]
        assert meta["last_ltm_consolidated_message_id"] == ms.current_message_ids()[-1]

    _run(_body())


def test_d_compact_child_itself_does_not_trigger_extraction(tmp_path):
    service, runner, ms, trace = _make_service(tmp_path)
    state = _state()
    state.metadata["long_term_memory_extraction_agent"] = True
    ms.append("user", "compact summary content")

    service.mark_dirty(state)
    assert runner.requests == []


# ---------------------------------------------------------------------------
# E. Watermark: first processes 1-10, then only 11-15
# ---------------------------------------------------------------------------


def test_e_watermark_advances_and_skips_processed(tmp_path):
    async def _body():
        service, runner, ms, trace = _make_service(tmp_path)
        state = _state()

        for i in range(10):
            ms.append("user", f"msg {i + 1}")
        await service.consolidate(trigger=TRIGGER_IDLE, state=state)

        first_ids = ms.current_message_ids()
        meta = state.metadata[LONG_TERM_MEMORY_EXTRACTION_KEY]
        assert meta["last_ltm_consolidated_message_id"] == first_ids[-1]
        assert len(runner.requests) == 1

        for i in range(5):
            ms.append("user", f"msg {i + 11}")
        await service.consolidate(trigger=TRIGGER_IDLE, state=state)

        assert len(runner.requests) == 2
        meta = state.metadata[LONG_TERM_MEMORY_EXTRACTION_KEY]
        assert meta["last_ltm_consolidated_message_id"] == ms.current_message_ids()[-1]

    _run(_body())


def test_e_watermark_persists_to_file(tmp_path):
    async def _body():
        service, runner, ms, trace = _make_service(tmp_path)
        state = _state()
        ms.append("user", "persist me")

        await service.consolidate(trigger=TRIGGER_IDLE, state=state)

        wm_path = ms.transcript_store.session_dir / "ltm_watermark.json"
        assert wm_path.exists()
        data = json.loads(wm_path.read_text(encoding="utf-8"))
        assert data["last_ltm_consolidated_message_id"] == ms.current_message_ids()[-1]

    _run(_body())


# ---------------------------------------------------------------------------
# F. Extraction failure: watermark does NOT advance
# ---------------------------------------------------------------------------


def test_f_failure_does_not_advance_watermark(tmp_path):
    async def _body():
        failing_runner = FakeRunner(fail=True)
        service, runner, ms, trace = _make_service(tmp_path, runner=failing_runner)
        state = _state()
        ms.append("user", "will fail")

        await service.consolidate(trigger=TRIGGER_IDLE, state=state)

        meta = state.metadata.get(LONG_TERM_MEMORY_EXTRACTION_KEY, {})
        assert meta.get("last_ltm_consolidated_message_id") is None
        assert meta.get("last_status") == "failed"
        failed_events = [e for e in _event_names(trace) if e == "ltm_consolidation_failed"]
        assert len(failed_events) == 1

    _run(_body())


# ---------------------------------------------------------------------------
# G. No-memory-worthy result: watermark still advances
# ---------------------------------------------------------------------------


def test_g_no_memory_worthy_still_advances_watermark(tmp_path):
    async def _body():
        service, runner, ms, trace = _make_service(tmp_path)
        state = _state()
        ms.append("user", "nothing durable here")

        await service.consolidate(trigger=TRIGGER_IDLE, state=state)

        meta = state.metadata[LONG_TERM_MEMORY_EXTRACTION_KEY]
        assert meta["last_ltm_consolidated_message_id"] == ms.current_message_ids()[-1]
        assert meta["last_status"] == "success"

    _run(_body())


# ---------------------------------------------------------------------------
# H. New messages during extraction: current job only advances to snapshot end
# ---------------------------------------------------------------------------


def test_h_new_messages_during_run_stay_pending(tmp_path):
    async def _body():
        service, runner, ms, trace = _make_service(tmp_path)
        state = _state()

        for i in range(3):
            ms.append("user", f"initial {i}")

        barrier = runner.hold()
        task = asyncio.create_task(service.consolidate(trigger=TRIGGER_IDLE, state=state))
        await asyncio.sleep(0.05)

        ms.append("user", "arrived during extraction")
        new_id = ms.current_message_ids()[-1]

        await service.consolidate(trigger=TRIGGER_IDLE, state=state)
        assert service.has_pending is True

        barrier.set()
        await task

        meta = state.metadata[LONG_TERM_MEMORY_EXTRACTION_KEY]
        assert meta["last_ltm_consolidated_message_id"] == new_id
        assert len(runner.requests) == 2

    _run(_body())


def test_h_running_job_only_processes_its_snapshot(tmp_path):
    async def _body():
        service, runner, ms, trace = _make_service(tmp_path)
        state = _state()

        for i in range(3):
            ms.append("user", f"snapshot {i}")
        snapshot_messages = ms.current_messages()
        snapshot_ids = ms.current_message_ids()

        barrier = runner.hold()
        task = asyncio.create_task(
            service.consolidate(
                trigger=TRIGGER_IDLE,
                state=state,
                messages=snapshot_messages,
                message_ids=snapshot_ids,
            )
        )
        await asyncio.sleep(0.05)

        ms.append("user", "not in snapshot")

        barrier.set()
        await task

        meta = state.metadata[LONG_TERM_MEMORY_EXTRACTION_KEY]
        assert meta["last_ltm_consolidated_message_id"] == snapshot_ids[-1]
        assert service.has_pending is False

    _run(_body())


# ---------------------------------------------------------------------------
# I. Session close / switch: unprocessed messages trigger consolidation
# ---------------------------------------------------------------------------


def test_i_session_close_flushes_unconsolidated(tmp_path):
    async def _body():
        service, runner, ms, trace = _make_service(tmp_path)
        state = _state()
        for i in range(4):
            ms.append("user", f"unprocessed {i}")

        service.mark_dirty(state)
        assert runner.requests == []

        await service.flush(trigger=TRIGGER_SESSION_CLOSE, state=state)

        assert len(runner.requests) == 1
        meta = state.metadata[LONG_TERM_MEMORY_EXTRACTION_KEY]
        assert meta["last_ltm_consolidated_message_id"] == ms.current_message_ids()[-1]

    _run(_body())


def test_i_session_switch_flushes(tmp_path):
    async def _body():
        service, runner, ms, trace = _make_service(tmp_path)
        state = _state()
        ms.append("user", "before switch")

        await service.flush(trigger=TRIGGER_SESSION_SWITCH, state=state)

        assert len(runner.requests) == 1
        completed = [
            e for e, a in trace.events
            if e == "ltm_consolidation_completed" and a.get("trigger") == TRIGGER_SESSION_SWITCH
        ]
        assert len(completed) == 1

    _run(_body())


# ---------------------------------------------------------------------------
# J. Fork child / extraction child: no recursive triggering
# ---------------------------------------------------------------------------


def test_j_fork_child_does_not_mark_dirty(tmp_path):
    service, runner, ms, trace = _make_service(tmp_path)
    state = _state()
    state.metadata["is_fork_child"] = True
    ms.append("user", "fork content")

    service.mark_dirty(state)
    assert runner.requests == []


def test_j_extraction_child_skipped_in_consolidate(tmp_path):
    async def _body():
        service, runner, ms, trace = _make_service(tmp_path)
        state = _state()
        state.metadata["long_term_memory_extraction_agent"] = True
        ms.append("user", "extraction child content")

        await service.consolidate(trigger=TRIGGER_IDLE, state=state)

        assert runner.requests == []
        skipped = [
            e for e, a in trace.events
            if e == "ltm_consolidation_skipped" and a.get("reason") == "extraction_child"
        ]
        assert len(skipped) == 1

    _run(_body())


# ---------------------------------------------------------------------------
# K. Existing store / selector / MEMORY.md compatibility
# ---------------------------------------------------------------------------


def test_k_memory_store_directory_and_entrypoint_unchanged(tmp_path):
    store = LongTermMemoryStore(tmp_path / "repo")
    store.ensure_exists()
    assert (store.memory_dir / "MEMORY.md").exists()
    assert store.entrypoint_path == store.memory_dir / "MEMORY.md"


def test_k_extraction_child_can_only_write_memory_dir(tmp_path):
    async def _body():
        service, runner, ms, trace = _make_service(tmp_path)
        state = _state()
        ms.append("user", "remember this fact")

        await service.consolidate(trigger=TRIGGER_IDLE, state=state)

        assert len(runner.requests) == 1
        request = runner.requests[0]
        assert request.metadata["purpose"] == "long_term_memory_extraction"
        assert "memory" in request.metadata["allowed_memory_dir"]
        assert request.metadata["max_turns"] == 5

    _run(_body())


# ---------------------------------------------------------------------------
# Additional: stale watermark after compaction skips synthetic messages
# ---------------------------------------------------------------------------


def test_stale_watermark_after_compaction_skips_synthetic(tmp_path):
    async def _body():
        service, runner, ms, trace = _make_service(tmp_path)
        state = _state()

        for i in range(3):
            ms.append("user", f"original {i}")
        await service.consolidate(trigger=TRIGGER_IDLE, state=state)

        # Simulate compaction: clear messages, add boundary + summary + new tail.
        ms._messages.clear()
        ms._ids.clear()
        ms.append_compact_boundary()
        ms.append_compact_summary()
        ms.append("user", "post-compact new message", message_id="post-compact-msg-1")

        await service.consolidate(trigger=TRIGGER_IDLE, state=state)

        assert len(runner.requests) == 2
        meta = state.metadata[LONG_TERM_MEMORY_EXTRACTION_KEY]
        assert meta["last_ltm_consolidated_message_id"] == ms.current_message_ids()[-1]

    _run(_body())


# ---------------------------------------------------------------------------
# Additional: explicit memory write triggers immediate consolidation
# ---------------------------------------------------------------------------


def test_explicit_memory_write_triggers_immediate(tmp_path):
    async def _body():
        service, runner, ms, trace = _make_service(tmp_path)
        state = _state()
        state.metadata["long_term_memory_writes"] = [{"turn_count": 1, "path": "x.md"}]
        ms.append("user", "remember this")
        ms.append("assistant", "saved to memory")

        service.mark_dirty(state)
        await asyncio.sleep(0.1)

        # Explicit memory write: no extraction child needed (memory already saved),
        # but the watermark should advance to include this turn.
        assert runner.requests == []
        meta = state.metadata[LONG_TERM_MEMORY_EXTRACTION_KEY]
        assert meta["last_ltm_consolidated_message_id"] == ms.current_message_ids()[-1]
        scheduled = [
            e for e, a in trace.events
            if e == "ltm_consolidation_scheduled" and a.get("trigger") == TRIGGER_EXPLICIT
        ]
        assert len(scheduled) == 1

    _run(_body())


# ---------------------------------------------------------------------------
# Additional: disabled policy skips everything
# ---------------------------------------------------------------------------


def test_disabled_policy_skips(tmp_path):
    async def _body():
        service, runner, ms, trace = _make_service(
            tmp_path,
            policy=LongTermMemoryExtractionPolicy(enabled=False),
        )
        state = _state()
        ms.append("user", "should be skipped")

        service.mark_dirty(state)
        await service.consolidate(trigger=TRIGGER_IDLE, state=state)

        assert runner.requests == []

    _run(_body())


# ---------------------------------------------------------------------------
# Additional: no new messages -> skipped
# ---------------------------------------------------------------------------


def test_no_new_messages_skipped(tmp_path):
    async def _body():
        service, runner, ms, trace = _make_service(tmp_path)
        state = _state()
        ms.append("user", "already processed")

        await service.consolidate(trigger=TRIGGER_IDLE, state=state)
        assert len(runner.requests) == 1

        await service.consolidate(trigger=TRIGGER_IDLE, state=state)
        assert len(runner.requests) == 1
        skipped = [
            e for e, a in trace.events
            if e == "ltm_consolidation_skipped" and a.get("reason") == "no_new_messages"
        ]
        assert len(skipped) == 1

    _run(_body())
