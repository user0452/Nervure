from __future__ import annotations

from pathlib import Path

from evals.understanding_analysis import analyze_records


def _trace() -> list[dict]:
    return [
        {"record_type": "span_end", "name": "model_call", "attributes": {
            "turn_count": 2,
            "tool_calls": [{"tool_name": "symbol_search", "tool_call_id": "call-1", "arguments": {"query": "Graph.update_weight"}}],
        }},
        {"record_type": "span_start", "name": "tool_preflight", "attributes": {
            "tool_name": "symbol_search", "tool_call_id": "call-1", "tool_arguments": {"query": "Graph.update_weight"},
        }},
        {"record_type": "span_end", "name": "tool_preflight", "attributes": {"tool_name": "symbol_search", "tool_call_id": "call-1"}},
        {"record_type": "span_start", "name": "tool_execution", "attributes": {
            "tool_name": "symbol_search", "tool_call_id": "call-1", "tool_arguments": {"query": "Graph.update_weight"},
        }},
        {"record_type": "span_end", "name": "tool_execution", "attributes": {"tool_name": "symbol_search", "tool_call_id": "call-1"}},
        {"record_type": "event", "name": "tool_result", "attributes": {
            "tool_name": "symbol_search", "tool_call_id": "call-1",
            "tool_result_text": "Symbol matches for: Graph.update_weight\n1. route_lab/graph.py:30",
        }},
        {"record_type": "span_start", "name": "tool_preflight", "attributes": {
            "tool_name": "glob", "tool_call_id": "call-2", "tool_arguments": {"pattern": "**/*.py"},
        }},
        {"record_type": "span_start", "name": "tool_execution", "attributes": {
            "tool_name": "glob", "tool_call_id": "call-2", "tool_arguments": {"pattern": "**/*.py"},
        }},
        {"record_type": "event", "name": "tool_result", "attributes": {
            "tool_name": "glob", "tool_call_id": "call-2", "tool_result_text": "Found 3 files",
        }},
    ]


def test_analysis_counts_one_call_per_tool_call_id() -> None:
    result = analyze_records(_trace())

    assert result["tool_call_count"] == 2
    assert result["tool_breakdown"] == {"glob": 1, "symbol_search": 1}
    assert result["tool_sequence"] == ["symbol_search", "glob"]
    assert result["symbol_search_calls"] == 1
    assert result["first_symbol_search_turn"] == 2
    assert result["symbol_queries"] == ["Graph.update_weight"]
    assert result["symbol_match_counts"] == [1]
    assert result["next_tool_after_symbol_search"] == "glob"
    assert result["repeated_glob_or_grep_after_symbol_search"] == 1


def test_analysis_counts_repo_map_and_symbol_search_independently() -> None:
    records = _trace()
    records.extend([
        {"record_type": "span_start", "name": "tool_preflight", "attributes": {
            "tool_name": "repo_map", "tool_call_id": "call-3", "tool_arguments": {},
        }},
        {"record_type": "event", "name": "tool_result", "attributes": {
            "tool_name": "repo_map", "tool_call_id": "call-3", "tool_result_text": "Repository",
        }},
    ])
    result = analyze_records(records)
    assert result["repo_map_calls"] == 1
    assert result["symbol_search_calls"] == 1
    assert len(result["calls"]) == 3


def test_all_harbor_task_images_install_ripgrep_at_build_time() -> None:
    root = Path(__file__).parents[1] / "evals" / "tasks"
    dockerfiles = list(root.glob("*/environment/Dockerfile"))
    assert dockerfiles
    assert all("ripgrep" in path.read_text(encoding="utf-8") for path in dockerfiles)


def test_symbol_probe_instructions_name_real_symbols_without_tool_or_path_leaks() -> None:
    root = Path(__file__).parents[1] / "evals" / "tasks"
    expected = {
        "symbol_taskflow_retry_recovery": "Scheduler._unblock_after_success",
        "symbol_vcs_snapshot_commit": "Repository.commit",
        "symbol_route_mutation_notify": "Graph.update_weight",
        "symbol_xiangqi_move_legality": "is_legal_move",
    }
    for task, symbol in expected.items():
        text = (root / task / "instruction.md").read_text(encoding="utf-8")
        assert symbol in text
        assert "symbol_search" not in text
        assert "/workspace/" not in text
        assert "先定位" in text
