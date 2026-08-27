from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from evals.agent import NervureAgent, _install_command, _prepare_logs_command
from evals.cases import CASES, SUITES
from evals.metrics import NervureTraceAnalyzer
from evals.selection import select_case_ids
from evals.report import render_trial_report
from evals.trace_atif import NervureTraceToATIFConverter
from evals.harbor_compat import HARBOR_COMMIT, HARBOR_TAG, HARBOR_VERSION
from evals import cli as eval_cli
from evals import headless as eval_headless
from evals.baseline import _status, build_baseline, collect_trial, write_baseline


def _records() -> list[dict]:
    return [
        {
            "record_type": "span_start",
            "timestamp": "2026-08-17T00:00:00Z",
            "session_id": "s1",
            "trace_id": "t1",
            "name": "interaction",
            "span_id": "i1",
            "attributes": {"user_prompt": "Fix calculator", "user_turn_id": "u1"},
        },
        {
            "record_type": "span_end",
            "timestamp": "2026-08-17T00:00:01Z",
            "session_id": "s1",
            "trace_id": "t1",
            "name": "model_call",
            "attributes": {
                "provider_id": "test",
                "model": "test-model",
                "assistant_visible_text": "I will inspect it.",
                "provider_reasoning_text": "Need verify arithmetic.",
                "tool_calls": [{"tool_name": "read_file", "tool_call_id": "c1", "arguments": {"path": "calculator.py", "api_key": "secret"}}],
                "input_tokens": 10,
                "output_tokens": 4,
                "cache_read_input_tokens": 3,
                "uncached_input_tokens": 7,
                "reasoning_tokens": 2,
                "visible_output_tokens": 2,
                "stop_reason": "tool_calls",
                "duration_ms": 12.5,
            },
        },
        {
            "record_type": "event",
            "timestamp": "2026-08-17T00:00:02Z",
            "session_id": "s1",
            "trace_id": "t1",
            "name": "tool_result",
            "attributes": {"tool_name": "read_file", "tool_call_id": "c1", "tool_result_text": "safe result", "result_length": 11, "duration_ms": 2},
        },
    ]


def test_nervure_agent_is_importable_without_harbor() -> None:
    agent = NervureAgent(source_root=Path.cwd(), revision="test-revision")
    assert agent.name() == "nervure"
    assert agent.version() == "test-revision"


def test_eval_install_uses_constraints_and_retries() -> None:
    command = _install_command()

    assert "-c /opt/nervure/evals/runtime-constraints.txt" in command
    assert "-e /opt/nervure" in command
    assert "for attempt in 1 2 3" in command
    assert "sleep 2" in command


def test_eval_run_prepares_writable_agent_log_dir() -> None:
    command = _prepare_logs_command()

    assert "mkdir -p /logs/agent/nervure" in command
    assert "chmod 777 /logs/agent /logs/agent/nervure" in command


def test_runtime_dependency_keeps_mcp_on_v1() -> None:
    root = Path(__file__).parents[1]
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    constraints = (root / "evals" / "runtime-constraints.txt").read_text(encoding="utf-8")

    assert '"mcp>=1.28.1,<2"' in pyproject
    assert "mcp==1.28.1" in constraints


def test_trace_to_atif_maps_user_reasoning_tools_and_redacts_secret() -> None:
    result = NervureTraceToATIFConverter().convert_records(_records())
    assert result["steps"][0]["source"] == "user"
    assert result["steps"][1]["source"] == "agent"
    assert result["steps"][1]["reasoning_content"] == "Need verify arithmetic."
    assert result["steps"][1]["tool_calls"][0]["function_name"] == "read_file"
    assert result["steps"][1]["tool_calls"][0]["arguments"]["api_key"] == "[redacted]"
    assert result["steps"][1]["observation"]["results"][0]["content"] == "safe result"


def test_trace_to_atif_recovers_normal_trace_tool_identity_from_execution_span() -> None:
    records = _records()
    records[1]["attributes"]["tool_calls"] = []
    records.insert(
        2,
        {
            "record_type": "span_start",
            "name": "tool_preflight",
            "attributes": {"tool_name": "read_file", "tool_call_id": "c1"},
        },
    )

    result = NervureTraceToATIFConverter().convert_records(records)

    assert result["steps"][1]["tool_calls"] == [
        {"tool_call_id": "c1", "function_name": "read_file", "arguments": {}}
    ]


