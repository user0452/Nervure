"""Thin Nervure evaluation CLI; Harbor remains the actual runner."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib

from dotenv import dotenv_values

from evals.artifacts import git_metadata, read_json, write_json
from evals.baseline import build_baseline, collect_trial, write_baseline
from evals.cases import CASES, SUITES, case_by_id
from evals.harbor_compat import HARBOR_TAG, HarborUnavailableError, require_harbor
from evals.metrics import NervureTraceAnalyzer
from evals.selection import select_case_ids, suite_case_ids


REQUIRED_NERVURE_ARTIFACTS = (
    "/workspace",
    "/logs/agent/nervure/trace.jsonl",
    "/logs/agent/nervure/trajectory.json",
    "/logs/agent/nervure/nervure_metrics.json",
    "/logs/agent/nervure/atif_validation.json",
    "/logs/agent/nervure/git_diff.patch",
    "/logs/agent/nervure/task_metadata.json",
    "/logs/agent/nervure/nervure_report.md",
)

DEFAULT_FULL_SUITE_CONCURRENCY = 12


def repository_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    raise RuntimeError("Could not locate the current Git repository root.")


def task_path(root: Path, case_id: str) -> Path:
    if case_id == "nervure-eval-poc":
        return root / "evals" / "tasks" / case_id
    try:
        case_by_id(case_id)
    except KeyError as exc:
        raise RuntimeError(f"Unknown Nervure eval case: {case_id!r}") from exc
    task = root / "evals" / "tasks" / case_id
    if task.is_dir():
        return task
    raise RuntimeError(f"Case {case_id!r} is registered but its Harbor task fixture is missing.")


def harbor_command(
    root: Path,
    case_id: str,
    *,
    job_name: str | None = None,
    environment: str = "docker",
) -> list[str]:
    task = task_path(root, case_id)
    revision = _git_revision(root)
    command = [
        "harbor", "run", "--path", str(task),
        "--agent", "evals.agent:NervureAgent",
        "--env", environment,
        "--agent-kwarg", f"source_root={root}",
        "--agent-kwarg", f"revision={revision}",
        "--agent-env", "NERVURE_TRACE_LEVEL=debug",
        "--agent-env", "NERVURE_MAX_TOOL_RESULT_CHARS=12000",
        "--n-concurrent", "1",
    ]
    jobs_dir = os.environ.get("NERVURE_EVAL_JOBS_DIR")
    if jobs_dir:
        command.extend(["--jobs-dir", jobs_dir])
    if job_name:
        command.extend(["--job-name", job_name])
    for key, value in _provider_environment().items():
        command.extend(["--agent-env", f"{key}={value}"])
    return command


def harbor_full_suite_command(
    root: Path,
    *,
    job_name: str | None = None,
    environment: str = "docker",
    n_concurrent: int = DEFAULT_FULL_SUITE_CONCURRENCY,
) -> list[str]:
    """Run all 12 medium tasks as one Harbor job using Harbor's own scheduler."""

    if n_concurrent < 1:
        raise ValueError("n_concurrent must be at least 1")
    revision = _git_revision(root)
    command = [
        "harbor", "run", "--path", str(root / "evals" / "tasks"),
        "--exclude-task-name", "nervure-eval-poc",
        "--agent", "evals.agent:NervureAgent",
        "--env", environment,
        "--agent-kwarg", f"source_root={root}",
        "--agent-kwarg", f"revision={revision}",
        "--agent-env", "NERVURE_TRACE_LEVEL=debug",
        "--agent-env", "NERVURE_MAX_TOOL_RESULT_CHARS=12000",
        "--n-concurrent", str(n_concurrent),
    ]
    jobs_dir = os.environ.get("NERVURE_EVAL_JOBS_DIR")
    if jobs_dir:
        command.extend(["--jobs-dir", jobs_dir])
    if job_name:
        command.extend(["--job-name", job_name])
    for key, value in _provider_environment().items():
        command.extend(["--agent-env", f"{key}={value}"])
    return command


