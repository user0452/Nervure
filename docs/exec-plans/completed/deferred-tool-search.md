# Replace Prompt Filtering with Deferred Tool Search

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds. It follows the repository requirements in `PLANS.md`.

## Purpose / Big Picture

Nervure currently changes the model-visible tool list by lexically matching every user prompt. After this change, small toolsets will behave like an ordinary coding agent: every allowed tool schema is sent directly to the provider. When the available toolset is large or its schemas are too large, the model will instead receive the six core coding primitives, enabled `agent`, and one `tool_search` meta-tool. Calling `tool_search` returns a short ranked result and makes the matched tools' full schemas visible on the next model request, so the model can call them through the normal executor.

## Progress

- [x] (2026-08-29 00:20Z) Read repository architecture, tool-runtime rules, active plans, debt notes, existing discovery code, runtime composition, and the full baseline test suite.
- [x] (2026-08-29 00:21Z) Confirmed baseline: `uv run python -m pytest tests -q` reported `758 passed`.
- [x] (2026-08-29 00:35Z) Replace prompt-driven discovery with deferred exposure, `tool_search`, and a bounded loaded-tool lifecycle.
- [x] (2026-08-29 00:37Z) Preserve deferred composition when a runtime rebuilds for `/connect` and add focused regression tests.
- [x] (2026-08-29 00:45Z) Update tool-runtime documentation, run all required validation, and complete this plan with evidence.

## Surprises & Discoveries

- Observation: The current checkout no longer writes the legacy per-prompt discovery metadata in `core/loop.py`; its remaining behavior was narrowly centralized in `ToolRegistry.visible_descriptors()` and one old behavioral test.
  Evidence: the pre-edit search found the legacy metadata only in the registry and `tests/test_harness_expansion.py`, with no loop call site.
- Observation: The project already owns a conservative provider-visible JSON token estimator.
  Evidence: `services/compaction/token_estimator.py` estimates serialized JSON at `ceil(len(text) / 3)`, which is suitable for a lightweight schema guard without a new budget subsystem.
- Observation: The startup agent-disable switch previously required an explicit rebuild carry-over because `CliRuntime.with_model_config()` reconstructed an agent descriptor from base descriptors.
  Evidence: the new `agent_tool_enabled` runtime composition field keeps a startup-disabled agent absent after the `/connect` rebuild regression.

## Decision Log

- Decision: Keep `ToolDiscovery` as the deterministic metadata ranking component, but remove its query-to-schema-filter API and prompt coupling.
  Rationale: It already matches name, description, and `search_hint` without provider or embedding dependencies. The new registry will invoke it only from `tool_search` against the authority-filtered deferred set.
  Date/Author: 2026-08-29 / Codex
- Decision: Put exposure policy and loaded-tool bookkeeping in `ToolRegistry`, and implement `tool_search` as a normal registered descriptor whose handler delegates to that registry.
  Rationale: `ToolRegistry` already owns provider-visible schemas and permission-aware visibility, while the normal executor retains validation, guard, permission, hooks, tracing, and error-result behavior.
  Date/Author: 2026-08-29 / Codex
- Decision: Default the full-schema threshold to 12,000 estimated tokens and expose both thresholds in a small immutable configuration object.
  Rationale: This is below the existing 12,800-token compact-summary reserve and keeps room for prompt, messages, and output in the documented 128K context architecture, while still allowing normal small toolsets through unchanged.
  Date/Author: 2026-08-29 / Codex

## Outcomes & Retrospective

Delivered small-toolset full exposure and large/token-heavy deferred exposure. `tool_search` is a normal executor-backed tool that uses only allowed descriptor metadata, persists loaded names in the active session, and expands full schemas on the following model snapshot. The implementation deliberately has no embedding retrieval, intent classifier, LLM router, LRU, eviction, or new context framework.

