# Build the Nervure Harbor Evaluation Integration

This living plan follows `PLANS.md`.  The user-visible goal is one safe command
that evaluates the current Nervure checkout in Harbor's isolated task and
separate verifier environments, while retaining raw trace, ATIF, metrics and
diff artifacts for later comparison.

## Progress

- [x] (2026-08-17) Research Harbor stable `v0.21.0`, custom installed-agent API, task layout, separate verifier transfer, and official ATIF validator.
- [x] (2026-08-17) Add optional Harbor boundary, Nervure installed-agent adapter, current-revision upload, and headless runtime entry.
- [x] (2026-08-17) Add PoC calculator task with visible tests, separate hidden verifier, and oracle fixture.
- [x] (2026-08-17) Add trace-to-ATIF conversion, official-validator hook, deterministic Nervure metrics, case registry, selection and CLI wrapper.
- [x] (2026-08-17) Re-audit against Harbor `v0.21.0`, migrate the optional dependency and PoC task schema/network modes, and add version-boundary tests.
- [x] (2026-08-17) Import the canonical `nervure-medium-coding` dataset: twelve non-PoC fixtures spanning four medium Python projects, their separate hidden verifiers, and Oracle fixes.
- [x] (2026-08-17) Validate all twelve imported fixtures offline, validate the Nervure task schema/artifact contract, and build all twelve agent and verifier Docker images without a model call.
- [x] (2026-08-17) Run one authorized real Harbor Nervure PoC trial: separate verifier PASS, reward `1.0`, trace/ATIF/metrics/diff artifacts collected, and official ATIF validation PASS.

## Surprises & Discoveries

- Harbor `v0.21.0` is the current stable tag whose commit resolves to `64afbbcb62165950301e1a6407c729aa26d844ff`; its current API uses `BaseInstalledAgent.install()` and `run(instruction, environment, context)`.
- Harbor's separate verifier receives declared artifacts, not `/logs/agent` implicitly.  The PoC therefore declares `/workspace` and selected log artifacts explicitly.
- Between v0.16.1 and v0.21.0, the relevant integration changes are task schema `1.4` and `[task].version`, canonical `public/no-network/allowlist` network modes, unified custom-agent import through `--agent`, and the newer artifact/trajectory handling.  The installed-agent method contract used by Nervure remains stable.
- Nervure's provider configuration originally read only dotenv files.  The headless adapter needs a narrow `allow_process_env=True` path so Harbor can inject provider variables without writing secrets into the task workspace.
- The source medium dataset declared only `/workspace` as an artifact.  That is enough for a generic task, but it would omit Nervure's trace and metrics evidence, so each imported task receives a task-TOML artifact overlay while its benchmark code and verifier remain unchanged.

## Decision Log

- Decision: Use Harbor `BaseInstalledAgent`, not an external agent. Rationale: Nervure's existing tools and AgentLoop must run inside Harbor's task workspace; the installed adapter can upload the current working tree and invoke the existing composition root.
- Decision: Keep Harbor optional. Rationale: normal Nervure CLI imports must not fail when Harbor/Docker is unavailable.
- Decision: Implement the PoC before medium projects. Rationale: the task explicitly prohibits spending model calls before the full harness path is proven.
- Decision: Import the user-supplied `nervure-medium-coding.zip` as the canonical medium dataset rather than authoring replacement fixtures. Rationale: the archive already contains validated task fixtures, hidden verifiers and Oracle fixes targeted at Harbor v0.21.0.
- Decision: Do not run medium-case Oracle, NOP, or real-agent trials during integration. Rationale: the user requested that only pre-evaluation work be completed; future benchmark execution requires an explicit authorization.

## Outcomes & Retrospective

The Harbor integration and the canonical twelve-case medium dataset are now
ready for explicitly authorized evaluation.  The pre-evaluation evidence is:
the archive's own offline validator passed all twelve tasks, Nervure's
structural validator passed, and all twelve agent images plus all twelve
separate-verifier images built successfully.  No medium-case Harbor trial,
Oracle/NOP run, or provider request has been made during dataset integration.