def test_trace_analyzer_emits_nervure_metrics() -> None:
    metrics = NervureTraceAnalyzer().analyze_records(_records())
    assert metrics["main_agent"]["model_calls"] == 1
    assert metrics["main_agent"]["uncached_input_tokens"] == 7
    assert metrics["tools"]["tool_calls"] == 1
    assert metrics["context"]["context_prepare_count"] == 0


def test_agent_post_run_builds_metrics_and_trajectory_from_trace(tmp_path: Path) -> None:
    logs_dir = tmp_path / "logs"
    output_dir = logs_dir / "nervure"
    output_dir.mkdir(parents=True)
    (output_dir / "trace.jsonl").write_text(
        "\n".join(json.dumps(record) for record in _records()) + "\n",
        encoding="utf-8",
    )
    agent = NervureAgent(logs_dir=logs_dir, source_root=Path.cwd(), revision="test-revision")
    context = SimpleNamespace(metadata={}, n_input_tokens=None, n_cache_tokens=None, n_output_tokens=None)

    agent.populate_context_post_run(context)

    assert json.loads((output_dir / "nervure_metrics.json").read_text(encoding="utf-8"))["main_agent"]["model_calls"] == 1
    assert json.loads((output_dir / "trajectory.json").read_text(encoding="utf-8"))["agent"]["version"] == "test-revision"
    assert (output_dir / "atif_validation.json").exists()
    assert (output_dir / "nervure_report.md").exists()
    assert context.n_input_tokens == 10


