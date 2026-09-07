"""Tests for token-aware, context-aware dynamic Tool Result externalization."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from core.runtime_state import RuntimeState
from services.compaction import ContextCompactionService
from services.compaction.token_estimator import (
    estimate_message_tokens,
    estimate_messages_tokens,
)
from services.compaction.types import CompactionConfig
from services.context.current_model_context import CurrentModelContext
from services.context.snapshot import ContextSnapshot
from services.observability import TraceRecorder
from services.observability.events import TraceRecord
from utils.toolResultStorage import ToolResultStorage


@dataclass
class RecordingSink:
    records: list[TraceRecord] = field(default_factory=list)

    def emit(self, record: TraceRecord) -> None:
        self.records.append(record)

    def flush(self) -> None:
        return None


def _prepare(service: ContextCompactionService, messages: tuple[dict, ...]):
    return asyncio.run(service.prepare_for_model(messages, RuntimeState()))


def _result(call_id: str, token_target: int, *, tool_name: str = "grep") -> dict:
    # _estimate_text_tokens is ceil(chars / 3); small per-message overhead is
    # negligible compared with the targets used below.
    return {
        "role": "tool_result",
        "tool_call_id": call_id,
        "tool_name": tool_name,
        "content": "x" * (token_target * 3),
    }


def _assistant_call(call_id: str, name: str = "grep") -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": call_id, "name": name}],
    }


def _result_budget_events(records: list[TraceRecord]) -> list[dict[str, Any]]:
    events = [
        record.attributes
        for record in records
        if record.name == "compact_result_budget"
    ]
    assert events, "compact_result_budget trace event missing"
    return events


# --- A: empty context + 5K token result stays inline -----------------------


def test_empty_context_5k_token_result_stays_inline() -> None:
    service = ContextCompactionService(config=CompactionConfig())
    messages = (_result("call-1", 5_000),)
    original_content = messages[0]["content"]

    result = _prepare(service, messages)

    projected = result.messages[0]
    assert projected["content"] == original_content
    assert projected.get("metadata") is None
    assert estimate_message_tokens(projected) < 16_000
    # Projection-only: the input chain is untouched.
    assert messages[0]["content"] == original_content


# --- B: empty context + 30K token result externalized by hard cap ----------


def test_empty_context_30k_token_result_externalized_by_hard_cap(tmp_path) -> None:
    sink = RecordingSink()
    recorder = TraceRecorder(session_id="session-b", sink=sink)
    store = ToolResultStorage(tmp_path / "session-b")
    service = ContextCompactionService(
        config=CompactionConfig(),
        result_store=store,
        trace_recorder=recorder,
    )
    full_content = "x" * (30_000 * 3)
    messages = (_result("call-big", 30_000, tool_name="pytest"),)

    result = _prepare(service, messages)

    projected = result.messages[0]
    metadata = projected["metadata"]
    assert metadata["result_stored"] is True
    assert metadata["externalized_reason"] == "token_budget"
    assert metadata["result_estimated_tokens"] > 16_000
    assert metadata["dynamic_budget_tokens"] == 16_000
    assert metadata["remaining_tokens"] == 102_400
    assert metadata["context_tokens_without_result"] < 50
    assert "[Tool result stored]" in projected["content"]
    assert "Preview:\n" in projected["content"]
    # Full result is durably recoverable; the raw message was not rewritten.
    assert (
        store.read_result(metadata["stored_result_relative_path"]) == full_content
    )
    assert messages[0]["content"] == full_content

    event = _result_budget_events(sink.records)[-1]
    decision = event["results"][0]
    assert decision["externalized"] is True
    assert decision["result_estimated_tokens"] == metadata["result_estimated_tokens"]
    assert decision["dynamic_budget_tokens"] == 16_000
    assert decision["hard_cap_tokens"] == 16_000
    assert decision["remaining_tokens"] == 102_400
    assert decision["context_tokens_without_result"] < 50
    # Trace must never carry result content.
    assert "content" not in decision
    assert full_content not in repr(decision)


# --- C: context near the threshold externalizes an 8K result ---------------


def test_near_threshold_context_externalizes_moderate_result(tmp_path) -> None:
    store = ToolResultStorage(tmp_path / "session-c")
    service = ContextCompactionService(
        config=CompactionConfig(),
        result_store=store,
    )
    # ~96K tokens of ordinary history, then an 8K token tool result.
    history = (
        {"role": "user", "content": "y" * (96_000 * 3)},
        _assistant_call("call-c"),
        _result("call-c", 8_000),
    )

    result = _prepare(service, history)

    metadata = result.messages[-1]["metadata"]
    assert metadata["result_stored"] is True
    assert metadata["result_estimated_tokens"] > 8_000
    assert metadata["dynamic_budget_tokens"] < 2_000
    assert metadata["remaining_tokens"] < 8_000
    assert metadata["context_tokens_without_result"] > 90_000


# --- D: budget scales with context_window_tokens ---------------------------


def test_dynamic_budget_scales_with_context_window() -> None:
    moderate = (_result("call-d", 14_000),)

    small_window = ContextCompactionService(
        config=CompactionConfig(context_window_tokens=64_000)
    )
    large_window = ContextCompactionService(
        config=CompactionConfig(context_window_tokens=128_000)
    )

    small_result = _prepare(small_window, moderate)
    large_result = _prepare(large_window, moderate)

    # 64K window -> threshold 51.2K -> budget 12.8K -> 14K result externalized
    # (preview only without a store).
    assert len(small_result.messages[0]["content"]) == 4_000
    # 128K window -> budget 16K -> 14K result stays inline.
    assert len(large_result.messages[0]["content"]) == 14_000 * 3


def test_dynamic_budget_formula_matches_occupancy() -> None:
    config = CompactionConfig()
    assert config.auto_compact_threshold_tokens == 102_400
    assert config.tool_result_dynamic_budget(0) == (102_400, 16_000)
    # At/over the threshold the local budget is fully closed; global full
    # compaction owns that layer.
    assert config.tool_result_dynamic_budget(102_400) == (0, 0)
    assert config.tool_result_dynamic_budget(120_000) == (0, 0)
    # 128K context, occupancy 20K / 80K / 98K worked example.
    assert config.tool_result_dynamic_budget(20_000) == (82_400, 16_000)
    assert config.tool_result_dynamic_budget(80_000) == (22_400, 5_600)
    assert config.tool_result_dynamic_budget(98_000) == (4_400, 1_100)


# --- E: several large results cannot each claim full remaining headroom ----


def test_consecutive_large_results_use_shrinking_sequential_budget() -> None:
    # High hard cap so the ratio/remaining mechanism (not the cap) decides.
    service = ContextCompactionService(
        config=CompactionConfig(
            tool_result_hard_cap_tokens=100_000,
            tool_result_remaining_ratio=0.25,
        )
    )
    messages = (
        _assistant_call("call-1"),
        _result("call-1", 25_000),
        _assistant_call("call-2"),
        _result("call-2", 25_000),
        _assistant_call("call-3"),
        _result("call-3", 25_000),
    )

    result = _prepare(service, messages)
    results = [m for m in result.messages if m.get("role") == "tool_result"]

    # First result fits its share of the full remaining headroom and inlines.
    assert len(results[0]["content"]) == 25_000 * 3
    # The second and third must not re-claim 25% of the ORIGINAL remaining:
    # their baseline already contains the first result's 25K tokens.
    assert len(results[1]["content"]) == 4_000
    assert len(results[2]["content"]) == 4_000
    total = estimate_messages_tokens(tuple(result.messages))
    assert total < 30_000


def test_consecutive_results_over_hard_cap_all_externalized_compactly(
    tmp_path,
) -> None:
    sink = RecordingSink()
    store = ToolResultStorage(tmp_path / "session-e")
    service = ContextCompactionService(
        config=CompactionConfig(),
        result_store=store,
        trace_recorder=TraceRecorder(session_id="session-e", sink=sink),
    )
    messages = (
        _assistant_call("call-1"),
        _result("call-1", 30_000),
        _assistant_call("call-2"),
        _result("call-2", 30_000),
    )

    result = _prepare(service, messages)
    projected_results = [
        m for m in result.messages if m.get("role") == "tool_result"
    ]

    assert all(m["metadata"]["result_stored"] is True for m in projected_results)
    # Two reference+previews must stay far below even one full 30K result.
    assert estimate_messages_tokens(tuple(result.messages)) < 5_000
    decisions = _result_budget_events(sink.records)[-1]["results"]
    assert [d["externalized"] for d in decisions] == [True, True]
    # The second decision's baseline counts the first projected reference.
    assert decisions[1]["context_tokens_without_result"] > 0
    assert decisions[0]["dynamic_budget_tokens"] == 16_000
    stored_files = sorted(path.name for path in store.results_dir.iterdir())
    assert stored_files == ["call-1.txt", "call-2.txt"]


# --- F: ref / preview / recovery compatibility -----------------------------


def test_externalized_result_roundtrip_and_repeat_prepare_stable(tmp_path) -> None:
    store = ToolResultStorage(tmp_path / "session-f")
    service = ContextCompactionService(
        config=CompactionConfig(),
        result_store=store,
    )
    messages = (_result("call-f", 30_000),)

    first = _prepare(service, messages)
    second = _prepare(service, messages)

    first_meta = first.messages[0]["metadata"]
    second_meta = second.messages[0]["metadata"]
    assert second_meta["stored_result_id"] == first_meta["stored_result_id"]
    assert second.transcript_refs == first.transcript_refs
    assert (
        store.read_result(first_meta["stored_result_relative_path"])
        == messages[0]["content"]
    )
    assert sorted(path.name for path in store.results_dir.iterdir()) == [
        "call-f.txt"
    ]
    # Raw MessageStore semantics remain projection-only.
    assert len(messages[0]["content"]) == 30_000 * 3


def test_reprojecting_stored_result_preserves_full_original_reference(tmp_path) -> None:
    store = ToolResultStorage(tmp_path / "session-reproject")
    service = ContextCompactionService(result_store=store)
    raw = _result("call-reproject", 30_000)
    first = _prepare(service, (raw,))
    original = first.messages[0]
    messages = (
        {"role": "user", "content": "y" * (102_000 * 3)},
        _assistant_call("call-reproject"),
        original,
    )

    second = _prepare(service, messages)
    projected = second.messages[-1]

    assert projected == original
    assert second.transcript_refs == first.transcript_refs
    relative_path = projected["metadata"]["stored_result_relative_path"]
    assert store.read_result(relative_path) == raw["content"]
    assert [path.name for path in store.results_dir.iterdir()] == ["call-reproject.txt"]


def test_legacy_char_budget_still_forces_externalization(tmp_path) -> None:
    store = ToolResultStorage(tmp_path / "session-legacy")
    service = ContextCompactionService(
        config=CompactionConfig(
            tool_result_budget_chars=3,
            tool_result_preview_chars=2,
        ),
        result_store=store,
    )
    messages = (
        _assistant_call("call-legacy"),
        {
            "role": "tool_result",
            "tool_call_id": "call-legacy",
            "tool_name": "grep",
            "content": "abcdef",
        },
    )

    result = _prepare(service, messages)
    metadata = result.messages[1]["metadata"]
    assert metadata["result_stored"] is True
    assert "legacy_char_budget" in metadata["externalized_reason"]
    assert "Preview:\nab" in result.messages[1]["content"]


# --- fixed prefix (system prompt / tool schemas) counts as occupancy --------


def test_previous_snapshot_fixed_prefix_shrinks_budget() -> None:
    # ~98K-token system prompt in the previous request snapshot; the 5K result
    # must be treated as if context were nearly full.
    holder = CurrentModelContext(
        ContextSnapshot(system_prompt="s" * (98_000 * 3), messages=())
    )
    service = ContextCompactionService(
        config=CompactionConfig(),
        current_model_context=holder,
    )
    messages = (_result("call-prefix", 5_000),)

    result = _prepare(service, messages)

    # No store -> externalized content collapses to the 4K-char preview.
    assert len(result.messages[0]["content"]) == 4_000


def test_previous_snapshot_messages_are_not_double_counted() -> None:
    # Snapshot messages are stale (the live chain supersedes them): a 90K-token
    # snapshot message must not inflate the baseline of the live 5K result.
    holder = CurrentModelContext(
        ContextSnapshot(
            system_prompt="",
            messages=({"role": "user", "content": "z" * (90_000 * 3)},),
        )
    )
    service = ContextCompactionService(
        config=CompactionConfig(),
        current_model_context=holder,
    )
    messages = (_result("call-ignore", 5_000),)

    result = _prepare(service, messages)

    assert len(result.messages[0]["content"]) == 5_000 * 3


# --- config validation ------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"tool_result_hard_cap_tokens": 0},
        {"tool_result_hard_cap_tokens": -1},
        {"tool_result_remaining_ratio": 0.0},
        {"tool_result_remaining_ratio": 1.2},
        {"tool_result_preview_chars": -1},
        {"tool_result_budget_chars": 0},
    ],
)
def test_tool_result_config_validation_rejects_unsafe_values(kwargs) -> None:
    with pytest.raises(ValueError):
        CompactionConfig(**kwargs)


def test_default_config_is_token_aware() -> None:
    config = CompactionConfig()
    assert config.tool_result_hard_cap_tokens == 16_000
    assert config.tool_result_remaining_ratio == 0.25
    assert config.tool_result_preview_chars == 4_000
    assert config.tool_result_budget_chars == 200_000
