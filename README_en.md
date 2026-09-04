# Nervure

[中文](README.md) | English

![Python Version](https://img.shields.io/badge/Python-3.11+-blue.svg?logo=python&logoColor=white) ![License](https://img.shields.io/badge/License-MIT-green.svg)
<p align="center">
  <img src="docs/assets/logo.svg" alt="Logo" width="200" />
</p>

![Demo](docs/assets/demo.gif)

Nervure is a **CodeAgent** built around the engineering philosophy of a **Harness**. It treats the large language model as an executor with tool-calling capabilities, while a stable and controllable engineering framework constrains, organizes, and absorbs its side effects. This allows AI to complete long-horizon coding tasks reliably in real environments without losing control or crossing boundaries.

---

## Core Features

### Main Loop
Nervure revolves around a thin and stable main loop that drives the agent's "think — act — observe" cycle. The loop is responsible only for orchestrating the agent lifecycle: deterministic state transitions handle error recovery, budget control, and session termination, without allowing expanding agent capabilities to contaminate the orchestration layer.

### Extensibility Through Hooks
All non-core capabilities are attached through **Hooks**. Developers can inject deterministic engineering constraints, safety rules, and business logic at key lifecycle points, turning behaviors that prompts alone cannot reliably enforce into code-level guarantees. Hooks remain decoupled from the main loop, so new capabilities do not require modifying the orchestration core.

### Context Engineering
Nervure treats the context sent to the model on every turn as an engineered artifact. Message chains, transcripts, and snapshots are rebuilt dynamically from runtime state; multi-level compaction, long-term memory, `@mention` attachment projection, and a dynamic system prompt work together to keep the model focused on high-information-density context rather than an ever-growing raw conversation history.

### Extensible Tool System
All built-in and external capabilities are exposed through a unified **tool registry**. The registry defines tool execution logic, permission boundaries, and error-feedback contracts. Tools run in a controlled environment, and exceptions are captured and translated into contextual feedback. Built-in tools cover files, commands, retrieval, attachments, background tasks, and subagents; external capabilities can be integrated through **Skills** and **MCP** using the same registration model.

### Interruptible and Recoverable State
Runtime transcripts, tool-call history, and lightweight validation metadata are persisted independently. `/resume` always restores the transcript, but a session is marked `SAFE_RESUME` only when the workspace, Git HEAD, touched-file hashes, project instructions, tool/model configuration, and permission mode still match. Configuration changes produce `STALE_CONTEXT`; workspace changes produce `WORKSPACE_DIVERGED`, clear stale file caches, and require rereading before writes. File mutation tools create checkpoints before execution, and `/undo` restores the latest valid checkpoint for the current session.

### Observability and Analysis
The system records structured runtime **traces** and **error logs**. Decisions, tool calls, and state transitions remain inspectable for debugging, replay, and evaluation.

### Capability Integration
Complex work can be delegated to **Subagents**, long-running operations can move into **Background Tasks**, domain expertise can be packaged as loadable **Skills**, and external tools can connect through **MCP**. These capabilities are modular additions to the loop rather than reasons to inflate the core layer.

### Multi-Provider Model Support
Model providers are isolated in the infrastructure layer. Runtime and business logic depend on provider-neutral protocols, allowing model vendors to be changed without rewriting the agent loop.

### Layered Safety and Permissions
Filesystem sandboxing, permission decisions, and lifecycle hooks cooperate in layers. Dangerous operations are blocked before they reach execution, so the system's safety boundary does not depend on the model "behaving itself."

---

## Quick Start

Nervure requires **Python 3.11 or later** and uses [uv](https://docs.astral.sh/uv/) for dependency management.

### 1. Prepare the environment

```bash
# Sync the virtual environment, including development dependencies
uv sync --dev

# Copy the environment variable template
cp .env.example .env
```

Provider configuration is read from `.env`. If you do not want to configure it immediately, launch the terminal and use `/connect` inside Nervure.

### 2. Launch the terminal

```bash
# On Windows, activate the virtual environment first
.\.venv\Scripts\Activate.ps1

# Start the inline terminal REPL
uv run python -m ui.cli.app
```

Batch mode is also supported. When standard input is not a TTY, Nervure automatically switches to batch mode, for example:

```bash
echo "List the files in the current directory" | uv run python -m ui.cli.app
```

---

## Development Guide

Nervure is a harness-style engineering project. Its target architecture, knowledge map, and working conventions are maintained explicitly as documentation, and both AI agents and developers are expected to follow those documents. In this model, documentation itself acts as an interface; following the documented contracts matters more than following an ad-hoc prompt.

- **Understand the project — read [`AGENTS.md`](AGENTS.md)**: `AGENTS.md` is the entry point for agents entering the repository. It explains where project knowledge lives, the recommended reading order, dependency boundaries, environment setup, and common commands. When you or an AI agent first enters the repository, start with `AGENTS.md`, then continue with `architecture.md` and the relevant files under `docs/design-docs/`.
- **Read the architecture — see [`architecture.md`](architecture.md)**: the root architecture document defines the target runtime structure, logical layers, core abstractions, and dependency direction. Module-level details live under `docs/design-docs/`.
- **Plan complex work — use [`PLANS.md`](PLANS.md)**: complex features and major refactors should use the ExecPlan format documented in `PLANS.md`. Active plans live under `docs/exec-plans/active/` and move to `docs/exec-plans/completed/` when finished.
- **Track debt — use [`tech_debt_tracker_guide.md`](tech_debt_tracker_guide.md)**: known shortcuts and risks are recorded through the project's technical debt tracker. Follow that guide when adding or updating debt entries.
- **Run tests / boundary checks**:
  ```bash
  uv run python -m pytest tests -q
  uv run python -m pytest tests/test_import_boundaries.py -q
  uv run python -m compileall core services infrastructure
  ```

---

## Module Documentation Index

For deeper implementation details, see the design documents below.

**Core orchestration**
- [`core-runtime-architecture.md`](docs/design-docs/core-runtime-architecture.md) — `core/` orchestration layer

**Context management**
- [`context-architecture.md`](docs/design-docs/context-architecture.md) — message chain / transcript / snapshots
- [`compaction-architecture.md`](docs/design-docs/compaction-architecture.md) — compaction and session memory
- [`prompt-architecture.md`](docs/design-docs/prompt-architecture.md) — dynamic system prompt

**Hook system**
- [`hook-architecture.md`](docs/design-docs/hook-architecture.md) — loop extension points

**Memory system**
- [`memory-architecture.md`](docs/design-docs/memory-architecture.md) — long-term and instruction memory

**Attachment system**
- [`attachment-architecture.md`](docs/design-docs/attachment-architecture.md) — `@mention` and attachment projection

**Tool system**
- [`tool-runtime-architecture.md`](docs/design-docs/tool-runtime-architecture.md) — tool runtime
- [`builtin-tools-architecture.md`](docs/design-docs/builtin-tools-architecture.md) — built-in tool responsibilities

**Safety and permissions**
- [`guard-architecture.md`](docs/design-docs/guard-architecture.md) — sandbox and path safety
- [`permission-architecture.md`](docs/design-docs/permission-architecture.md) — permission decisions

**Capability integration**
- [`subagent-architecture.md`](docs/design-docs/subagent-architecture.md) — subagents
- [`skill-architecture.md`](docs/design-docs/skill-architecture.md) — skill system
- [`mcp-architecture.md`](docs/design-docs/mcp-architecture.md) — MCP integration
- [`task-architecture.md`](docs/design-docs/task-architecture.md) — task system
- [`background-task-architecture.md`](docs/design-docs/background-task-architecture.md) — background tasks

**Boundaries and interfaces**
- [`model-provider-architecture.md`](docs/design-docs/model-provider-architecture.md) — model/provider layer
- [`observability-architecture.md`](docs/design-docs/observability-architecture.md) — traces and error logs
- [`cli-architecture.md`](docs/design-docs/cli-architecture.md) — CLI interface

**Cross-cutting conventions**
- [`core-beliefs.md`](docs/design-docs/core-beliefs.md) — design beliefs and anti-patterns
- [`tool-design-guidelines.md`](docs/design-docs/tool-design-guidelines.md) — guidelines for adding tools

---

## License

MIT