def test_headless_uses_scoped_noninteractive_permission_prompter(
    monkeypatch, tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    class Runtime:
        attachment_collector = None
        state = object()

    def build_runtime(*_args, **kwargs):
        captured.update(kwargs)
        return Runtime()

    async def consume_stream(*_args, **_kwargs) -> str:
        return ""

    async def shutdown(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(eval_headless, "build_runtime", build_runtime)
    monkeypatch.setattr(eval_headless, "_consume_stream", consume_stream)
    monkeypatch.setattr(eval_headless, "_shutdown", shutdown)

    assert asyncio.run(eval_headless.run_headless("smoke", tmp_path)) == 0
    prompter = captured["permission_prompter"]
    assert isinstance(prompter, eval_headless.HarborWorkspacePermissionPrompter)
    response = asyncio.run(prompter.request_permission(None))
    assert response.action == "allow"
    assert response.scope == "session"


def test_selection_is_seed_reproducible() -> None:
    assert select_case_ids(count=4, seed=42) == select_case_ids(count=4, seed=42)
    assert set(SUITES["smoke"]) == {"xiangqi_flying_general", "vcs_staging_snapshot", "route_cache_invalidation", "taskflow_retry_state"}
    assert len(CASES) == 12
    assert all(case.available for case in CASES)


def test_medium_tasks_are_registered_and_structurally_valid() -> None:
    root = Path(__file__).parents[1]
    assert all(eval_cli.task_path(root, case.case_id).is_dir() for case in CASES)
    assert eval_cli.validate_dataset(root) == 0


def test_full_suite_uses_one_harbor_job_with_twelve_way_concurrency(
    monkeypatch,
) -> None:
    root = Path(__file__).parents[1]
    monkeypatch.setattr(eval_cli, "_provider_environment", lambda: {})
    command = eval_cli.harbor_full_suite_command(root, job_name="full-test")

    assert command[:4] == ["harbor", "run", "--path", str(root / "evals" / "tasks")]
    assert command[command.index("--exclude-task-name") + 1] == "nervure-eval-poc"
    assert command[command.index("--n-concurrent") + 1] == "12"
    assert command[command.index("--job-name") + 1] == "full-test"


def test_smoke_suite_uses_exact_four_tasks_and_configurable_concurrency(monkeypatch) -> None:
    root = Path(__file__).parents[1]
    monkeypatch.setattr(eval_cli, "_provider_environment", lambda: {})
    command = eval_cli.harbor_smoke_suite_command(root, job_name="smoke-test", n_concurrent=4)

    assert command[:4] == ["harbor", "run", "--path", str(root / "evals" / "tasks")]
    assert command.count("--include-task-name") == 4
    assert command[command.index("--n-concurrent") + 1] == "4"
    assert command[command.index("--job-name") + 1] == "smoke-test"


def test_full_job_baseline_collection_reads_all_twelve_trials(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[Path, str, str]] = []

    def fake_collect(job_dir: Path, *, case_id: str, suite: str) -> dict:
        calls.append((job_dir, case_id, suite))
        return {"case_id": case_id}

    monkeypatch.setattr(eval_cli, "collect_trial", fake_collect)
    trials = eval_cli.collect_baseline_trials(full_job=tmp_path / "full-job")

    assert len(trials) == 12
    assert [case_id for _, case_id, _ in calls] == list(eval_cli.suite_case_ids("full"))
    assert all(job_dir == tmp_path / "full-job" for job_dir, _, _ in calls)
    assert all(suite == "full" for _, _, suite in calls)


def test_medium_tasks_collect_the_same_nervure_artifacts_as_the_poc() -> None:
    root = Path(__file__).parents[1]
    for case in CASES:
        config = (eval_cli.task_path(root, case.case_id) / "task.toml").read_text(encoding="utf-8")
        for artifact in eval_cli.REQUIRED_NERVURE_ARTIFACTS:
            assert artifact in config


def test_baseline_collector_aggregates_harbor_artifacts(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    trial_dir = job_dir / "case__trial"
    output_dir = trial_dir / "artifacts" / "logs" / "agent" / "nervure"
    output_dir.mkdir(parents=True)
    (job_dir / "result.json").write_text("{}", encoding="utf-8")
    (trial_dir / "result.json").write_text(
        json.dumps({"trial_name": "case__trial", "verifier_result": {"rewards": {"reward": 1.0}}}),
        encoding="utf-8",
    )
    (output_dir / "nervure_metrics.json").write_text(
        json.dumps({"main_agent": {"model_calls": 2, "input_tokens": 10, "cache_read_input_tokens": 4, "uncached_input_tokens": 6, "output_tokens": 3, "reasoning_tokens": 0, "duration_ms": 7}, "tools": {"tool_calls": 1, "tool_errors": 0, "unknown_tool": 0, "permission_denied": 0}, "memory": {"selector_fresh_calls": 1, "selector_cache_hits": 0, "selector_parse_failures": 0}, "ltm_extraction": {"jobs": 0}, "compact": {"full_compact_count": 0}, "subagent": {"child_count": 0}}),
        encoding="utf-8",
    )
    (output_dir / "atif_validation.json").write_text('{"atif_valid": true, "errors": []}', encoding="utf-8")
    (output_dir / "git_diff.patch").write_text("diff --git a/a.py b/a.py\n", encoding="utf-8")
    (output_dir / "trace.jsonl").write_text(json.dumps({"record_type": "span_end", "name": "model_call", "attributes": {"model": "model-a", "provider_id": "provider-a"}}) + "\n", encoding="utf-8")

    trial = collect_trial(job_dir, case_id="case", suite="full")
    baseline = build_baseline([trial], repository_root=Path(__file__).parents[1], dataset_version="0.1.0", label="nervure-v0-baseline")
    json_path, markdown_path = write_baseline(baseline, tmp_path / "baseline")

    assert trial["status"] == "pass"
    assert baseline["summary"]["success_rate"] == 1.0
    assert baseline["summary"]["atif_all_valid"] is True
    assert json_path.exists() and markdown_path.exists()


def test_baseline_collector_selects_case_from_multi_trial_job(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    target_dir = job_dir / "route_batch_perf__abc123"
    other_dir = job_dir / "taskflow_cycle_detection__def456"
    output_dir = target_dir / "artifacts" / "logs" / "agent" / "nervure"
    output_dir.mkdir(parents=True)
    other_dir.mkdir(parents=True)
    (job_dir / "result.json").write_text("{}", encoding="utf-8")
    (target_dir / "result.json").write_text(
        json.dumps({"trial_name": target_dir.name, "verifier_result": {"rewards": {"reward": 1.0}}}),
        encoding="utf-8",
    )
    (other_dir / "result.json").write_text(
        json.dumps({"trial_name": other_dir.name, "verifier_result": {"rewards": {"reward": 0.0}}}),
        encoding="utf-8",
    )
    (output_dir / "nervure_metrics.json").write_text("{}", encoding="utf-8")
    (output_dir / "atif_validation.json").write_text('{"atif_valid": true, "errors": []}', encoding="utf-8")

    trial = collect_trial(job_dir, case_id="route_batch_perf", suite="full")

    assert trial["trial_name"] == "route_batch_perf__abc123"
    assert trial["reward"] == 1.0


def test_baseline_classifier_marks_agent_install_failure_as_infrastructure() -> None:
    exception = {
        "exception_type": "NonZeroAgentExitCodeError",
        "exception_traceback": (
            "trial.run -> _prepare -> _setup_agent -> setup -> "
            "await self.install(environment) -> pip install failed"
        ),
    }

    assert _status(None, exception) == "infrastructure_error"


def test_baseline_classifier_keeps_started_agent_nonzero_exit_as_agent_failure() -> None:
    exception = {
        "exception_type": "NonZeroAgentExitCodeError",
        "exception_traceback": "trial.run -> agent.run -> command exited 1",
    }

    assert _status(None, exception) == "agent_failure"


def test_baseline_classifier_separates_provider_503_from_agent_failure() -> None:
    exception = {
        "exception_type": "NonZeroAgentExitCodeError",
        "exception_traceback": (
            "trial.run -> agent.run -> ProviderError: "
            "No available channel for model mimo-v2.5-pro-1m (HTTP 503)"
        ),
    }

    assert _status(None, exception) == "infrastructure_error"


def test_poc_has_separate_verifier_and_hidden_grader_outside_agent_image() -> None:
    root = Path(__file__).parents[1]
    task = root / "evals" / "tasks" / "nervure-eval-poc"
    config = (task / "task.toml").read_text(encoding="utf-8")
    assert 'environment_mode = "separate"' in config
    assert not list((task / "environment").rglob("grader.py"))
    assert (task / "tests" / "grader.py").exists()
    environment = (task / "environment" / "Dockerfile").read_text(encoding="utf-8")
    assert "git init -q" in environment


def test_poc_targets_current_harbor_task_schema_and_network_modes() -> None:
    root = Path(__file__).parents[1]
    task = root / "evals" / "tasks" / "nervure-eval-poc"
    config = (task / "task.toml").read_text(encoding="utf-8")
    assert HARBOR_VERSION == "0.21.0"
    assert HARBOR_TAG == "v0.21.0"
    assert HARBOR_COMMIT == "64afbbcb62165950301e1a6407c729aa26d844ff"
    assert 'schema_version = "1.4"' in config
    assert 'network_mode = "public"' in config
    assert 'network_mode = "no-network"' in config


def test_jsonl_converter_tolerates_malformed_line(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    path.write_text("{bad}\n" + "\n".join(json.dumps(item) for item in _records()) + "\n", encoding="utf-8")
    result = NervureTraceToATIFConverter().convert_file(path)
    assert result.source_errors
    assert result.trajectory["steps"]


def test_trial_report_is_summary_only() -> None:
    metrics = NervureTraceAnalyzer().analyze_records(_records())
    report = render_trial_report(metrics, atif_valid=None, revision="r1")
    assert "Model calls" in report
    assert "Need verify arithmetic" not in report


def test_provider_environment_maps_legacy_anthropic_fields_for_custom_agent(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        eval_cli,
        "dotenv_values",
        lambda *_args, **_kwargs: {
            "ONECODE_PROVIDER_ID": "custom",
            "ANTHROPIC_BASE_URL": "https://gateway.example/v1",
            "ANTHROPIC_AUTH_TOKEN": "token-value",
            "ANTHROPIC_MODEL": "model-name",
        },
    )
    for key in ("CUSTOM_BASE_URL", "CUSTOM_API_KEY", "CUSTOM_MODEL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gateway.example/v1")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token-value")
    monkeypatch.setenv("ANTHROPIC_MODEL", "model-name")

    values = eval_cli._provider_environment()

    assert values["CUSTOM_BASE_URL"] == "https://gateway.example/v1"
    assert values["CUSTOM_API_KEY"] == "token-value"
    assert values["CUSTOM_MODEL"] == "model-name"


def test_repo_map_ablation_flag_is_forwarded_to_harbor_agent_env(monkeypatch) -> None:
    root = Path(__file__).parents[1]
    monkeypatch.setenv("NERVURE_DISABLE_REPO_MAP", "1")
    monkeypatch.setattr(eval_cli, "_provider_environment", lambda: {"NERVURE_DISABLE_REPO_MAP": "1"})

    command = eval_cli.harbor_command(root, "xiangqi_flying_general", job_name="ablation-off")

    assert "NERVURE_DISABLE_REPO_MAP=1" in command


def test_factorial_ablation_flags_are_forwarded_to_harbor_agent_env(monkeypatch) -> None:
    root = Path(__file__).parents[1]
    monkeypatch.setenv("NERVURE_DISABLE_REPO_MAP", "1")
    monkeypatch.setenv("NERVURE_DISABLE_SYMBOL_SEARCH", "1")
    monkeypatch.setattr(
        eval_cli,
        "_provider_environment",
        lambda: {
            "NERVURE_DISABLE_REPO_MAP": "1",
            "NERVURE_DISABLE_SYMBOL_SEARCH": "1",
        },
    )

    command = eval_cli.harbor_command(root, "vcs_staging_snapshot", job_name="factorial-none")

    assert "NERVURE_DISABLE_REPO_MAP=1" in command
    assert "NERVURE_DISABLE_SYMBOL_SEARCH=1" in command


def test_nervure_agent_copies_repo_map_ablation_flag_into_headless_env(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}
    agent = NervureAgent(
        logs_dir=Path("/tmp/logs"),
        source_root=Path.cwd(),
        revision="test-revision",
        extra_env={"NERVURE_DISABLE_REPO_MAP": "1"},
    )

    async def fake_root(*_args, **_kwargs) -> None:
        return None

    async def fake_agent(*_args, **kwargs) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(agent, "exec_as_root", fake_root, raising=False)
    monkeypatch.setattr(agent, "exec_as_agent", fake_agent, raising=False)

    asyncio.run(agent.run("smoke", object(), None))

    assert captured["env"]["NERVURE_DISABLE_REPO_MAP"] == "1"


def test_nervure_agent_copies_both_ablation_flags_into_headless_env(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}
    agent = NervureAgent(
        logs_dir=Path("/tmp/logs"),
        source_root=Path.cwd(),
        revision="test-revision",
        extra_env={
            "NERVURE_DISABLE_REPO_MAP": "0",
            "NERVURE_DISABLE_SYMBOL_SEARCH": "1",
        },
    )

    async def fake_root(*_args, **_kwargs) -> None:
        return None

    async def fake_agent(*_args, **kwargs) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(agent, "exec_as_root", fake_root, raising=False)
    monkeypatch.setattr(agent, "exec_as_agent", fake_agent, raising=False)

    asyncio.run(agent.run("smoke", object(), None))

    assert captured["env"]["NERVURE_DISABLE_REPO_MAP"] == "0"
    assert captured["env"]["NERVURE_DISABLE_SYMBOL_SEARCH"] == "1"


def test_nervure_agent_copies_subagent_ablation_flag_into_headless_env(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}
    agent = NervureAgent(
        logs_dir=Path("/tmp/logs"),
        source_root=Path.cwd(),
        revision="test-revision",
        extra_env={"NERVURE_DISABLE_SUBAGENT": "1"},
    )

    async def fake_root(*_args, **_kwargs) -> None:
        return None

    async def fake_agent(*_args, **kwargs) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(agent, "exec_as_root", fake_root, raising=False)
    monkeypatch.setattr(agent, "exec_as_agent", fake_agent, raising=False)

    asyncio.run(agent.run("smoke", object(), None))

    assert captured["env"]["NERVURE_DISABLE_SUBAGENT"] == "1"
