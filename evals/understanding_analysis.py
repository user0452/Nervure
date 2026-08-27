"""Deterministic analysis helpers for Repo Understanding ablations.

The debug trace contains lifecycle spans for one tool call (preflight,
execution, and result).  This module deliberately treats ``tool_call_id`` as
the unit of work so adoption and sequence metrics cannot double count spans.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
from typing import Any, Iterable


_TOOL_START_NAMES = {"tool_preflight", "tool_execution"}
_REPO_TOOLS = {"repo_map", "symbol_search"}


def read_trace(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def _attrs(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("attributes")
    return value if isinstance(value, dict) else {}


def _json_key(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _tool_id(record: dict[str, Any], index: int) -> str | None:
    call_id = _attrs(record).get("tool_call_id")
    if call_id:
        return str(call_id)
    # Missing IDs are not normal, but retaining a per-record fallback keeps a
    # malformed trace inspectable without merging unrelated calls.
    if record.get("name") == "tool_result":
        return f"missing-id-result-{index}"
    return None


def _model_call_index(records: list[dict[str, Any]]) -> dict[str, int | None]:
    """Map model-emitted tool call IDs to the model turn that emitted them."""

    result: dict[str, int | None] = {}
    for record in records:
        if record.get("record_type") != "span_end" or record.get("name") != "model_call":
            continue
        attrs = _attrs(record)
        turn = attrs.get("turn_count")
        turn_value = int(turn) if isinstance(turn, (int, float)) else None
        for call in attrs.get("tool_calls") or ():
            if isinstance(call, dict) and call.get("tool_call_id"):
                result.setdefault(str(call["tool_call_id"]), turn_value)
    return result


def _turn_before(records: list[dict[str, Any]], index: int) -> int | None:
    turn: int | None = None
    for record in records[: index + 1]:
        if record.get("name") != "transition":
            continue
        value = _attrs(record).get("turn_count")
        if isinstance(value, (int, float)):
            turn = int(value)
    return turn


def unique_tool_calls(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return one record per actual tool call, ordered by result/start time."""

    model_turns = _model_call_index(records)
    starts: dict[str, tuple[int, dict[str, Any]]] = {}
    results: dict[str, tuple[int, dict[str, Any]]] = {}
    for index, record in enumerate(records):
        name = record.get("name")
        attrs = _attrs(record)
        call_id = _tool_id(record, index)
        if not call_id:
            continue
        if name in _TOOL_START_NAMES and record.get("record_type") == "span_start":
            starts.setdefault(call_id, (index, record))
        elif name == "tool_result" and record.get("record_type") == "event":
            results.setdefault(call_id, (index, record))

    ids = set(starts) | set(results)
    ordered_ids = sorted(ids, key=lambda value: (results.get(value) or starts[value])[0])
    calls: list[dict[str, Any]] = []
    for call_id in ordered_ids:
        start_index, start = starts.get(call_id, results.get(call_id, (0, {})))
        result_index, result = results.get(call_id, (start_index, {}))
        start_attrs = _attrs(start)
        result_attrs = _attrs(result)
        name = str(result_attrs.get("tool_name") or start_attrs.get("tool_name") or "unknown")
        arguments = start_attrs.get("tool_arguments")
        if arguments is None:
            arguments = result_attrs.get("tool_arguments")
        turn = model_turns.get(call_id)
        if turn is None:
            turn = _turn_before(records, min(start_index, result_index))
        calls.append({
            "tool_call_id": call_id,
            "tool_name": name,
            "arguments": arguments if isinstance(arguments, dict) else {},
            "turn_count": turn,
            "result": result_attrs,
            "record_index": min(start_index, result_index),
        })
    return calls


def _symbol_match_count(result_text: Any) -> int | None:
    if not isinstance(result_text, str):
        return None
    try:
        payload = json.loads(result_text)
    except (TypeError, json.JSONDecodeError):
        payload = None
    if isinstance(payload, dict):
        for key in ("match_count", "matches_count"):
            if isinstance(payload.get(key), int):
                return payload[key]
    matches = re.findall(r"^\s*\d+\.\s+[^\n]+", result_text, flags=re.MULTILINE)
    return len(matches) if matches else (0 if "no symbol definitions found" in result_text else None)


