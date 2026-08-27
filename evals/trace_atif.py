"""Convert Nervure JSONL debug traces to Harbor ATIF without changing schema."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from services.observability.sanitize import sanitize_attributes


@dataclass(frozen=True)
class ATIFConversion:
    trajectory: dict[str, Any]
    atif_valid: bool | None
    errors: tuple[str, ...]
    source_errors: tuple[str, ...] = ()


class NervureTraceToATIFConverter:
    """Best-effort converter; malformed lines never erase the raw artifact."""

    def __init__(self, *, agent_version: str = "working-tree") -> None:
        self.agent_version = agent_version

    def convert_file(self, trace_path: Path) -> ATIFConversion:
        records, source_errors = _read_jsonl(trace_path)
        trajectory = self.convert_records(records)
        valid, errors = _validate_with_harbor(trajectory)
        return ATIFConversion(
            trajectory=trajectory,
            atif_valid=valid,
            errors=tuple(errors),
            source_errors=tuple(source_errors),
        )

    def convert_records(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        first = records[0] if records else {}
        session_id = str(first.get("session_id") or "nervure-unknown")
        trace_id = first.get("trace_id")
        model_call_ends = [
            record for record in records
            if record.get("record_type") == "span_end" and record.get("name") == "model_call"
        ]
        first_attrs = _attrs(model_call_ends[0] if model_call_ends else {})
        trace_tool_calls = _tool_calls_by_id(records)
        steps: list[dict[str, Any]] = []
        step_id = 1
        model_indexes = {id(record): index for index, record in enumerate(model_call_ends)}
        for record in records:
            if record.get("record_type") == "span_start" and record.get("name") == "interaction":
                attrs = _attrs(record)
                prompt = attrs.get("user_prompt")
                if not isinstance(prompt, str):
                    prompt = "[user prompt unavailable: trace level did not retain正文]"
                steps.append({
                    "step_id": step_id,
                    "timestamp": record.get("timestamp"),
                    "source": "user",
                    "message": _safe_text(prompt),
                    "extra": {"user_turn_id": attrs.get("user_turn_id"), "user_prompt_available": "user_prompt" in attrs},
                })
                step_id += 1
                continue
            if id(record) not in model_indexes:
                continue
            index = model_indexes[id(record)]
            attrs = _attrs(record)
            calls = attrs.get("tool_calls") or []
            tool_calls = []
            for call in calls:
                if not isinstance(call, dict):
                    continue
                tool_calls.append({
                    "tool_call_id": str(call.get("tool_call_id") or "unknown"),
                    "function_name": str(call.get("tool_name") or "unknown"),
                    "arguments": _safe_mapping(call.get("arguments") or {}),
                })
            observations = _observations_after(records, record, model_call_ends, index)
            known_call_ids = {call["tool_call_id"] for call in tool_calls}
            # Normal traces deliberately omit tool arguments and model output
            # bodies.  They still retain tool_preflight spans, so reconstruct
            # the call identity from that metadata before linking results.
            # ATIF requires every observation source_call_id to reference a
            # tool call in the same agent step.
            for observation in observations:
                source_call_id = observation.get("source_call_id")
                if not source_call_id or source_call_id in known_call_ids:
                    continue
                fallback = trace_tool_calls.get(str(source_call_id))
                tool_calls.append(
                    fallback
                    or {
                        "tool_call_id": str(source_call_id),
                        "function_name": "unknown",
                        "arguments": {},
                    }
                )
                known_call_ids.add(str(source_call_id))
            metrics = _metrics(attrs)
            step: dict[str, Any] = {
                "step_id": step_id,
                "timestamp": record.get("timestamp"),
                "source": "agent",
                "model_name": attrs.get("model"),
                "message": _safe_text(attrs.get("assistant_visible_text") or ""),
                "tool_calls": tool_calls or None,
                "observation": {"results": observations} if observations else None,
                "metrics": metrics or None,
                "llm_call_count": 1,
                "extra": {
                    "provider_id": attrs.get("provider_id"),
                    "stop_reason": attrs.get("stop_reason"),
                    "duration_ms": attrs.get("duration_ms"),
                },
            }
            reasoning = attrs.get("provider_reasoning_text")
            if isinstance(reasoning, str) and reasoning:
                step["reasoning_content"] = _safe_text(reasoning)
            steps.append({key: value for key, value in step.items() if value is not None})
            step_id += 1

        if not steps:
            steps.append({
                "step_id": 1,
                "source": "system",
                "message": "No model interaction was retained in the Nervure trace.",
                "extra": {"trace_id": trace_id},
            })
        totals = _totals(model_call_ends)
        return {
            "schema_version": "ATIF-v1.7",
            "session_id": session_id,
            "trajectory_id": str(trace_id) if trace_id else None,
            "agent": {
                "name": "nervure",
                "version": self.agent_version,
                "model_name": first_attrs.get("model"),
                "extra": {"provider_id": first_attrs.get("provider_id")},
            },
            "steps": steps,
            "final_metrics": {
                **totals,
                "total_steps": len(steps),
                "extra": {"nervure_trace_records": len(records)},
            },
            "extra": {
                "source": "nervure_trace.jsonl",
                "trace_id": trace_id,
                "conversion": "nervure-trace-to-atif",
            },
        }


def _read_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"line {line_no}: malformed JSON ({exc.msg})")
            continue
        if isinstance(value, dict):
            records.append(value)
        else:
            errors.append(f"line {line_no}: record is not an object")
    return records, errors


def _attrs(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("attributes")
    return value if isinstance(value, dict) else {}


def _tool_calls_by_id(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Recover tool identities retained by normal-level execution spans."""

    calls: dict[str, dict[str, Any]] = {}
    for record in records:
        if record.get("record_type") != "span_start" or record.get("name") not in {
            "tool_preflight",
            "tool_execution",
        }:
            continue
        attrs = _attrs(record)
        call_id = attrs.get("tool_call_id")
        tool_name = attrs.get("tool_name")
        if not call_id or not tool_name:
            continue
        calls.setdefault(
            str(call_id),
            {
                "tool_call_id": str(call_id),
                "function_name": str(tool_name),
                "arguments": _safe_mapping(attrs.get("tool_arguments") or {}),
            },
        )
    return calls


