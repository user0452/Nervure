from __future__ import annotations

from evals.metrics import NervureTraceAnalyzer


def _record(name: str, **attributes):
    return {
        "record_type": "event",
        "name": name,
        "attributes": attributes,
    }


def test_full_compact_metrics_use_completed_usage_not_request_estimate() -> None:
    records = [
        _record(
            "compact_prepare",
            trigger="micro",
            token_before=50,
            token_after=40,
        ),
        _record("compact_micro", microcompacted_count=2),
        _record(
            "compact_request",
            trigger="manual",
            estimated_input_tokens=9999,
        ),
        _record(
            "compact_completed",
            trigger="manual",
            input_tokens=200,
            cache_read_input_tokens=120,
            uncached_input_tokens=80,
            output_tokens=10,
            duration_ms=123.5,
        ),
        _record("compact_failed", trigger="reactive", error_type="ProviderError"),
        # A malformed/future micro request must not be counted as Full Compact.
        _record(
            "compact_request",
            trigger="micro",
            input_tokens=7000,
            output_tokens=700,
            duration_ms=999,
        ),
    ]

    compact = NervureTraceAnalyzer().analyze_records(records)["compact"]

    assert compact == {
        "full_compact_count": 1,
        "compact_completed_count": 1,
        "micro_compact_count": 1,
        "compact_trigger": ["manual", "manual"],
        "compact_requests": 1,
        "compact_input": 200,
        "compact_cache_read": 120,
        "compact_uncached": 80,
        "compact_output": 10,
        "compact_duration_ms": 123.5,
        "compact_failure": 1,
    }


def test_compact_metrics_are_zero_without_full_compact() -> None:
    compact = NervureTraceAnalyzer().analyze_records(
        [_record("compact_prepare", trigger="micro")]
    )["compact"]

    assert compact["full_compact_count"] == 0
    assert compact["compact_completed_count"] == 0
    assert compact["compact_requests"] == 0
    assert compact["compact_input"] == 0
    assert compact["compact_cache_read"] == 0
    assert compact["compact_uncached"] == 0
    assert compact["compact_output"] == 0
    assert compact["compact_duration_ms"] == 0
    assert compact["compact_failure"] == 0


def test_legacy_compact_request_without_trigger_is_counted() -> None:
    compact = NervureTraceAnalyzer().analyze_records(
        [_record("compact_request", estimated_input_tokens=123)]
    )["compact"]

    assert compact["compact_requests"] == 1


def test_subagent_usage_is_split_from_main_by_trace_lineage() -> None:
    records = [
        {
            "record_type": "span_start",
            "name": "interaction",
            "span_id": "main-interaction",
            "parent_span_id": None,
            "attributes": {},
        },
        {
            "record_type": "span_end",
            "name": "model_call",
            "span_id": "main-model",
            "parent_span_id": "main-interaction",
            "attributes": {
                "input_tokens": 100,
                "output_tokens": 10,
                "cache_read_input_tokens": 60,
                "uncached_input_tokens": 40,
                "reasoning_tokens": 0,
                "duration_ms": 10,
            },
        },
        {
            "record_type": "span_start",
            "name": "tool_execution",
            "span_id": "agent-tool",
            "parent_span_id": "main-interaction",
            "attributes": {"tool_name": "agent", "tool_call_id": "call-agent"},
        },
        {
            "record_type": "event",
            "name": "subagent_start",
            "parent_span_id": "agent-tool",
            "attributes": {
                "agent_type": "Explore",
                "child_session_id": "child-1",
                "is_fork": False,
                "read_only": True,
            },
        },
        {
            "record_type": "span_start",
            "name": "interaction",
            "span_id": "child-interaction",
            "parent_span_id": "agent-tool",
            "attributes": {},
        },
        {
            "record_type": "span_end",
            "name": "model_call",
            "span_id": "child-model",
            "parent_span_id": "child-interaction",
            "attributes": {
                "model": "test-child",
                "input_tokens": 30,
                "output_tokens": 5,
                "cache_read_input_tokens": 20,
                "uncached_input_tokens": 10,
                "reasoning_tokens": 1,
                "duration_ms": 5,
            },
        },
        {
            "record_type": "span_start",
            "name": "tool_execution",
            "span_id": "child-tool",
            "parent_span_id": "child-interaction",
            "attributes": {"tool_name": "read_file", "tool_call_id": "child-call"},
        },
        {
            "record_type": "event",
            "name": "tool_result",
            "parent_span_id": "child-tool",
            "attributes": {"tool_name": "read_file", "is_error": False},
        },
        {
            "record_type": "event",
            "name": "subagent_completed",
            "parent_span_id": "agent-tool",
            "attributes": {
                "agent_type": "Explore",
                "child_session_id": "child-1",
                "duration_ms": 25,
            },
        },
    ]

    metrics = NervureTraceAnalyzer().analyze_records(records)

    assert metrics["main_agent"]["model_calls"] == 1
    assert metrics["main_agent"]["input_tokens"] == 100
    assert metrics["child_agent"]["model_calls"] == 1
    assert metrics["child_agent"]["input_tokens"] == 30
    assert metrics["total"]["model_calls"] == 2
    assert metrics["total"]["input_tokens"] == 130
    assert metrics["subagent"]["child_count"] == 1
    assert metrics["subagent"]["completed_count"] == 1
    assert metrics["subagent"]["child_duration_ms"] == 25
    assert metrics["subagent"]["children"][0]["tool_calls"] == 1


def test_subagent_metrics_use_current_event_names() -> None:
    metrics = NervureTraceAnalyzer().analyze_records(
        [
            _record(
                "subagent_start",
                agent_type="Explore",
                child_session_id="child-1",
                is_fork=False,
                read_only=True,
            ),
            _record(
                "subagent_completed",
                agent_type="Explore",
                child_session_id="child-1",
                duration_ms=3,
            ),
        ]
    )

    assert metrics["subagent"]["child_count"] == 1
    assert metrics["subagent"]["completed_count"] == 1
    assert metrics["subagent"]["agent_type_counts"] == {"Explore": 1}
