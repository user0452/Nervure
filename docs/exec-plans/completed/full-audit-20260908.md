# Audit the runtime, validate live modules, and publish evidence

This ExecPlan is maintained under `PLANS.md`. All work is in
`D:/all-python/nervure`; the source copy at `C:/CodexWork/nervure` is untouched.

## Purpose / Big Picture


The user should receive a bug-fixed runtime without product scope changes,
reproducible offline and limited real-provider checks, and a Chinese report
comparing every major module with established coding agents. The report must
distinguish tested behavior from design claims and proposed internship work.

## Progress


- [x] (2026-09-07 UTC) Confirm clean main at 5ae6213 and baseline: 845 tests passed.
- [x] (2026-09-07 UTC) Read architecture, relevant design/debt and evaluation code; confirm provider/model and key presence without exposing secrets.
- [x] (2026-09-07 UTC) Add deterministic regressions and fix persistence, task paths, provider completion, MCP retry, headless status, compaction tail, config limits and plan atomic writes.
- [x] (2026-09-07 UTC) Run isolated live checks; all seven distinct scenarios have successful final checks across recorded runs. Initial failures and harness corrections are retained.
- [x] (2026-09-07 UTC) Review module entrypoints/tests and current official repositories, including OpenHands SDK versus Agent Canvas ownership.
- [x] (2026-09-07 UTC) Run final suite: 895 passed in 21.15s; compileall, dataset validation and diff whitespace checks passed.
- [x] (2026-09-07 UTC) Complete staged path/configured-secret checks and commit tested runtime changes as bc43c1b7724efebcd6c9d6c77c28b8ef0a213321.
- [x] (2026-09-07 UTC) Complete Chinese report with 24 module topics and official source snapshots; verify all 93 local references and sanitized evidence against all six raw live summaries.
- [x] (2026-09-07 UTC) Push main and verify GitHub HEAD equals local 0c3e8f380cc0c70e0dd8f68d67c9a58fec0693d0; working tree clean and repository still private. Archive this completed plan.

## Surprises & Discoveries


The baseline is green but `JsonlTranscriptStore.flush` clears queued lines
before opening a dynamically selected session path outside its lock.
`evals/headless.py` duplicates shutdown rather than invoking `CliRuntime.close`.
Both were reproduced and fixed. The same flush race affected trace/error sinks.
Live compaction exposed an unbounded rewind to the user anchor: a 40,628-token
estimated context grew to 41,325 after summarization. After retaining the anchor
without the summarized middle, the same fixture shrank to 10,755.
The checkout has no `.github` directory. Historical evaluation reports are
not evidence that this revision has passed those same live workloads.

The configured model alias `mimo-v2.5-pro-1m` returned server errors twice and
was absent from authenticated model discovery. The returned model list contained
`mimo-v2.5` and `mimo-v2.5-pro`. An explicit test-only override selected the
latter without changing `.env`. Early exact-echo probes failed semantic assertions;
one harness misuse of the async executor was corrected. No failed run was deleted.

The built-in Ollama definition allowed an empty API key but both runtime clients
still required one, and chat/probe endpoints disagreed with the selected
Chat Completions protocol. Three transport contract regressions now pass after
aligning the endpoint, headers and native model-list parser. No live Ollama
server was run.

## Decision Log


Decision: use synthetic workspaces and only the existing configured model.
Rationale: live requests must not disclose user sessions, personal memory or
credentials. One million tokens is a ceiling, not a target to consume.
Date/Author: 2026-09-07 UTC, Codex.

Decision: no feature expansion, history rewrite, original-project edits or
public visibility changes. Use ordinary main commits.
Rationale: the user requested bug fixes, real checks and a report.
Date/Author: 2026-09-07 UTC, Codex.

Decision: only automatically repeat dispatched MCP calls with read-only or
idempotent annotations; return unknown execution outcome otherwise.
Rationale: a reply can be lost after the server has already applied a write.
Date/Author: 2026-09-07 UTC, Codex.

Decision: retain a single user anchor plus the recent tool-pair-safe tail.
Rationale: copying every intervening step defeats context compaction on one
long coding turn; its existing summary already covers the omitted middle.
Date/Author: 2026-09-07 UTC, Codex.

## Outcomes & Retrospective


Nine defect groups are fixed without changing product scope. The final offline
suite is 895 passed in 21.15s, up from the 845-test baseline. Compilation,
dataset validation and diff checks pass. The report at
`docs/evidence/full-project-audit-20260907.md` covers 24 module topics,
competitor source snapshots, remaining risks, contribution boundaries and
a half-day through one-week evidence roadmap.