Focused regression evidence: `uv run python -m pytest tests/test_deferred_tool_search.py tests/test_harness_expansion.py tests/test_subagent_eval_controls.py tests/test_cli_connect.py -q` reported `21 passed`. Final validation reported `764 passed` for the complete suite, `4 passed` for import boundaries, and successful `compileall`. `git diff --check` will be run after this plan is archived.

## Context and Orientation

`core/loop.py` is the thin lifecycle orchestrator. It appends user messages, builds provider snapshots, calls the model, and sends actual tool calls to the executor. It must no longer inspect user text to decide visible schemas.

`services/tools/types.py` defines `ToolDescriptor`, the complete definition of one executable tool. `services/tools/registry.py` owns registered descriptors and derives both model schemas and prompt sections after disabled, hidden, denied, and permission-policy visibility rules. `services/tools/executor.py` remains the authority for executing calls: it repeats input validation, guard, permission, hook, and result-policy checks.

In this plan, a *deferred tool* is an allowed descriptor that remains executable in the registry but whose full provider schema is not yet in the current model snapshot. A *loaded tool* is a deferred tool selected through `tool_search` and retained in the current `RuntimeState` session. Deferred exposure changes only what the provider can name; it never grants an otherwise hidden, disabled, or denied tool.

`ui/cli/app.py` constructs the main `ToolRegistry`. `ui/cli/types.py::CliRuntime.with_model_client()` rebuilds the registry when `/connect` changes model/provider. That rebuild must carry the same exposure configuration and loaded-tool state. `services/compaction/token_estimator.py` provides the existing serialized JSON estimate. `tools/` contains concrete coding primitives and will contain the new `tool_search` descriptor.

## Plan of Work

First, replace `services/tools/discovery.py` with a focused lexical ranker returning only matching descriptors and an explicit empty result for a no-match query. It will search lightweight descriptor metadata only: name, description, and optional `search_hint`.

Then extend `services/tools/registry.py` with a small `ToolExposureConfig`: the count threshold defaults to 30, the estimated-schema token threshold defaults to 12,000, the core tool names are `read_file`, `grep`, `glob`, `edit_file`, `write_file`, and `bash`, and one search loads a bounded number of candidates without exceeding the hard schema budget. The registry will first calculate descriptors allowed by existing visibility rules. If their full schemas fit both thresholds it returns all of them. Otherwise it returns core descriptors, enabled `agent` if present, `tool_search`, and descriptors named in the active session's loaded set. It will expose queryable deferred descriptors only after applying the same visibility rules, and it will never return all deferred descriptors on a no-match search.

Add `tools/tool_search/` with a normal descriptor. Its input is a nonempty `query` string. Its handler calls `ToolRegistry.search_and_load(runtime.state, query)`, returns candidate names and descriptions, and reports an ordinary empty result when there are no useful matches. The handler has a read-only session-state classification; because it is executed by `RegistryToolExecutor`, it cannot bypass runtime checks. `ui/cli/app.py` will register it and inject its registry reference through a minimal late-bound callable to avoid construction cycles.

Eliminate the old prompt-filter behavior and test. No `AgentLoop` query write exists in this checkout, so the implementation preserves the loop unchanged. Loaded names live in `RuntimeState.metadata` under a registry-owned key, which is session-scoped and naturally survives a provider rebuild. Pass the main registry's exposure configuration into `CliRuntime` and use it when rebuilding a registry for `/connect`. Preserve the startup-selected `agent` registration decision so a disabled `agent` remains absent after rebuild.

Add focused tests for full small-tool exposure, count and token-driven deferred exposure, core tools, query/load/next-snapshot/execution flow, no-match behavior, denied/hidden/disabled search exclusion, disabled agent behavior, absence of the old prompt state lifecycle, and `/connect` rebuild preservation. Update `docs/design-docs/tool-runtime-architecture.md` and replace the obsolete evidence statement so documentation describes the two modes and automatic next-turn schema expansion rather than prompt filtering.

## Concrete Steps

