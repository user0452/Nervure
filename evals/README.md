# Nervure Eval

Nervure uses Harbor as the runner rather than reimplementing a trial/job engine.
The integration targets Harbor `v0.21.0` (`64afbbcb62165950301e1a6407c729aa26d844ff`).
Harbor's `BaseInstalledAgent` runs the current checkout inside the agent
environment; the task workspace is `/workspace`, while Harbor's logs are under
`/logs/agent`.  A separate verifier environment is declared in the PoC task, so
the hidden grader is not copied into the agent image.

## Install

Harbor is optional and is not imported by the normal Nervure CLI:

    python -m pip install -e ".[eval]"

The Harbor release currently requires Python 3.12 or newer.  On Python 3.11,
normal Nervure remains usable, while eval commands report that Harbor is not
available.

## Harbor v0.16.1 -> v0.21.0 migration

The integration is pinned to one stable API target, `v0.21.0`; it does not
carry a compatibility branch for `v0.16.1`.  The relevant upstream changes
are:

- the custom installed-agent contract remains `install(environment)` plus
  `run(instruction, environment, context)`, so `NervureAgent` keeps that path;
- task configuration is now schema `1.4`, with package version metadata under
  `[task].version`;
- network policy names are `public`, `no-network`, and `allowlist`;
- unified `--agent` accepts a custom import path, while `--agent-kwarg` and
  `--agent-env` remain the runtime injection flags;
- separate verifier images are built from `tests/`, and only declared
  artifacts (plus Harbor's publish directory) cross the sandbox boundary;
- the official ATIF validator remains
  `harbor.utils.trajectory_validator.TrajectoryValidator`.

The PoC task uses `schema_version = "1.4"`, public agent networking for
dependency installation, and a no-network separate verifier.

## Commands

Run the offline selection and fixture checks first:

    python -m evals.cli random --count 4 --seed 42
    python -m evals.cli suite smoke
    python -m evals.cli validate-dataset

The proof-of-concept Harbor trial is deliberately explicit and is not run by
tests or setup commands:

    nervure-eval run nervure-eval-poc

The wrapper passes the current repository root and Git revision to Harbor.  It
forwards provider configuration through Harbor's agent environment, never into
task files, source fixtures, ATIF, or metrics artifacts.  Agent installation uses
`evals/runtime-constraints.txt` so benchmark dependency resolution is stable,
keeps the current MCP integration on the v1 API line, and retries the complete
pip install up to three times for transient package-index failures.  The installed
agent sets `NERVURE_TRACE_LEVEL=debug` and writes raw trace, ATIF, metrics,
validation, and git diff artifacts under the Harbor trial.

## Medium coding dataset

The checked-in medium dataset is the canonical source for future benchmark
runs.  It contains twelve Harbor tasks across `mini_xiangqi`, `mini_vcs`,
`route_lab`, and `taskflow`: eight bug fixes and four deterministic performance
tasks.  Its source archive is `nervure-medium-coding.zip`, SHA-256
`8f07d3cb137ed68848344f3dfecc3922c8178e3cbb65aefd1f122494c2d9ede1`.

The fixtures live under `evals/tasks/<case_id>/`; each keeps its hidden tests
inside `tests/`, outside the Agent's `environment/` Docker build context.  Use
`nervure-eval run <case_id>` for one case, or run the complete medium suite as
one Harbor job with Harbor-native twelve-way scheduling:

    nervure-eval run-full

`run-full` points Harbor at `evals/tasks`, excludes the separate
`nervure-eval-poc` fixture, and defaults to `--n-concurrent 12`.  Override the
limit with `nervure-eval run-full --n-concurrent <N>` when local Docker or the
model endpoint cannot sustain twelve simultaneous trials.  A completed full job
can be collected directly without repeating the same path twelve times:

    nervure-eval baseline collect --full-job <JOB_DIR> --output <BASELINE_PATH>

The imported source task TOML files have one Nervure-specific overlay: their
`artifacts` declarations also collect trace, ATIF, metrics, validation, report
and git-diff evidence.  The benchmark code, instruction, visible tests, hidden
verifier and Oracle solution remain byte-identical to the source archive.
`validate-dataset` performs structural validation only and never calls a model
or starts Harbor.

## Trace and metrics

`evals.trace_atif.NervureTraceToATIFConverter` maps the existing Nervure
`trace.jsonl` to ATIF-v1.7 and calls Harbor's official
`TrajectoryValidator` when Harbor is installed.  If Harbor is absent, the raw
trace is still preserved and validation is reported as unavailable rather than
silently treating JSON parsing as validation.

`evals.metrics.NervureTraceAnalyzer` emits deterministic Nervure-specific
metrics for model calls, token/cache usage, tools, selector, LTM extraction,
compact, subagents, context, and HITL evidence.  It never invents hidden
reasoning and does not mark `hitl_possible_auto_continue` without objective
evidence.

For Subagent evaluation, `main_agent`, `child_agent`, and `total` are separate
usage aggregates.  Child calls are identified from the real
`subagent_start`/`subagent_completed`/`subagent_error` events plus their span
lineage, rather than by the shared recorder session ID.  The `subagent` section
also records agent type, fork/non-fork, read-only, completion/error counts,
duration, child model usage, and child tool errors.  Legacy event names are
accepted only for trace-reading compatibility.

The provider-visible `agent` tool is ON by default.  Set
`NERVURE_DISABLE_SUBAGENT=1` (or the explicit alias
`NERVURE_DISABLE_AGENT_TOOL=1`) for an OFF trial.  OFF skips registration, so
the tool is absent from the provider schema and dynamic prompt; it is not a
visible tool that is later denied.  The Harbor wrapper forwards the switch into
the headless runtime.

For repository-understanding analysis, one tool call is identified by its
unique `tool_call_id`; `tool_preflight`, `tool_execution`, and `tool_result`
records are one lifecycle and must not be added as three calls. The V1
evidence package is under `docs/evidence/`; it records the Full Compact cache
validation, the four-trial repo_map direction-only sample, and the four-trial
symbol_search exact-probe adoption result with their source paths and limits.
