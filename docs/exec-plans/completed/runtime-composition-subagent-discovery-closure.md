# Close runtime composition, child context, and tool discovery loops

This ExecPlan is a living document and must be maintained according to `PLANS.md`. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` are updated as implementation proceeds.

## Purpose / Big Picture

After this work, changing provider through `/connect` will preserve the same provider-visible tool capability composition that startup created, child agents will have the same bounded context safety as the parent without touching the parent transcript, and the existing lexical Tool Discovery will participate automatically in ordinary top-level prompts. These are closure changes to existing runtime behavior; they do not add Tool RAG, embeddings, a router, new orchestration, or UI features.

## Progress

- [x] (2026-08-28) Confirmed a clean `main` worktree at `d1168cd` and read the repository architecture, relevant design documents, active plan, technical-debt tracker, and current implementations.
- [x] (2026-08-28) Confirmed that `build_runtime()` creates ToolDiscovery and conditionally registers `agent`, while `CliRuntime.with_model_config()` recreates a registry without discovery and always registers `agent`.
- [x] (2026-08-28) Confirmed that `SubagentRunner` creates ephemeral child stores but gives child `ContextEngine` and `AgentLoop` no compaction service or final budget manager.
- [x] (2026-08-28) Confirmed that `ToolRegistry` already consumes `state.metadata["tool_discovery_query"]`, but only tests manually populate it; `AgentLoop.stream()` does not manage its lifecycle.
- [x] (2026-08-28) Centralized provider-visible registry composition and reused it from startup and model reconfiguration, including startup-selected agent enablement and ToolDiscovery.
- [x] (2026-08-28) Added child-local compaction and final-budget protection with one bounded context-limit recovery and deterministic terminal failure.
- [x] (2026-08-28) Refreshed Tool Discovery query for each top-level `stream(prompt)` and cleared it for seeded `continue_stream()` flows.
- [x] (2026-08-28) Added focused regressions and updated the architecture documents; the combined focused group passes with 48 tests.
- [x] (2026-08-28) Final validation passed: 776 full-suite tests, 4 import-boundary tests, compileall for `core services infrastructure ui`, and `git diff --check`.
- [x] (2026-08-28) Recorded final outcomes and moved this plan to `docs/exec-plans/completed/`; commit and push are the remaining delivery operations.

## Surprises & Discoveries

- Observation: `base_descriptors` intentionally excludes the `agent` descriptor because that descriptor closes over the current `SubagentRunner`; therefore preserving the startup registry object is not enough when the model client changes. The composition policy must be preserved while rebuilding the runner-bound descriptor.
  Evidence: `ui/cli/app.py` registers `agent` after constructing `SubagentRunner`, while `ui/cli/types.py::with_model_config()` constructs a new runner and descriptor.

- Observation: discovery affects both `tool_schemas()` and prompt sections through `visible_descriptors()`, while executor lookup continues to use the complete descriptor registry. This already matches the required authority boundary.
  Evidence: `services/tools/registry.py` filters only `visible_descriptors`; `get()` remains unfiltered.

- Observation: child stores use `InMemoryTranscriptStore`, so destructive compaction can safely replace the child active chain without creating a resumable session, provided the compaction service is bound only to that child store.
  Evidence: `MessageStore.ephemeral()` constructs an in-memory transcript store and existing fork tests verify no child session file is created.

- Observation: the existing lexical discovery tokenizer intentionally falls back to the full visible set when a query has no lexical match, including prompts it cannot tokenize usefully. This preserves correctness for Chinese prompts without introducing the prohibited translation or routing subsystem.
  Evidence: the focused lifecycle test proves both relevant reduction and unrelated-query fallback through the real `ToolRegistry` provider schema path.

- Observation: one existing evaluation-control test imports `_subagent_descriptors` directly even though it is private, so deleting it breaks test collection.
  Evidence: the first full-suite collection failed in `tests/test_subagent_eval_controls.py`; the name is now retained as a thin wrapper over the shared runtime-bound agent composition function.

## Decision Log

- Decision: Introduce one small UI composition value/helper that owns base descriptors, discovery, and the conditional `agent` rule; keep descriptor creation that depends on workspace services in `build_runtime()`.
  Rationale: Startup and `/connect` need identical provider-visible composition, but moving every builtin constructor into `CliRuntime.with_model_config()` would increase duplication and coupling.
  Date/Author: 2026-08-28 / Codex

- Decision: Reuse `ContextCompactionService`, `ContextEngine` final budget validation, and `AgentLoop` reactive compact retry for child runtimes, with a new service instance bound exclusively to each ephemeral child store.
  Rationale: This preserves the existing compaction architecture and actual parent compaction configuration, and automatically enforces one retry through `RuntimeState.has_attempted_reactive_compact` without mutating the parent store.
  Date/Author: 2026-08-28 / Codex

- Decision: Set `tool_discovery_query` only at the top-level `AgentLoop.stream(prompt)` entry and remove it at `continue_stream()` entry.
  Rationale: A user prompt should control every model call within that user turn, while seeded child/internal flows must not inherit a stale query from a prior top-level turn.
  Date/Author: 2026-08-28 / Codex

## Outcomes & Retrospective

Startup and `/connect` now reuse one startup-selected provider-visible tool composition, including builtin and MCP descriptors, environment-controlled experimental tools, ToolDiscovery, permission policy, and conditional runtime-bound `agent`. Reconfiguration replaces the model-bound subagent runner and descriptor without silently adding disabled capabilities or dropping discovery.

Ordinary child and skill runtimes now own their compaction service, use the actual main-runtime compaction configuration, validate the fully projected child request, and receive the existing one-shot reactive context recovery. Both successful recovery and the stable `subagent_context_limit` terminal result are covered, while parent messages and `CurrentModelContext` remain unchanged.

Each ordinary top-level `stream(prompt)` now refreshes lexical discovery from that prompt, and `continue_stream()` clears the key before seeded child/internal work. Relevant English queries reduce provider schemas; unmatched and Chinese queries retain the safe full-set fallback. No embeddings, translation layer, router, new permission path, or recursive agent capability was introduced.

Validation completed with:

    uv run python -m pytest tests/test_cli_runtime_composition.py tests/test_cli_connect.py tests/test_harness_expansion.py tests/test_loop.py tests/test_subagent_runner.py tests/test_context_recovery.py tests/test_agent_tool.py -q
    48 passed in 1.72s

    uv run python -m pytest tests -q
    776 passed in 14.22s

    uv run python -m pytest tests/test_import_boundaries.py -q
    4 passed in 0.05s

    uv run python -m compileall core services infrastructure ui
    exit 0

    git diff --check
    exit 0

The first full-suite collection exposed the retained private-test import `_subagent_descriptors`; it was restored as a thin compatibility wrapper over the shared composition function, after which the complete suite passed. Live provider and MCP calls were deliberately not run because all changed behavior is provider-neutral and the composition smoke mocks only those external boundaries.

## Context and Orientation

`ui/cli/app.py::build_runtime()` creates service-dependent builtin descriptors, MCP descriptors, ToolDiscovery, the main ToolRegistry, SubagentRunner, executor, context engine, and agent loop. `ui/cli/types.py::CliRuntime.with_model_config()` is used after `/connect`; it must replace model-dependent objects without changing the capability policy selected at startup.

`services/tools/registry.py` owns the executable descriptor map and derives provider-visible schemas and prompt sections. Its optional `ToolDiscovery` performs deterministic lexical selection, keeps metadata-marked core tools visible, and falls back to the full visible set when no relevant descriptor matches. Discovery does not change executor authority.

`services/subagents/runner.py` constructs both ordinary/fork children and skill children. A fork child seeds a deep copy of the last parent `ContextSnapshot` messages plus the child directive, but its store is ephemeral. Today its `ContextEngine` has no context preparer/final budget manager and its `AgentLoop` has no compaction service, so an inherited large snapshot can reach the provider unguarded.

`core/loop.py::AgentLoop.stream(prompt)` is the only public entry that receives a new top-level user prompt. `continue_stream()` is used by subagents after their messages are already seeded. The metadata query lifecycle belongs at these two entry boundaries, not in ToolRegistry or the CLI renderer.

## Plan of Work

First add a small runtime tool-composition object under `ui/cli/` that stores the startup-selected base descriptors, lexical ToolDiscovery instance, and whether the `agent` tool was enabled. It will build a ToolRegistry from the current PermissionPolicy and current SubagentRunner. `build_runtime()` and `with_model_config()` will both call it. `CliRuntime` will retain this composition value so environment switches, MCP descriptors, experimental descriptors, and discovery cannot drift during `/connect`.

Next give every child runtime its own `ContextCompactionService`, bound to the child `MessageStore`, shared model client, and trace recorder. Child `ContextEngine` will use it as context preparer and final budget manager; child `AgentLoop` will use it for the single existing reactive retry. The parent store is never passed to this service. Fork prompt and schemas remain inherited by their existing providers, read-only and permission semantics stay unchanged, and failure is collapsed by `SubagentRunner` into a deterministic structured context-limit result.

Then update `AgentLoop.stream(prompt)` to replace `state.metadata["tool_discovery_query"]` with the current prompt before context building. Update `continue_stream()` to remove that key before child/internal context building. Keep ToolDiscovery lexical and deterministic; only add minimal normalization if focused Chinese cases prove the existing whitespace tokenizer unusable and a small direct mapping can be justified without creating routing logic.

Finally update only the architecture documents whose runtime semantics changed, add focused tests to existing test files, run the required focused and full validation, record exact results in this plan, move it to completed, commit, push to `origin/main`, and verify local and remote hashes.

## Concrete Steps

All commands run from `C:\Users\刘\Documents\Codex\2026-08-28\bu\Nervure`.

Focused validation during implementation:

    uv run python -m pytest tests/test_cli_runtime_composition.py tests/test_cli_connect.py tests/test_harness_expansion.py tests/test_loop.py tests/test_subagent_runner.py tests/test_context_recovery.py -q

Final validation:

    uv run python -m pytest tests -q
    uv run python -m pytest tests/test_import_boundaries.py -q
    uv run python -m compileall core services infrastructure ui
    git diff --check

Expected focused tests prove startup-to-reconfiguration tool-name equivalence, disabled `agent`, preserved discovery, top-level query refresh/fallback, child-local reduction/retry, unchanged parent messages, and deterministic failure after one retry. The full suite must pass without boundary violations.

## Validation and Acceptance

The startup-to-`with_model_config()` smoke test must construct the real runtime composition while mocking only provider and MCP external boundaries. Provider-visible tool names before and after reconfiguration must match for the same state and prompt. A startup with the agent switch disabled must still omit `agent` after reconfiguration. A relevant prompt must reduce schemas through discovery both before and after reconfiguration, while an unrelated prompt falls back to the normal visible set.

A large fork snapshot must trigger a compact request against the child model and then produce a smaller normal child request. The parent `MessageStore` and parent `CurrentModelContext` must remain unchanged. A provider-raised `context_limit_exceeded` may cause exactly one child-local reactive compact and one retry; a second context-limit failure must return a `SubagentResult` with a stable context-limit error code and must not loop.

Ordinary `AgentLoop.stream("...")` must set the discovery query before its first context build. A later `stream()` call must replace it. `continue_stream()` must clear it so seeded children/internal continuations cannot inherit the previous user query.

## Idempotence and Recovery

All code edits are local and additive before duplicated composition is removed. Tests use temporary workspaces and in-memory child transcript stores. No user session, provider credential, MCP server, or remote service is modified by validation. If a focused test fails, fix the specific boundary before running the final full suite; do not reset or clean the checkout.

## Artifacts and Notes

Current verified repository state before edits:

    main d1168cd fix: close runtime follow-up gaps
    worktree clean

No live provider or MCP validation is planned because the acceptance behavior can be proven at the composition and provider-neutral model boundaries without credentials or external side effects.

## Interfaces and Dependencies

The shared composition helper will expose a small method that receives the current `SubagentRunner`, `BackgroundTaskManager`, and `PermissionPolicy` and returns a `ToolRegistry`. `CliRuntime` will retain the helper/value as session-stable composition state. No new third-party dependencies are allowed.

Child composition will instantiate the existing `ContextCompactionService` with the ephemeral child store, current model client, and trace recorder, then pass the same instance to `ContextEngine(final_context_budget_manager=...)` and `AgentLoop(compaction_service=...)`. No parent store or parent compaction service may be referenced.

Revision note (2026-08-28): Initial plan created after source audit; decisions reflect the observed startup/reconfiguration drift, existing ephemeral child store, and manual-only ToolDiscovery query path. Completed after focused and full validation; the only validation-time adjustment retained the existing private evaluation-test import through the new shared composition boundary.