def analyze_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    calls = unique_tool_calls(records)
    sequence = [call["tool_name"] for call in calls]
    counts = Counter(sequence)
    repo_map_calls = [call for call in calls if call["tool_name"] == "repo_map"]
    symbol_calls = [call for call in calls if call["tool_name"] == "symbol_search"]
    symbol_queries = [call["arguments"].get("query") for call in symbol_calls if call["arguments"].get("query")]
    symbol_match_counts = [
        count for call in symbol_calls
        if (count := _symbol_match_count(call["result"].get("tool_result_text"))) is not None
    ]
    first_symbol_index = next((index for index, name in enumerate(sequence) if name == "symbol_search"), None)
    next_tool = sequence[first_symbol_index + 1] if first_symbol_index is not None and first_symbol_index + 1 < len(sequence) else None
    repeated_search_after_symbol = 0
    if first_symbol_index is not None:
        repeated_search_after_symbol = sum(
            sequence[index] in {"grep", "glob"}
            for index in range(first_symbol_index + 1, len(sequence))
        )
    return {
        "tool_call_count": len(calls),
        "tool_breakdown": dict(sorted(counts.items())),
        "tool_sequence": sequence,
        "repo_map_calls": len(repo_map_calls),
        "symbol_search_calls": len(symbol_calls),
        "repo_map_adopted": bool(repo_map_calls),
        "symbol_search_adopted": bool(symbol_calls),
        "first_repo_map_turn": repo_map_calls[0]["turn_count"] if repo_map_calls else None,
        "first_symbol_search_turn": symbol_calls[0]["turn_count"] if symbol_calls else None,
        "symbol_queries": symbol_queries,
        "symbol_match_counts": symbol_match_counts,
        "next_tool_after_symbol_search": next_tool,
        "repeated_glob_or_grep_after_symbol_search": repeated_search_after_symbol,
        "calls": calls,
    }


def _find_one(root: Path, filename: str) -> Path | None:
    direct = root / filename
    if direct.exists():
        return direct
    return next(root.rglob(filename), None)


def analyze_trial(trial_dir: Path) -> dict[str, Any]:
    trace_path = _find_one(trial_dir, "trace.jsonl")
    result_path = trial_dir / "result.json"
    metrics_path = _find_one(trial_dir, "nervure_metrics.json")
    atif_path = _find_one(trial_dir, "atif_validation.json")
    analysis = analyze_records(read_trace(trace_path)) if trace_path else analyze_records([])
    metrics = json.loads(metrics_path.read_text(encoding="utf-8")) if metrics_path else {}
    atif = json.loads(atif_path.read_text(encoding="utf-8")) if atif_path else {}
    harbor = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
    reward = (harbor.get("verifier_result") or {}).get("rewards", {}).get("reward")
    exception_info = harbor.get("exception_info")
    return {
        "trial_dir": str(trial_dir),
        "reward": reward,
        "atif_valid": atif.get("atif_valid"),
        "exception_info": exception_info,
        "valid": exception_info is None and reward is not None and atif.get("atif_valid") is True,
        "trace_artifact": trace_path is not None,
        "metrics": metrics,
        "analysis": analysis,
    }


def trial_dirs(root: Path) -> list[Path]:
    """Find Harbor trial directories below a job/variant directory."""

    return sorted(
        {path.parent for path in root.rglob("result.json") if (path.parent / "agent").exists()},
        key=lambda path: str(path).casefold(),
    )


def analyze_trials(root: Path) -> list[dict[str, Any]]:
    return [analyze_trial(path) for path in trial_dirs(root)]


def _cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path)
    parser.add_argument("--trial", type=Path)
    args = parser.parse_args()
    if bool(args.trace) == bool(args.trial):
        parser.error("provide exactly one of --trace or --trial")
    result = analyze_records(read_trace(args.trace)) if args.trace else analyze_trial(args.trial)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
