"""Aggregate completed Harbor trials into a reproducible Nervure baseline."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

from evals.artifacts import git_metadata, write_json
from evals.metrics import NervureTraceAnalyzer


def collect_trial(job_dir: Path, *, case_id: str, suite: str) -> dict[str, Any]:
    """Read one completed Harbor job without invoking Harbor or a model."""

    job_result = _read_json(job_dir / "result.json")
    trial_dirs = [
        path
        for path in job_dir.iterdir()
        if path.is_dir() and (path / "result.json").exists()
    ]
    trial_dir = _trial_dir_for_case(job_dir, trial_dirs, case_id)
    trial_result = _read_json(trial_dir / "result.json")
    artifact_root = trial_dir / "artifacts" / "logs" / "agent" / "nervure"
    fallback_root = trial_dir / "agent" / "nervure"
    output_root = artifact_root if artifact_root.is_dir() else fallback_root
    trace_path = _find_trace(trial_dir, artifact_root, fallback_root)
    metrics_path = output_root / "nervure_metrics.json"
    atif_path = output_root / "atif_validation.json"
    # A Harbor-level timeout can terminate the agent before post-run artifact
    # collection.  Preserve that result as an infrastructure failure instead
    # of making baseline collection itself fail or inventing metrics.
    metrics = _read_json(metrics_path) if metrics_path.exists() else (
        NervureTraceAnalyzer().analyze_file(trace_path) if trace_path else {}
    )
    atif = _read_json(atif_path) if atif_path.exists() else {}
    diff_path = output_root / "git_diff.patch"
    reward = _nested(trial_result, "verifier_result", "rewards", "reward")
    exception = trial_result.get("exception_info")
    trace_identity = _trace_identity(trace_path)
    return {
        "case_id": case_id,
        "suite": suite,
        "job_dir": str(job_dir),
        "trial_dir": str(trial_dir),
        "trial_name": trial_result.get("trial_name"),
        "status": _status(reward, exception),
        "reward": reward,
        "exception": exception,
        "model": trace_identity.get("model"),
        "provider": trace_identity.get("provider"),
        "metrics": metrics,
        "atif_valid": atif.get("atif_valid"),
        "atif_errors": atif.get("errors", []),
        "artifacts_complete": metrics_path.exists() and atif_path.exists(),
        "git_diff_bytes": diff_path.stat().st_size if diff_path.exists() else 0,
        "changed_files": _changed_files(diff_path),
        "job_finished_at": job_result.get("finished_at"),
    }


def build_baseline(
    trials: list[dict[str, Any]],
    *,
    repository_root: Path,
    dataset_version: str,
    label: str,
) -> dict[str, Any]:
    """Return a stable JSON-ready V0 summary from completed trial records."""

    if not trials:
        raise ValueError("A baseline requires at least one completed trial.")
    totals = _totals(trials)
    revision = git_metadata(repository_root)
    models = sorted({value for value in (trial.get("model") for trial in trials) if value})
    providers = sorted({value for value in (trial.get("provider") for trial in trials) if value})
    return {
        "schema_version": 1,
        "label": label,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "repository": revision,
        "dataset": {"name": "nervure-medium-coding", "version": dataset_version},
        "models": models,
        "providers": providers,
        "case_ids": [trial["case_id"] for trial in trials],
        "trials": trials,
        "summary": totals,
    }


def write_baseline(baseline: dict[str, Any], destination: Path) -> tuple[Path, Path]:
    """Persist machine-readable JSON and a concise human-readable report."""

    json_path = destination.with_suffix(".json")
    markdown_path = destination.with_suffix(".md")
    write_json(json_path, baseline)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(render_baseline_report(baseline), encoding="utf-8")
    return json_path, markdown_path


def render_baseline_report(baseline: dict[str, Any]) -> str:
    summary = baseline["summary"]
    lines = [
        f"# {baseline['label']}",
        "",
        f"- Dataset: `{baseline['dataset']['name']} {baseline['dataset']['version']}`",
        f"- Revision: `{baseline['repository'].get('revision')}` (dirty: `{baseline['repository'].get('dirty')}`)",
        f"- Models: {', '.join(baseline['models']) or 'unavailable'}",
        f"- Providers: {', '.join(baseline['providers']) or 'unavailable'}",
        "",
        "| Case | Result | Reward | Calls | Uncached | Tools | Time (ms) | ATIF |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for trial in baseline["trials"]:
        main = trial["metrics"].get("main_agent", {})
        tools = trial["metrics"].get("tools", {})
        lines.append(
            "| {case} | {status} | {reward} | {calls} | {uncached} | {tools} | {duration:.3f} | {atif} |".format(
                case=trial["case_id"],
                status=str(trial["status"]).upper(),
                reward=trial.get("reward", "-"),
                calls=main.get("model_calls", 0),
                uncached=main.get("uncached_input_tokens", 0),
                tools=tools.get("tool_calls", 0),
                duration=float(main.get("duration_ms", 0) or 0),
                atif=trial.get("atif_valid"),
            )
        )
    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- Passed: {summary['passed']} / {summary['total']} ({summary['success_rate']:.2%})",
            f"- Infrastructure errors: {summary['infrastructure_errors']}",
        f"- Total model calls: {summary['main']['model_calls']}",
        f"- Average model calls per task: {summary['main']['avg_model_calls_per_task']:.2f}",
            f"- Input / cache read / uncached / output / reasoning: {summary['main']['input_tokens']} / {summary['main']['cache_read_input_tokens']} / {summary['main']['uncached_input_tokens']} / {summary['main']['output_tokens']} / {summary['main']['reasoning_tokens']}",
            f"- Cache hit ratio: {summary['main']['cache_hit_ratio']:.2%}",
        f"- Total runtime (ms): {summary['main']['duration_ms']:.3f}",
        f"- Average runtime per task (ms): {summary['main']['avg_duration_ms_per_task']:.3f}",
            f"- Tools / errors / unknown / permission denied: {summary['tools']['tool_calls']} / {summary['tools']['tool_errors']} / {summary['tools']['unknown_tool']} / {summary['tools']['permission_denied']}",
        f"- Selector fresh / cache hits / parse failures: {summary['memory']['selector_fresh_calls']} / {summary['memory']['selector_cache_hits']} / {summary['memory']['selector_parse_failures']}",
        f"- Selector input / output / reasoning tokens: {summary['memory']['selector_input_tokens']} / {summary['memory']['selector_output_tokens']} / {summary['memory']['selector_reasoning_tokens']}",
        f"- LTM extraction jobs / model calls: {summary['ltm_extraction']['jobs']} / {summary['ltm_extraction']['model_calls']} (the current metrics schema does not expose LTM-only token usage)",
            f"- Full compacts: {summary['full_compact_count']}",
        f"- Subagents: {summary['subagent_child_count']}",
            f"- Main / child / total model calls: {summary['main']['model_calls']} / {summary['child']['model_calls']} / {summary['usage_total']['model_calls']}",
            f"- Child input / cache read / uncached / output: {summary['child']['input_tokens']} / {summary['child']['cache_read_input_tokens']} / {summary['child']['uncached_input_tokens']} / {summary['child']['output_tokens']}",
            f"- Child completed / errors / duration (ms): {summary['subagent_completed_count']} / {summary['subagent_error_count']} / {summary['subagent_duration_ms']:.3f}",
            f"- Changed files / diff bytes: {summary['changed_files']} / {summary['git_diff_bytes']}",
            f"- ATIF all valid: {summary['atif_all_valid']}",
            "",
        ]
    )
    return "\n".join(lines)


def _totals(trials: list[dict[str, Any]]) -> dict[str, Any]:
    main_keys = ("model_calls", "input_tokens", "cache_read_input_tokens", "uncached_input_tokens", "output_tokens", "reasoning_tokens", "duration_ms")
    tool_keys = ("tool_calls", "tool_errors", "unknown_tool", "permission_denied")
    memory_keys = (
        "selector_fresh_calls",
        "selector_cache_hits",
        "selector_parse_failures",
        "selector_input_tokens",
        "selector_output_tokens",
        "selector_reasoning_tokens",
    )
    main = {key: sum(_number(trial["metrics"].get("main_agent", {}).get(key)) for trial in trials) for key in main_keys}
    child = {key: sum(_number(trial["metrics"].get("child_agent", {}).get(key)) for trial in trials) for key in main_keys}
    total = {key: sum(_number(trial["metrics"].get("total", {}).get(key)) for trial in trials) for key in main_keys}
    tools = {key: sum(_number(trial["metrics"].get("tools", {}).get(key)) for trial in trials) for key in tool_keys}
    memory = {key: sum(_number(trial["metrics"].get("memory", {}).get(key)) for trial in trials) for key in memory_keys}
    input_tokens = main["input_tokens"]
    main["cache_hit_ratio"] = (main["cache_read_input_tokens"] / input_tokens) if input_tokens else 0.0
    main["avg_model_calls_per_task"] = main["model_calls"] / len(trials)
    main["avg_duration_ms_per_task"] = main["duration_ms"] / len(trials)
    return {
        "total": len(trials),
        "passed": sum(trial.get("reward") == 1.0 for trial in trials),
        "success_rate": sum(trial.get("reward") == 1.0 for trial in trials) / len(trials),
        "infrastructure_errors": sum(trial.get("status") == "infrastructure_error" for trial in trials),
        "main": main,
        "child": child,
        "usage_total": total,
        "tools": tools,
        "memory": memory,
        "ltm_extraction": {
            "jobs": sum(_number(trial["metrics"].get("ltm_extraction", {}).get("jobs")) for trial in trials),
            "model_calls": sum(_number(trial["metrics"].get("ltm_extraction", {}).get("model_calls")) for trial in trials),
        },
        "full_compact_count": sum(_number(trial["metrics"].get("compact", {}).get("full_compact_count")) for trial in trials),
        "subagent_child_count": sum(_number(trial["metrics"].get("subagent", {}).get("child_count")) for trial in trials),
        "subagent_completed_count": sum(_number(trial["metrics"].get("subagent", {}).get("completed_count")) for trial in trials),
        "subagent_error_count": sum(_number(trial["metrics"].get("subagent", {}).get("error_count")) for trial in trials),
        "subagent_duration_ms": sum(_number(trial["metrics"].get("subagent", {}).get("child_duration_ms")) for trial in trials),
        "changed_files": sum(len(trial.get("changed_files", [])) for trial in trials),
        "git_diff_bytes": sum(_number(trial.get("git_diff_bytes")) for trial in trials),
        "atif_all_valid": all(trial.get("atif_valid") is True for trial in trials),
    }


def _trial_dir_for_case(
    job_dir: Path,
    trial_dirs: list[Path],
    case_id: str,
) -> Path:
    if len(trial_dirs) == 1:
        return trial_dirs[0]
    matches = [
        path
        for path in trial_dirs
        if path.name == case_id or path.name.startswith(f"{case_id}__")
    ]
    if len(matches) == 1:
        return matches[0]
    raise ValueError(
        f"Expected exactly one trial for {case_id!r} under {job_dir}; "
        f"found {len(matches)} matches among {len(trial_dirs)} trials."
    )


def _trace_identity(path: Path | None) -> dict[str, str | None]:
    if path is None or not path.exists():
        return {"model": None, "provider": None}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("record_type") == "span_end" and record.get("name") == "model_call":
            attrs = record.get("attributes") or {}
            return {"model": attrs.get("model"), "provider": attrs.get("provider_id")}
    return {"model": None, "provider": None}


def _find_trace(trial_dir: Path, artifact_root: Path, fallback_root: Path) -> Path | None:
    candidates = [artifact_root / "trace.jsonl", fallback_root / "trace.jsonl"]
    candidates.extend((trial_dir / "artifacts" / "workspace").glob(".onecode/sessions/*/trace.jsonl"))
    return next((path for path in candidates if path.exists()), None)


def _changed_files(path: Path) -> list[str]:
    if not path.exists():
        return []
    return re.findall(r"^diff --git a/(.*?) b/", path.read_text(encoding="utf-8", errors="replace"), flags=re.MULTILINE)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _nested(value: dict[str, Any], *keys: str) -> Any:
    current: Any = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _status(reward: Any, exception: Any) -> str:
    if exception:
        if _is_setup_failure(exception):
            return "infrastructure_error"
        if _is_provider_infrastructure_failure(exception):
            return "infrastructure_error"
        exception_type = exception.get("exception_type") if isinstance(exception, dict) else None
        # Once Harbor has finished agent setup, timeout/non-zero exit describe
        # the evaluated agent process itself and belong in the benchmark.
        if exception_type in {"AgentTimeoutError", "NonZeroAgentExitCodeError"}:
            return "agent_failure"
        return "infrastructure_error"
    return "pass" if reward == 1.0 else "agent_failure"


def _is_setup_failure(exception: Any) -> bool:
    """Return True when Harbor failed before the evaluated agent started."""

    if not isinstance(exception, dict):
        return False
    traceback = str(exception.get("exception_traceback") or "")
    setup_markers = (
        "_setup_agent",
        "await self.install(environment)",
    )
    return any(marker in traceback for marker in setup_markers)


def _is_provider_infrastructure_failure(exception: Any) -> bool:
    """Classify a started agent that could not reach an available provider.

    Harbor reports the resulting process exit as ``NonZeroAgentExitCodeError``;
    the traceback is the only durable distinction between a provider outage and
    an agent that ran and failed its task.  Keep this marker list narrow and
    evidence-based so normal task failures remain ``agent_failure``.
    """

    if not isinstance(exception, dict):
        return False
    text = str(exception.get("exception_traceback") or "").lower()
    return any(
        marker in text
        for marker in (
            "retryexhaustederror",
            "no available channel",
            "status_code=503",
            "status_code: 503",
            "http 503",
            "service unavailable",
        )
    )


def _number(value: Any) -> float | int:
    return value if isinstance(value, (int, float)) else 0