def harbor_smoke_suite_command(
    root: Path,
    *,
    job_name: str | None = None,
    environment: str = "docker",
    n_concurrent: int = 4,
) -> list[str]:
    """Run exactly the four stable smoke tasks as one Harbor job."""

    if n_concurrent < 1:
        raise ValueError("n_concurrent must be at least 1")
    revision = _git_revision(root)
    command = [
        "harbor", "run", "--path", str(root / "evals" / "tasks"),
        "--agent", "evals.agent:NervureAgent",
        "--env", environment,
        "--agent-kwarg", f"source_root={root}",
        "--agent-kwarg", f"revision={revision}",
        "--agent-env", "NERVURE_TRACE_LEVEL=debug",
        "--agent-env", "NERVURE_MAX_TOOL_RESULT_CHARS=12000",
        "--n-concurrent", str(n_concurrent),
    ]
    for case_id in SUITES["smoke"]:
        command.extend(["--include-task-name", case_id])
    jobs_dir = os.environ.get("NERVURE_EVAL_JOBS_DIR")
    if jobs_dir:
        command.extend(["--jobs-dir", jobs_dir])
    if job_name:
        command.extend(["--job-name", job_name])
    for key, value in _provider_environment().items():
        command.extend(["--agent-env", f"{key}={value}"])
    return command


def _run_harbor_command(root: Path, command: list[str]) -> int:
    print("Running Harbor:", " ".join(_redact_command(command)))
    # Harbor reads UTF-8 task files and emits Unicode progress glyphs.  On
    # Windows locales such as GBK, inherit explicit UTF-8 settings so task
    # loading and Rich rendering do not fail before the trial starts.
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return subprocess.run(command, cwd=root, check=False, env=env).returncode


def _check_harbor_version() -> int | None:
    try:
        harbor = require_harbor()
    except HarborUnavailableError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    installed = getattr(harbor, "__version__", None)
    if installed and installed != HARBOR_TAG.removeprefix("v"):
        print(f"Warning: validated Harbor is {HARBOR_TAG}, installed version is {installed}.", file=sys.stderr)
    return None


def run_case(root: Path, case_id: str) -> int:
    unavailable = _check_harbor_version()
    if unavailable is not None:
        return unavailable
    job_name = os.environ.get("NERVURE_EVAL_JOB_NAME") or f"nervure-{case_id}"
    return _run_harbor_command(root, harbor_command(root, case_id, job_name=job_name))


def run_full_suite(
    root: Path,
    *,
    n_concurrent: int = DEFAULT_FULL_SUITE_CONCURRENCY,
) -> int:
    unavailable = _check_harbor_version()
    if unavailable is not None:
        return unavailable
    job_name = os.environ.get("NERVURE_EVAL_JOB_NAME") or "nervure-full"
    command = harbor_full_suite_command(
        root,
        job_name=job_name,
        n_concurrent=n_concurrent,
    )
    return _run_harbor_command(root, command)