The six live runs made 24 model calls. Twenty-two calls reported 65,526 input
and 5,294 output tokens, totaling 70,820 reported tokens; two failed calls
have unknown usage. Cache-read input is already included in input. Cumulative
conservative reservations are 635,474 against the 1,000,000 ceiling. Seven
distinct scenarios have successful records, but the 14 case attempts include
seven failures and span evolving working trees. This is not a benchmark score
or a final-revision all-pass live run. Sanitized aggregates are stored in
`docs/evidence/live-module-audit-20260907.json`; private raw outputs remain
ignored. Runtime and evidence publication was verified at
0c3e8f380cc0c70e0dd8f68d67c9a58fec0693d0; the subsequent plan archival
commit changes documentation only.

## Context and Orientation


`core/loop.py` coordinates provider calls, tool execution and transitions.
`services/context/transcript.py` buffers JSONL records, where one JSON object
per line stores a session message. `ui/cli/types.py::CliRuntime.close` drains
session hooks, persists recovery state, flushes logs and closes MCP connections.
`evals/headless.py` is the noninteractive Harbor entry point; Harbor is an
optional container-based task evaluator, not a host-machine sandbox.
`infrastructure/providers/chat_completions.py` converts internal snapshots to
Chat Completions streams. `services/memory` and `services/compaction` invoke
model clients or restricted child runtimes for memory and summarization.

## Plan of Work


First establish focused regressions for persistence and shutdown, then inspect
provider streaming, tool boundaries, tasks and background lifecycles. Fix only
reproducible defects using existing abstractions. Add live test support under
`evals/` with explicit opt-in, a global conservative token ledger, bounded output
and call count, synthetic fixtures, and sanitized summaries. Actual input and
output usage must not double count repeated cumulative streaming usage events.
Record failures and unsupported behaviors as evidence rather than quietly
retrying indefinitely. Review official Aider, OpenHands, SWE-agent/mini-swe-agent,
Cline and OpenCode sources, then produce
`docs/evidence/full-project-audit-20260907.md`.

## Concrete Steps


Run commands from `D:/all-python/nervure`:

    .\.venv\Scripts\python.exe -m pytest tests -q
    .\.venv\Scripts\python.exe -m evals.cli validate-dataset
    .\.venv\Scripts\python.exe -m compileall -q core services infrastructure tools prompts ui evals
    git diff --check
    git status --short

The opt-in harness is now available:

    .\.venv\Scripts\python.exe -m evals.live_modules --allow-live --config .env --model mimo-v2.5-pro --output outputs/<new-directory> --budget <remaining-budget>

Use `--cases stream tool_roundtrip full_compaction` for selected checks.
Every rerun must subtract all prior reserved amounts from the original ceiling.
Read `.env` through `load_provider_config`; never print credentials or headers.

## Validation and Acceptance


Regression tests must fail on the original implementation and pass after the
fix. Session records must stay in order and in their original session; failed
file opening must leave queued records retryable. Headless shutdown must execute
the same lifecycle as CLI shutdown before exporting logs. The full offline
suite must remain green. Live module checks must have explicit assertions and
usage accounting, not merely a model saying that it succeeded. Verify the
pushed commit with the GitHub branch API and a clean working tree.

## Idempotence and Recovery


Tests use temporary directories. Live evidence is written to an explicitly
chosen ignored output directory; reruns use new directories. Never force push,
stage `.env`, private sessions or virtual environments, or remove user files.
If a provider lacks usage, reserve a conservative upper bound and mark the
call unmetered; stop before the global reservation ceiling is exceeded.

## Artifacts and Notes


Baseline: Python 3.11.15, `845 passed in 23.25s`. Final:
`895 passed in 21.15s`. Provider configured: `custom / mimo-v2.5-pro-1m`;
this alias failed two real probes. Test-only override `mimo-v2.5-pro` was used
for the successful module checks. No `.env` changes were made.

## Interfaces and Dependencies


Reuse pytest, asyncio, httpx, pathlib and the existing `ModelClient.stream`,
`ContextSnapshot`, `ModelUsage`, and runtime composition contracts. Do not add
a new runtime framework or a second agent loop. Preserve the existing license
and distinguish upstream code from later contributions in the report.

Revision note: updated after final offline validation, code commit and evidence
cross-checks. The large single-turn compaction fixture exposed a production
bug, not merely a benchmark assertion issue. Report and sanitized JSON are
complete and published, with remote HEAD verified. This final revision archives
the plan after completion; original files, credentials and private raw outputs
remain untouched.
