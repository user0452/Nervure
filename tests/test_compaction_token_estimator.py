from __future__ import annotations

from services.compaction.token_estimator import (
    DOCUMENT_BLOCK_TOKENS,
    IMAGE_BLOCK_TOKENS,
    UNKNOWN_BLOCK_TOKENS,
    estimate_message_tokens,
    estimate_messages_tokens,
    estimate_snapshot_tokens,
)
from services.compaction.types import CompactionConfig
from services.context.snapshot import ContextSnapshot


def test_estimates_string_message_with_conservative_char_ratio() -> None:
    message = {"role": "user", "content": "a" * 12}

    assert estimate_message_tokens(message) == 10


def test_estimates_tool_calls_and_metadata_json_fields() -> None:
    plain = {"role": "assistant", "content": ""}
    with_tool_call = {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": "call-1", "name": "read_file"}],
        "metadata": {"stop_reason": "tool_calls"},
    }

    assert estimate_message_tokens(with_tool_call) > estimate_message_tokens(plain)


def test_estimates_multimodal_and_unknown_blocks() -> None:
    message = {
        "role": "user",
        "content": [
            {"type": "text", "text": "abcdef"},
            {"type": "image_url", "image_url": {"url": "file:///image.png"}},
            {"type": "document", "name": "spec.pdf"},
            {"type": "custom", "payload": "value"},
        ],
    }

    total = estimate_message_tokens(message)

    assert total >= IMAGE_BLOCK_TOKENS
    assert total >= DOCUMENT_BLOCK_TOKENS
    assert total >= UNKNOWN_BLOCK_TOKENS


def test_estimates_message_chain_and_snapshot() -> None:
    messages = (
        {"role": "user", "content": "hello"},
        {"role": "tool_result", "tool_call_id": "call-1", "content": "result"},
    )
    snapshot = ContextSnapshot(
        system_prompt="system",
        messages=messages,
        tool_schemas=({"type": "function", "function": {"name": "read_file"}},),
        usage_hints={"estimated": True},
        transcript_refs=("messages.jsonl",),
    )

    assert estimate_messages_tokens(messages) > 0
    assert estimate_snapshot_tokens(snapshot) > estimate_messages_tokens(messages)


def test_compaction_budgets_are_derived_from_context_window_ratios() -> None:
    config = CompactionConfig(context_window_tokens=200_000)

    assert config.compact_trigger_tokens == 160_000
    assert config.compact_summary_reserve_tokens == 20_000
    assert config.compact_safety_buffer_tokens == 10_000
    assert config.recent_tail_budget_tokens == 16_000
    assert config.auto_compact_threshold_tokens == config.compact_trigger_tokens


def test_compaction_ratio_validation_rejects_unsafe_configuration() -> None:
    for kwargs in (
        {"context_window_tokens": 0},
        {"compact_trigger_ratio": 1.0},
        {"compact_trigger_ratio": 0.9, "compact_summary_reserve_ratio": 0.1, "compact_safety_buffer_ratio": 0.1},
        {"recent_tail_min_tokens": 2, "recent_tail_max_tokens": 1},
    ):
        try:
            CompactionConfig(**kwargs)
        except ValueError:
            continue
        raise AssertionError(f"invalid compaction config accepted: {kwargs}")