def validate_dataset(root: Path) -> int:
    failures: list[str] = []
    task_ids = ("nervure-eval-poc", *(case.case_id for case in CASES))
    for case_id in task_ids:
        task = root / "evals" / "tasks" / case_id
        if not task.is_dir():
            failures.append(f"missing {case_id}")
            continue
        for relative in ("task.toml", "instruction.md", "environment/Dockerfile", "tests/Dockerfile", "tests/test.sh"):
            if not (task / relative).exists():
                failures.append(f"{case_id} missing {relative}")
        try:
            config = tomllib.loads((task / "task.toml").read_text(encoding="utf-8"))
            if config.get("schema_version") != "1.4":
                failures.append(f"{case_id} must use Harbor task schema 1.4")
            if config.get("verifier", {}).get("environment_mode") != "separate":
                failures.append(f"{case_id} verifier must use a separate environment")
            if config.get("environment", {}).get("network_mode") != "public":
                failures.append(f"{case_id} agent environment must use public network mode")
            if config.get("verifier", {}).get("environment", {}).get("network_mode") != "no-network":
                failures.append(f"{case_id} verifier environment must use no-network mode")
            artifacts = config.get("artifacts", [])
            if not isinstance(artifacts, list) or any(path not in artifacts for path in REQUIRED_NERVURE_ARTIFACTS):
                failures.append(f"{case_id} must collect all required Nervure artifacts")
            if any(path.name in {"grader.py", "test_hidden.py"} for path in (task / "environment").rglob("*")):
                failures.append(f"{case_id} hidden verifier leaked into agent environment")
            if case_id != "nervure-eval-poc":
                for relative in ("environment/project", "tests/test_hidden.py", "solution/fix.py"):
                    if not (task / relative).exists():
                        failures.append(f"{case_id} missing medium-dataset fixture {relative}")
        except (OSError, tomllib.TOMLDecodeError) as exc:
            failures.append(f"invalid {case_id} task.toml: {exc}")
    if failures:
        print("Dataset Validation FAIL")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("Dataset Validation PASS")
    return 0


def save_baseline(root: Path, source: Path) -> int:
    payload = read_json(source)
    destination = root / "evals" / "baselines" / f"{source.stem}.json"
    write_json(destination, payload)
    print(destination)
    return 0


def collect_baseline_trials(
    *,
    job_specs: list[str] | None = None,
    full_job: Path | None = None,
) -> list[dict]:
    if full_job is not None:
        return [
            collect_trial(full_job, case_id=case_id, suite="full")
            for case_id in suite_case_ids("full")
        ]
    trials = []
    for spec in job_specs or []:
        case_id, separator, job_dir = spec.partition("=")
        if not separator or not case_id or not job_dir:
            raise SystemExit(f"Invalid --job {spec!r}; expected CASE_ID=JOB_DIR.")
        trials.append(collect_trial(Path(job_dir), case_id=case_id, suite="full"))
    return trials


def compare_metrics(current: Path, baseline: Path) -> int:
    current_data = read_json(current)
    baseline_data = read_json(baseline)
    current_main = current_data.get("main_agent", {})
    baseline_main = baseline_data.get("main_agent", {})
    print("Metric                 Current   Baseline   Delta")
    for key in ("model_calls", "input_tokens", "cache_read_input_tokens", "uncached_input_tokens", "output_tokens", "reasoning_tokens", "duration_ms"):
        value = current_main.get(key, 0)
        old = baseline_main.get(key, 0)
        print(f"{key:24} {value!s:>8} {old!s:>10} {value - old:>7}")
    return 0


def _git_revision(root: Path) -> str:
    return str(git_metadata(root).get("revision") or "working-tree")


def _provider_environment() -> dict[str, str]:
    # The source .env is intentionally excluded from the agent image.  Load its
    # provider settings here and forward only adapter-recognized prefixes;
    # values never enter task files or artifacts.  The printed command redacts
    # credentials.
    prefixes = (
        "NERVURE_",
        "ONECODE_",
        "OPENAI_",
        "ANTHROPIC_",
        "CUSTOM_",
        "MIMO_",
        "DEEPSEEK_",
    )
    values = {
        key: value
        for key, value in dotenv_values(".env", interpolate=False).items()
        if value is not None
    }
    values.update(os.environ)
    active_provider = values.get("NERVURE_PROVIDER_ID") or values.get("ONECODE_PROVIDER_ID") or ""
    if active_provider.strip().lower() == "custom":
        # Some legacy configurations use the Anthropic-style names
        # for an OpenAI-compatible custom gateway.  The normal custom adapter
        # resolves CUSTOM_*; derive only the agent-local equivalents so the
        # source .env can remain outside the Harbor container.
        aliases = (
            ("CUSTOM_BASE_URL", "ANTHROPIC_BASE_URL"),
            ("CUSTOM_API_KEY", "ANTHROPIC_API_KEY"),
            ("CUSTOM_API_KEY", "ANTHROPIC_AUTH_TOKEN"),
            ("CUSTOM_MODEL", "ANTHROPIC_MODEL"),
        )
        for target, source in aliases:
            if not values.get(target) and values.get(source):
                values[target] = values[source]
    return {
        key: value
        for key, value in values.items()
        if key.startswith(prefixes)
        and key not in {"NERVURE_CONFIG_PATH", "ONECODE_CONFIG_PATH"}
    }


