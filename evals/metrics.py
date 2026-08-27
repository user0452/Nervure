"""Nervure-specific deterministic metrics extracted from raw JSONL trace."""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
from typing import Any


class NervureTraceAnalyzer:
    def analyze_file(self, trace_path: Path) -> dict[str, Any]:
        records = []
        malformed = 0
        for line in trace_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if isinstance(value, dict):
                records.append(value)
        result = self.analyze_records(records)
        result["trace"] = {"records": len(records), "malformed_lines": malformed}
        return result

    def analyze_records(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        models = [r for r in records if r.get("record_type") == "span_end" and r.get("name") == "model_call"]
        tools = [r for r in records if r.get("record_type") == "event" and r.get("name") == "tool_result"]
        selectors = [r for r in records if r.get("record_type") == "event" and r.get("name") in {
            "long_term_memory_selector_started", "long_term_memory_selector_completed",
            "long_term_memory_selector_cache_hit", "long_term_memory_selector_parse_failed",
        }]
        compacts = [r for r in records if "compact" in str(r.get("name", ""))]
        extraction = [r for r in records if "long_term_memory_extraction" in str(r.get("name", ""))]
        subagents = [r for r in records if "subagent" in str(r.get("name", "")) or "fork" in str(r.get("name", ""))]
        context = [r for r in records if r.get("name") == "context_prepare" and r.get("record_type") == "span_end"]
        tool_counts = Counter(str(_attrs(r).get("tool_name") or "unknown") for r in tools)
        arguments_by_id = {
            _attrs(r).get("tool_call_id"): _attrs(r).get("tool_arguments")
            for r in records
            if r.get("name") in {"tool_execution", "tool_preflight"}
            and r.get("record_type") == "span_start"
            and _attrs(r).get("tool_call_id") is not None
        }
        repeated = Counter(
            _call_fingerprint(_attrs(r), arguments_by_id.get(_attrs(r).get("tool_call_id")))
            for r in tools
        )
        repeated = {key: count for key, count in repeated.items() if count > 1}
        # A child runtime uses the same TraceRecorder as its parent, so the
        # ``session_id`` on model spans is intentionally not a child-session
        # discriminator.  The span lineage is the stable boundary: a
        # ``subagent_start`` event is emitted inside the parent ``agent`` tool
        # span, and the child interaction/model/tool spans descend from that
        # tool span.  Split usage from that lineage before exposing
        # ``main_agent``; older metrics accidentally counted child calls as
        # main calls and looked only for the pre-V1 event names.
        child_contexts = _child_contexts(records)
        child_model_ids = {
            record.get("span_id")
            for context in child_contexts
            for record in models
            if _belongs_to_context(record, context["root_span_ids"])
        }
        child_models = [
            record for record in models if record.get("span_id") in child_model_ids
        ]
        main_models = [
            record for record in models if record.get("span_id") not in child_model_ids
        ]
        main = _aggregate_models(main_models)
        child = _aggregate_models(child_models)
        total = _aggregate_models(models)
        selector_completed = [r for r in selectors if r.get("name") == "long_term_memory_selector_completed"]
        selector_started = [r for r in selectors if r.get("name") == "long_term_memory_selector_started"]
        selector_cache = [r for r in selectors if r.get("name") == "long_term_memory_selector_cache_hit"]
        selector_failed = [r for r in selectors if r.get("name") == "long_term_memory_selector_parse_failed"]
        # ``compact_request`` is emitted before the provider call and only
        # contains the projected request estimate.  Usage and duration are
        # authoritative on the matching ``compact_completed`` event.  Keep
        # the trigger filter explicit so a future micro-compaction event
        # cannot silently enter Full Compact metrics.
        compact_requests = [
            r for r in compacts if _is_full_compact_event(r, "compact_request")
        ]
        compact_completed = [
            r for r in compacts if _is_full_compact_event(r, "compact_completed")
        ]
        compact_failed = [
            r for r in compacts if _is_full_compact_event(r, "compact_failed")
        ]
        micro_compact_events = [
            r for r in compacts if r.get("name") == "compact_micro"
        ]
        return {
            "schema_version": 1,
            "main_agent": main,
            "child_agent": child,
            "total": total,
            "tools": {
                "tool_calls": len(tools),
                "breakdown": dict(sorted(tool_counts.items())),
                "tool_errors": sum(bool(_attrs(r).get("is_error")) for r in tools),
                "unknown_tool": tool_counts.get("unknown_tool", 0),
                "permission_denied": sum("permission" in str(_attrs(r).get("error", "")) for r in tools),
                "file_not_read": sum(str(_attrs(r).get("tool_name")) == "read_file" and not _attrs(r).get("content_chars") for r in tools),
                "ask_user_question": tool_counts.get("ask_user_question", 0),
                "repeated_identical_calls": repeated,
            },
            "memory": {
                "selector_fresh_calls": len(selector_started),
                "selector_cache_hits": len(selector_cache),
                "selector_parse_failures": len(selector_failed),
                "selector_duration_ms": sum(float(_attrs(r).get("duration_ms", 0) or 0) for r in selector_completed),
                "selector_input_tokens": sum(int(_attrs(r).get("input_tokens", 0) or 0) for r in selector_completed),
                "selector_output_tokens": sum(int(_attrs(r).get("output_tokens", 0) or 0) for r in selector_completed),
                "selector_reasoning_tokens": sum(int(_attrs(r).get("reasoning_tokens", 0) or 0) for r in selector_completed),
                "selector_selected_paths": sorted({path for r in selector_completed for path in (_attrs(r).get("selected_paths") or ())}),
            },
            "ltm_extraction": {
                "jobs": sum(r.get("name") == "long_term_memory_extraction_decision" for r in extraction),
                "events": len(extraction),
                "model_calls": sum(_attrs(r).get("purpose") == "long_term_memory_extraction" for r in models),
            },
            "session_memory": {"event_count": sum("session_memory" in str(r.get("name", "")) for r in records), "regression": any("session_memory" in str(r.get("name", "")) for r in records)},
            "compact": {
                "full_compact_count": len(compact_completed),
                "compact_completed_count": len(compact_completed),
                "micro_compact_count": len(micro_compact_events),
                "compact_trigger": [
                    _attrs(r).get("trigger")
                    for r in (*compact_requests, *compact_completed)
                    if _attrs(r).get("trigger") is not None
                ],
                "compact_requests": len(compact_requests),
                "compact_input": sum(
                    int(_attrs(r).get("input_tokens", 0) or 0)
                    for r in compact_completed
                ),
                "compact_cache_read": sum(
                    int(_attrs(r).get("cache_read_input_tokens", 0) or 0)
                    for r in compact_completed
                ),
                "compact_uncached": sum(
                    int(_attrs(r).get("uncached_input_tokens", 0) or 0)
                    for r in compact_completed
                ),
                "compact_output": sum(
                    int(_attrs(r).get("output_tokens", 0) or 0)
                    for r in compact_completed
                ),
                "compact_duration_ms": sum(
                    float(_attrs(r).get("duration_ms", 0) or 0)
                    for r in compact_completed
                ),
                "compact_failure": len(compact_failed),
            },
            "subagent": _subagent_metrics(records, subagents, child_contexts, child),
            "context": {
                "context_prepare_count": len(context),
                "context_prepare_total_ms": sum(float(_attrs(r).get("duration_ms", 0) or 0) for r in context),
                "context_prepare_avg_ms": _average(context),
                "context_prepare_max_ms": max((float(_attrs(r).get("duration_ms", 0) or 0) for r in context), default=0),
                "selected_memories": sorted({path for r in context for path in (_attrs(r).get("selected_memory_paths") or ())}),
                "compact_boundary": _attrs(context[-1]).get("compact_boundary_id") if context else None,
            },
            "hitl": {
                "ask_user_question_count": tool_counts.get("ask_user_question", 0),
                "hitl_tool_results_without_new_user_turn": _hitl_without_user_turn(records),
                "hitl_possible_auto_continue": False,
            },
        }


def _child_contexts(
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return one span-lineage context per real child runtime.

    ``subagent_start`` is an event rather than a span.  Its parent is the
    parent ``tool_execution`` span; the child's first ``interaction`` span is
    therefore the first descendant with that parent.  Keeping the lineage
    calculation in the metrics layer lets old traces remain readable without
    changing the live AgentLoop or TraceRecorder contract.
    """

    parent_by_span = {
        str(record.get("span_id")): record.get("parent_span_id")
        for record in records
        if record.get("span_id")
    }
    interaction_starts = [
        record
        for record in records
        if record.get("record_type") == "span_start"
        and record.get("name") == "interaction"
        and record.get("span_id")
    ]
    starts = [
        record
        for record in records
        if record.get("record_type") == "event"
        and record.get("name") in {"subagent_start", "subagent_started"}
    ]
    contexts: list[dict[str, Any]] = []
    used_roots: set[str] = set()
    for start in starts:
        parent_span_id = start.get("parent_span_id")
        candidates = [
            record
            for record in interaction_starts
            if record.get("parent_span_id") == parent_span_id
            and str(record.get("span_id")) not in used_roots
        ]
        # A synthetic trace may omit the child interaction span.  Preserve the
        # event-level child count while leaving usage unclassified rather than
        # guessing which parent model call it belongs to.
        root_span_id = str(candidates[0].get("span_id")) if candidates else None
        if root_span_id:
            used_roots.add(root_span_id)
        contexts.append(
            {
                "start": start,
                "root_span_ids": _descendant_span_ids(
                    root_span_id,
                    parent_by_span,
                )
                if root_span_id
                else set(),
            }
        )
    return contexts


def _descendant_span_ids(
    root_span_id: str | None,
    parent_by_span: dict[str, Any],
) -> set[str]:
    if not root_span_id:
        return set()
    return {
        span_id
        for span_id in parent_by_span
        if _is_descendant(span_id, root_span_id, parent_by_span)
    }


def _is_descendant(
    span_id: str,
    root_span_id: str,
    parent_by_span: dict[str, Any],
) -> bool:
    current: Any = span_id
    seen: set[str] = set()
    while current and str(current) not in seen:
        current = str(current)
        if current == root_span_id:
            return True
        seen.add(current)
        current = parent_by_span.get(current)
    return False


def _belongs_to_context(
    record: dict[str, Any],
    root_span_ids: set[str],
) -> bool:
    if not root_span_ids:
        return False
    span_id = record.get("span_id")
    parent_span_id = record.get("parent_span_id")
    return str(span_id) in root_span_ids or str(parent_span_id) in root_span_ids


def _subagent_metrics(
    records: list[dict[str, Any]],
    subagent_records: list[dict[str, Any]],
    contexts: list[dict[str, Any]],
    child_usage: dict[str, Any],
) -> dict[str, Any]:
    starts = [
        record
        for record in subagent_records
        if record.get("name") in {"subagent_start", "subagent_started"}
    ]
    completed = [
        record
        for record in subagent_records
        if record.get("name") == "subagent_completed"
    ]
    errors = [
        record
        for record in subagent_records
        if record.get("name") == "subagent_error"
    ]
    completed_by_session = {
        str(_attrs(record).get("child_session_id")): record
        for record in completed
        if _attrs(record).get("child_session_id")
    }
    errors_by_session = {
        str(_attrs(record).get("child_session_id")): record
        for record in errors
        if _attrs(record).get("child_session_id")
    }
    children: list[dict[str, Any]] = []
    for context in contexts:
        start = context["start"]
        start_attrs = _attrs(start)
        session_id = str(start_attrs.get("child_session_id") or "")
        done = completed_by_session.get(session_id)
        error = errors_by_session.get(session_id)
        child_models = [
            record
            for record in records
            if record.get("record_type") == "span_end"
            and record.get("name") == "model_call"
            and _belongs_to_context(record, context["root_span_ids"])
        ]
        child_tools = [
            record
            for record in records
            if record.get("record_type") == "event"
            and record.get("name") == "tool_result"
            and _belongs_to_context(record, context["root_span_ids"])
        ]
        usage = _aggregate_models(child_models)
        children.append(
            {
                "child_session_id": session_id or None,
                "agent_type": start_attrs.get("agent_type"),
                "purpose": start_attrs.get("purpose"),
                "is_fork": bool(start_attrs.get("is_fork")),
                "read_only": bool(start_attrs.get("read_only")),
                "completed": done is not None,
                "error": error is not None,
                "duration_ms": float(_attrs(done).get("duration_ms", 0) or 0)
                if done
                else 0.0,
                "model": next(
                    (str(_attrs(record).get("model")) for record in child_models if _attrs(record).get("model")),
                    None,
                ),
                "model_calls": usage["model_calls"],
                "input_tokens": usage["input_tokens"],
                "cache_read_input_tokens": usage["cache_read_input_tokens"],
                "uncached_input_tokens": usage["uncached_input_tokens"],
                "output_tokens": usage["output_tokens"],
                "reasoning_tokens": usage["reasoning_tokens"],
                "tool_calls": len(child_tools),
                "tool_errors": sum(bool(_attrs(record).get("is_error")) for record in child_tools),
            }
        )
    agent_types = Counter(
        str(_attrs(record).get("agent_type") or "unknown") for record in starts
    )
    return {
        # ``child_count`` is the real start count, not the number of legacy
        # ``fork_started`` placeholders or completed children only.
        "child_count": len(starts),
        "completed_count": len(completed),
        "error_count": len(errors),
        "events": len(subagent_records),
        "agent_type_counts": dict(sorted(agent_types.items())),
        "fork_count": sum(bool(_attrs(record).get("is_fork")) for record in starts),
        "non_fork_count": sum(not bool(_attrs(record).get("is_fork")) for record in starts),
        "read_only_count": sum(bool(_attrs(record).get("read_only")) for record in starts),
        "child_duration_ms": sum(
            float(_attrs(record).get("duration_ms", 0) or 0) for record in completed
        ),
        "model_calls": child_usage["model_calls"],
        "input_tokens": child_usage["input_tokens"],
        "cache_read_input_tokens": child_usage["cache_read_input_tokens"],
        "uncached_input_tokens": child_usage["uncached_input_tokens"],
        "output_tokens": child_usage["output_tokens"],
        "reasoning_tokens": child_usage["reasoning_tokens"],
        "tool_calls": sum(child["tool_calls"] for child in children),
        "tool_errors": sum(child["tool_errors"] for child in children),
        "unknown_tool": sum(
            _attrs(record).get("tool_name") == "unknown_tool"
            for record in records
            if record.get("record_type") == "event"
            and record.get("name") == "tool_result"
            and any(
                _belongs_to_context(record, context["root_span_ids"])
                for context in contexts
            )
        ),
        "errors": len(errors),
        "children": children,
    }


def _attrs(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("attributes")
    return value if isinstance(value, dict) else {}


_FULL_COMPACT_TRIGGERS = {"auto_full", "manual", "reactive"}


def _is_full_compact_event(record: dict[str, Any], name: str) -> bool:
    if record.get("name") != name:
        return False
    trigger = _attrs(record).get("trigger")
    # Older traces were emitted before compact_request carried its trigger.
    # Requests have never been emitted by the micro-compaction path, so this
    # narrow compatibility rule preserves their count without weakening the
    # completed/failure event classification.
    if name == "compact_request" and trigger is None:
        return True
    return trigger in _FULL_COMPACT_TRIGGERS


def _aggregate_models(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "model_calls": len(records),
        "input_tokens": sum(int(_attrs(r).get("input_tokens", 0) or 0) for r in records),
        "cache_read_input_tokens": sum(int(_attrs(r).get("cache_read_input_tokens", 0) or 0) for r in records),
        "uncached_input_tokens": sum(int(_attrs(r).get("uncached_input_tokens", 0) or 0) for r in records),
        "output_tokens": sum(int(_attrs(r).get("output_tokens", 0) or 0) for r in records),
        "reasoning_tokens": sum(int(_attrs(r).get("reasoning_tokens", 0) or 0) for r in records),
        "visible_output_tokens": sum(int(_attrs(r).get("visible_output_tokens", 0) or 0) for r in records),
        "duration_ms": sum(float(_attrs(r).get("duration_ms", 0) or 0) for r in records),
    }


def _call_fingerprint(attrs: dict[str, Any], arguments: Any = None) -> str:
    value = {"tool_name": attrs.get("tool_name"), "arguments": arguments}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]


def _average(records: list[dict[str, Any]]) -> float:
    if not records:
        return 0.0
    return round(sum(float(_attrs(r).get("duration_ms", 0) or 0) for r in records) / len(records), 3)


def _hitl_without_user_turn(records: list[dict[str, Any]]) -> int:
    interactions = {r.get("span_id") for r in records if r.get("name") == "interaction"}
    count = 0
    for index, record in enumerate(records):
        attrs = _attrs(record)
        if record.get("name") != "tool_result" or attrs.get("tool_name") != "ask_user_question":
            continue
        following = records[index + 1 :]
        if not any(item.get("name") == "interaction" for item in following):
            count += 1
    _ = interactions
    return count