def _safe_text(value: Any) -> str:
    text = value if isinstance(value, str) else str(value)
    return str(sanitize_attributes({"user_prompt": text}, debug=True).get("user_prompt", ""))


def _safe_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return sanitize_attributes({"tool_arguments": value}, debug=True).get("tool_arguments", {})


def _metrics(attrs: dict[str, Any]) -> dict[str, Any]:
    mapping = {
        "input_tokens": "prompt_tokens",
        "output_tokens": "completion_tokens",
        "cache_read_input_tokens": "cached_tokens",
    }
    result = {target: attrs[source] for source, target in mapping.items() if isinstance(attrs.get(source), int)}
    extra = {
        key: attrs[key]
        for key in ("uncached_input_tokens", "reasoning_tokens", "visible_output_tokens", "cache_creation_input_tokens")
        if key in attrs
    }
    if extra:
        result["extra"] = extra
    return result


def _observations_after(
    records: list[dict[str, Any]],
    current: dict[str, Any],
    model_ends: list[dict[str, Any]],
    index: int,
) -> list[dict[str, Any]]:
    start = records.index(current) + 1
    end = records.index(model_ends[index + 1]) if index + 1 < len(model_ends) else len(records)
    results: list[dict[str, Any]] = []
    for record in records[start:end]:
        if record.get("record_type") != "event" or record.get("name") != "tool_result":
            continue
        attrs = _attrs(record)
        result: dict[str, Any] = {
            "source_call_id": attrs.get("tool_call_id"),
            "content": _safe_text(attrs["tool_result_text"]) if isinstance(attrs.get("tool_result_text"), str) else None,
            "extra": {
                key: attrs[key]
                for key in ("tool_name", "is_error", "result_length", "truncated", "duration_ms")
                if key in attrs
            },
        }
        results.append({key: value for key, value in result.items() if value is not None})
    return results


def _totals(records: list[dict[str, Any]]) -> dict[str, int]:
    keys = {
        "input_tokens": "total_prompt_tokens",
        "output_tokens": "total_completion_tokens",
        "cache_read_input_tokens": "total_cached_tokens",
    }
    result = {target: sum(int(_attrs(record).get(source, 0) or 0) for record in records) for source, target in keys.items()}
    return result


def _validate_with_harbor(trajectory: dict[str, Any]) -> tuple[bool | None, list[str]]:
    try:
        from evals.harbor_compat import harbor_trajectory_validator
        validator = harbor_trajectory_validator()()
    except Exception as exc:
        return None, [f"Harbor ATIF validator unavailable: {exc}"]
    valid = validator.validate(trajectory)
    return bool(valid), list(validator.get_errors())
