# Nervure Harness Expansion Report

## Added capabilities

- Project instruction entrypoint at `.nervure/instructions.md`, sorted rules, token budget, and trace metadata.
- `SkillRegistry` for validated skill discovery/search/load, alongside the existing inline/fork skill tool.
- `AgentProfile` declarations for Explore, Implement, and Review child agents.
- Prioritized, enabled/disabled, exception-isolated tool lifecycle hooks.
- Lexical `ToolDiscovery` for lazy provider-schema selection with deterministic fallback and always-visible safety tools.
- File-only mutation checkpoints with explicit-confirmation restore.

## Architecture boundaries

The main loop remains unchanged as an orchestrator. Prompt instruction rendering stays in the context/prompt layer; skills and profiles use the existing subagent/tool contracts; hooks and checkpoints are installed at executor lifecycle boundaries. Discovery filters provider visibility only and does not weaken direct execution permissions.

## Verification

Verification completed in this existing dirty worktree:

- `uv run python -m pytest tests/test_harness_expansion.py ... -q`: **71 passed**.
- `uv run python -m pytest tests/test_import_boundaries.py -q`: **4 passed**.
- `uv run python -m pytest tests -q`: **758 passed in 18.05s**.
- `uv run python -m compileall -q core services infrastructure tools evals ui`: passed.
- `git diff --check`: passed (Git emitted only pre-existing CRLF conversion warnings for the already-dirty worktree).

## Trace examples

`project_instructions_loaded` records source paths, estimated token count, and truncation. `agent_profile_selected` records profile/purpose. `hook_error` records isolated extension failures. `checkpoint_created` records the checkpoint id and affected-file count.

## Current limits

Instruction token estimation uses a deterministic four-character approximation. Tool discovery is lexical rather than embedding-backed. Checkpoints cover direct file targets only; shell-wide side effects and Git history are intentionally out of scope. Profile model preference is retained as declarative metadata because the current provider client is configured per runtime.

## Next directions

Provider-tokenizer integration, semantic tool retrieval, and UI commands for listing/restoring checkpoints can be added without changing the core loop. No benchmark, planner, multi-agent manager, or memory redesign is included in this increment.