def _redact_command(command: list[str]) -> list[str]:
    result = list(command)
    for index, value in enumerate(result):
        if "=" in value and value.split("=", 1)[0].upper().endswith(("KEY", "TOKEN", "PASSWORD", "SECRET")):
            result[index] = value.split("=", 1)[0] + "=[redacted]"
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nervure-eval")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("case_id")
    run_full = sub.add_parser("run-full")
    run_full.add_argument(
        "--n-concurrent",
        type=int,
        default=DEFAULT_FULL_SUITE_CONCURRENCY,
        help="Maximum Harbor trials to run concurrently (default: 12).",
    )
    random_cmd = sub.add_parser("random")
    random_cmd.add_argument("--count", type=int, default=1)
    random_cmd.add_argument("--seed", type=int)
    random_cmd.add_argument("--project")
    suite = sub.add_parser("suite")
    suite.add_argument("name", choices=("smoke", "bugfix", "performance", "full"))
    sub.add_parser("validate-dataset")
    baseline = sub.add_parser("baseline")
    baseline_sub = baseline.add_subparsers(dest="baseline_command", required=True)
    save = baseline_sub.add_parser("save")
    save.add_argument("metrics_json", type=Path)
    collect_cmd = baseline_sub.add_parser("collect")
    collect_cmd.add_argument("--label", default="Nervure V0 baseline")
    collect_cmd.add_argument("--dataset-version", default="0.1.0")
    collect_cmd.add_argument("--output", required=True, type=Path)
    collect_source = collect_cmd.add_mutually_exclusive_group(required=True)
    collect_source.add_argument(
        "--job",
        action="append",
        metavar="CASE_ID=JOB_DIR",
        help="Completed Harbor job directory for one case; repeat once per case.",
    )
    collect_source.add_argument(
        "--full-job",
        type=Path,
        help="One completed run-full Harbor job containing all 12 medium trials.",
    )
    compare = sub.add_parser("compare")
    compare.add_argument("current", type=Path)
    compare.add_argument("--baseline", required=True, type=Path)
    args = parser.parse_args(argv)
    root = repository_root()
    if args.command == "run":
        return run_case(root, args.case_id)
    if args.command == "run-full":
        if args.n_concurrent < 1:
            parser.error("--n-concurrent must be at least 1")
        return run_full_suite(root, n_concurrent=args.n_concurrent)
    if args.command == "random":
        ids = select_case_ids(count=args.count, seed=args.seed, project=args.project)
        print("\n".join(ids))
        return 0
    if args.command == "suite":
        print("\n".join(suite_case_ids(args.name)))
        return 0
    if args.command == "validate-dataset":
        return validate_dataset(root)
    if args.command == "baseline" and args.baseline_command == "save":
        return save_baseline(root, args.metrics_json)
    if args.command == "baseline" and args.baseline_command == "collect":
        trials = collect_baseline_trials(
            job_specs=args.job,
            full_job=args.full_job,
        )
        baseline = build_baseline(
            trials,
            repository_root=root,
            dataset_version=args.dataset_version,
            label=args.label,
        )
        json_path, markdown_path = write_baseline(baseline, args.output)
        print(json_path)
        print(markdown_path)
        return 0
    if args.command == "compare":
        return compare_metrics(args.current, args.baseline)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