Run all commands from `C:\Users\刘\Documents\Codex\2026-08-16\nervure`.

1. Add registry and `tool_search` tests, then implement the exposure policy and run the focused tool test command:

       uv run python -m pytest tests/test_deferred_tool_search.py tests/test_tool_registry_and_executor.py -q

   Expect all focused tests to pass. The deferred scenario must show `tool_search` before retrieval, the requested long-tail schema on the following snapshot, and no unrelated long-tail schema.

2. Add the CLI rebuild regression and run its targeted test plus existing CLI coverage:

       uv run python -m pytest tests/test_deferred_tool_search.py tests/test_cli_commands.py tests/test_cli_runtime.py -q

   Expect a model-client rebuild to retain loaded names, disabled tools, permission policy, MCP descriptors, and exposure configuration.

3. Run final repository validation:

       uv run python -m pytest tests -q
       uv run python -m pytest tests/test_import_boundaries.py -q
       uv run python -m compileall core services infrastructure tools ui
       git diff --check

   Expect zero failures, compile output with no syntax errors, and no whitespace errors.

## Validation and Acceptance

The tests will construct thirty or fewer allowed descriptors and assert that every ordinary schema is immediately provider-visible. They will also construct more than thirty descriptors, and a smaller but schema-heavy group, and assert that each enters deferred mode. In deferred mode, the six core primitives, enabled `agent`, and `tool_search` stay visible, while MCP-like long-tail descriptors are absent.

The end-to-end test will issue a normal executor call to `tool_search` with `github pull request`, assert that matching deferred tools are returned and recorded as loaded, build a subsequent context snapshot, then issue a normal tool call to one returned descriptor. A no-match search must return an empty candidate result and leave unrelated descriptors deferred. Hidden, disabled, project-denied, policy-denied, and disabled `agent` descriptors must not be returned by search or reintroduced in schemas. A provider rebuild must leave a previously loaded descriptor available without exposing the whole catalog.

## Idempotence and Recovery

The registry keeps only descriptor names in state metadata; repeated searches union names and never duplicate schemas. Its cap is deterministic and applies only to a successful search. Re-running tests does not write project state beyond temporary pytest paths. If a focused test fails, rerun it by exact node id, inspect the visible-descriptor set, and adjust only the registry policy; do not weaken executor checks or permission visibility to make a schema appear.

## Artifacts and Notes

Baseline evidence before edits:

    uv run python -m pytest tests -q
    758 passed in 16.65s

The default 12,000-token schema threshold is a conservative input guard, not a new context budgeting subsystem. It uses the project’s existing serialized JSON estimate and is configurable per registry construction for tests and future composition.

## Interfaces and Dependencies

`services.tools.registry.ToolExposureConfig` must provide configurable `max_direct_tool_count`, `max_direct_schema_tokens`, `max_loaded_from_search`, `max_loaded_schema_tokens`, and `core_tool_names` fields. `ToolRegistry` must expose an `exposure_config` property, an `is_deferred_mode(state)` query, a `deferred_descriptors(state)` query, and `search_and_load(state, query)` returning a structured result with the ranked candidates loaded by that call.

`tools.tool_search.descriptor()` returns a `ToolDescriptor` named `tool_search` with an input object containing required `query`. Its handler receives the active registry through `ToolRuntime.registry`, which `RegistryToolExecutor` binds while constructing the runtime for an actual call. It returns a normal tool error if that binding is absent. It does not import `core` or inspect prompt text.

`CliRuntime` must retain `tool_exposure_config` and pass it when `with_model_client()` recreates `ToolRegistry`. `ToolRegistry` remains the only schema/prompt projection source, and `RegistryToolExecutor` remains the only execution entrypoint.

Plan revision note (2026-08-29): Created after clean baseline and code orientation to satisfy `PLANS.md` before implementation. Revised after implementation discovery: the stale query writer had already been removed from `AgentLoop`; only registry/test integration required removal.
