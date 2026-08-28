"""Inline REPL — the TTY entry point for the Nervure CLI.

The :class:`InlineRepl` wires commands and agent events into one persistent
``PersistentTerminalApp`` render owner. Compatibility renderers remain for
batch/non-TTY and transient command pages, but a live TTY turn never writes
to stdout outside that application.

Loop shape::

    PersistentTerminalApp owns the input Buffer and submits either a slash
    command or an agent turn; queued input is drained in FIFO order after the
    current turn.

A "turn" is one full pass through the agent loop, including any
tool calls and queued follow-ups. ``InputQueue`` is shared with
:class:`StreamingSession` so the user can keep typing while an
agent turn is running; the running-turn input box pushes new
submissions onto the same queue, and the REPL drains it once the
turn finishes.
"""

from __future__ import annotations

import asyncio
import shlex
import shutil
import sys
from typing import Awaitable, Callable

from rich.console import Console
from rich.text import Text

from core.runtime_state import InteractionKind, RunStatus, RuntimeState
from services.errors import actionable_error_message
from services.plans import build_plan_attachments_for_state
from ui.cli import renderer
from ui.cli.commands import dispatch_command
from ui.cli.resume import list_session_summaries, restore_runtime_from_target
from ui.cli.suggestions import SuggestionItem
from ui.cli.terminal.connect_flow import run_connect_flow
from ui.cli.terminal.detect import detect_terminal_brightness
from ui.cli.terminal.interaction_host import TerminalInteractionHost
from ui.cli.terminal.page import TransientPage
from ui.cli.terminal.permission_prompt import TtyPermissionPrompter
from ui.cli.terminal.question_prompt import TtyUserQuestionPrompter
from ui.cli.terminal.prompt_session import PromptSession, PromptSubmission, SubmissionKind
from ui.cli.terminal.persistent_app import PersistentTerminalApp
from ui.cli.terminal.queue import InputQueue
from ui.cli.terminal.selector import SelectorItem, TransientSelector
from ui.cli.terminal.static_output import print_user_submitted
from ui.cli.terminal.stream_session import StreamingSession
from ui.cli.terminal.trust_prompt import default_trust_prompt
from ui.cli.terminal.transcript_replay import replay_messages_to_static
from ui.cli.theme import rich_theme_for
from ui.cli.types import CliRuntime, CommandResult


class InlineRepl:
    """The TTY CLI main loop, implemented with one prompt_toolkit app."""

    def __init__(
        self,
        runtime: CliRuntime,
        *,
        permission_prompter: TtyPermissionPrompter | None = None,
        user_question_prompter: TtyUserQuestionPrompter | None = None,
        interaction_host: TerminalInteractionHost | None = None,
    ) -> None:
        self._runtime = runtime
        self._interaction_host = interaction_host or TerminalInteractionHost()
        self._brightness = detect_terminal_brightness()
        self._queue = InputQueue()
        self._prompt = PromptSession(runtime, self._queue)
        self._agent_running = False
        self._cancel_requested = False
        self._pending_attachments: list[dict[str, object]] = []
        self._permission_prompter = permission_prompter or TtyPermissionPrompter(
            self._interaction_host
        )
        self._user_question_prompter = user_question_prompter or TtyUserQuestionPrompter(
            self._interaction_host
        )
        # Use the brightness-aware theme so foreground colors stay
        # legible against light or dark hosts. Static region only —
        # the theme never sets a background.
        self._console = Console(theme=rich_theme_for(self._brightness))
        self._terminal_app: PersistentTerminalApp | None = None

    # --- public entry -----------------------------------------------------

    def run(self) -> int:
        """Synchronous entry point. Returns a process exit code."""

        try:
            asyncio.run(self._main_loop())
        except KeyboardInterrupt:
            self._shutdown()
            return 0
        return 0

    # --- main loop --------------------------------------------------------

    async def _main_loop(self) -> None:
        self._terminal_app = PersistentTerminalApp(
            self._runtime,
            interaction_host=self._interaction_host,
            on_submit=self._handle_terminal_submission,
            on_exit=self._shutdown_async,
            on_cancel=self._cancel_current_turn,
        )
        self._terminal_app.append_banner()
        if self._runtime.background_task_manager is not None:
            self._runtime.background_task_manager.bind_terminal_notifier(
                self._terminal_app.append_notice
            )
        if not self._runtime.configured:
            self._terminal_app.append_notice(
                "⚠ 尚未配置供应商。请输入 /connect 进行配置。"
            )
        try:
            await self._terminal_app.run()
        finally:
            if self._runtime.background_task_manager is not None:
                self._runtime.background_task_manager.bind_terminal_notifier(None)

    async def _handle_terminal_submission(self, text: str) -> None:
        """Dispatch one line submitted by the persistent application."""

        if self._runtime is None:
            return
        if text.startswith("/"):
            if not self._runtime.configured:
                command = text.split()[0][1:].lower()
                if command not in {"connect", "exit"}:
                    if self._terminal_app is not None:
                        self._terminal_app.append_notice(
                            "尚未配置供应商。请先使用 /connect 配置 API 供应商。"
                        )
                    return
            await self._handle_command(text)
            return
        if not self._runtime.configured:
            if self._terminal_app is not None:
                self._terminal_app.append_notice(
                    "尚未配置供应商。请先使用 /connect 配置 API 供应商。"
                )
            return
        await self._run_turn(text)
        await self._drain_queue()

    # --- command dispatch -------------------------------------------------

    async def _handle_command(self, line: str) -> None:
        result = dispatch_command(self._runtime, line)
        if result.interaction == "resume_selector":
            result = await self._run_resume_selector()
        elif result.interaction == "connect":
            result = await self._run_connect_flow()
        if result.runtime is not None:
            self._runtime = result.runtime
            if self._terminal_app is not None:
                self._terminal_app.set_runtime(self._runtime)
            self._reset_prompt_session()
        if result.reset_main_view:
            self._reset_main_view(result.renderable)
            return
        if result.renderable is not None:
            if result.presentation == "page":
                await self._show_page(result.renderable)
            elif self._terminal_app is not None:
                self._terminal_app.append_renderable(result.renderable)
            else:
                self._console.print(result.renderable)
        # Replay restored history into the main scrollback after any inline
        # notice is printed. This runs once the resume selector (if any) has
        # already exited the alternate screen, and before the next prompt is
        # read, so historical messages land in the primary buffer.
        if result.replay_messages:
            if self._terminal_app is not None:
                self._terminal_app.append_replay_messages(result.replay_messages)
            else:
                replay_messages_to_static(
                    result.replay_messages,
                    brightness=self._brightness,
                    workspace=self._runtime.workspace if self._runtime else None,
                )
        if result.attachments:
            self._pending_attachments.extend(result.attachments)
        if result.should_exit:
            await self._shutdown_async()
            self._runtime = None
            if self._terminal_app is not None and self._terminal_app.app.is_running:
                self._terminal_app.app.exit()
            return
        if result.queued_prompt:
            if not self._runtime.configured:
                if self._terminal_app is not None:
                    self._terminal_app.append_notice(
                        "尚未配置供应商。请先使用 /connect 配置 API 供应商。"
                    )
                else:
                    self._console.print(
                        Text(
                            "尚未配置供应商。请先使用 /connect 配置 API 供应商。",
                            style="nervure.warning",
                        )
                    )
                return
            await self._run_turn(result.queued_prompt)
            await self._drain_queue()

    async def _run_resume_selector(self) -> CommandResult:
        summaries = list_session_summaries(self._runtime.workspace)
        if not summaries:
            return CommandResult(
                renderable=renderer.render_session_summaries(
                    summaries, self._runtime.workspace
                ),
                presentation="page",
            )

        def detail(summary: object) -> str:
            updated = getattr(summary, "updated_at", None)
            date = updated.strftime("%Y-%m-%d") if updated else ""
            count = getattr(summary, "message_count", 0)
            return f"{date}  {count} messages".strip()

        items = tuple(
            SelectorItem(
                label=getattr(summary, "title", summary.session_id),
                value=summary,
                detail=detail(summary),
            )
            for summary in summaries
        )
        selector: TransientSelector = TransientSelector("Resume", items)
        chosen = await selector.run()
        if chosen is None:
            return CommandResult()
        assert chosen.value is not None
        try:
            resumed = restore_runtime_from_target(self._runtime, chosen.value.session_id)
        except Exception as exc:
            return CommandResult(renderable=renderer.render_error(str(exc)))
        return CommandResult(
            runtime=resumed,
            renderable=renderer.render_resume(
                resumed.state.session_id,
                resumed.message_store.transcript_store.messages_path,
                resumed.workspace,
                classification=str(
                    resumed.state.metadata.get(
                        "resume_classification",
                        "SAFE_RESUME",
                    )
                ),
                reasons=tuple(resumed.state.metadata.get("resume_reasons", ())),
            ),
            presentation="inline",
            replay_messages=resumed.message_store.current_messages(),
        )

    async def _run_connect_flow(self) -> CommandResult:
        was_configured = self._runtime.configured
        result = await run_connect_flow(self._runtime)
        if result.cancelled or result.runtime is None:
            return CommandResult(renderable=result.renderable)
            
        runtime = result.runtime
        if not was_configured:
            from ui.cli.app import build_runtime
            from ui.cli.terminal.trust_prompt import default_trust_prompt
            try:
                runtime = build_runtime(
                    self._runtime.workspace,
                    trust_prompt=default_trust_prompt,
                    permission_prompter=self._permission_prompter,
                    user_question_prompter=self._user_question_prompter,
                    mcp_trust_mode="prompt",
                )
            except Exception as exc:
                return CommandResult(renderable=renderer.render_error(f"Failed to initialize runtime: {exc}"))

        return CommandResult(
            runtime=runtime,
            renderable=result.renderable,
            reset_main_view=True,
        )

    async def _show_page(self, renderable: object) -> None:
        """Show a renderable full-screen until the user presses Esc.

        On non-TTY hosts the page is a no-op, so we fall back to
        printing the renderable inline into the static region.
        """

        from ui.cli.terminal.transient import can_enter_alternate_screen

        if not can_enter_alternate_screen():
            self._console.print(renderable)
            return
        page = TransientPage(renderable)
        await page.show()

    def _reset_prompt_session(self) -> None:
        self._prompt = PromptSession(self._runtime, self._queue)

    def _reset_main_view(self, renderable: object | None) -> None:
        if self._terminal_app is not None:
            self._terminal_app.reset_main_view(renderable)
            return
        self._push_previous_view_out()
        self._console.print(renderer.render_banner(self._runtime))
        if renderable is not None:
            self._console.print(renderable)

    def _push_previous_view_out(self) -> None:
        for _ in range(self._terminal_height() + 1):
            self._console.print()

    def _terminal_height(self) -> int:
        return shutil.get_terminal_size((80, 24)).lines

    # --- agent turn -------------------------------------------------------

    async def _run_turn(self, line: str) -> None:
        """Run one full agent turn through the persistent render owner.

        The compatibility ``StreamingSession`` branch is retained only for
        direct tests/non-TTY callers that do not install a persistent app.

        The session shares ``self._queue`` so the user can keep
        typing into the running-turn input box while the agent is
        busy; queued submissions land on the same FIFO that
        :meth:`_drain_queue` will consume after the turn ends.
        """

        self._agent_running = True
        try:
            events = self._agent_events(line)
            if self._terminal_app is not None:
                self._terminal_app.begin_turn()
                await self._terminal_app.consume_events(events)
            else:
                session = StreamingSession(
                    workspace=self._runtime.workspace,
                    queue=self._queue,
                    runtime=self._runtime,
                    interaction_host=self._interaction_host,
                )
                await session.run(events)
        except Exception as exc:
            self._runtime.error_log_recorder.record_error(
                exc,
                source="cli_main_loop",
                attributes={"turn_count": self._runtime.state.turn_count},
            )
            self._runtime.error_log_recorder.flush()
            if self._terminal_app is not None:
                self._terminal_app.append_renderable(renderer.render_error(str(exc)))
            else:
                self._console.print(renderer.render_error(str(exc)))
        finally:
            self._agent_running = False

        if (
            self._runtime is not None
            and self._runtime.state.interaction is not None
            and self._runtime.state.interaction.kind == InteractionKind.PLAN_REVIEW
        ):
            await self._run_plan_review()

    async def _run_plan_review(self) -> None:
        """Present the pending plan as a keyboard-driven review flow."""

        if self._runtime is None:
            return
        interaction = self._runtime.state.interaction
        if interaction is None or interaction.kind != InteractionKind.PLAN_REVIEW:
            return

        if self._terminal_app is not None:
            decision = await self._interaction_host.request_plan_review(
                summary=str(interaction.payload.get("summary", "") or ""),
                plan_path=str(interaction.payload.get("plan_path", "") or ""),
            )
            if decision.action == "approve":
                await self._handle_command("/plan approve")
                return
            if decision.action == "modify":
                if decision.feedback:
                    self._terminal_app.append_user(decision.feedback)
                await self._handle_command(
                    f"/plan reject {shlex.quote(decision.feedback)}"
                )
                return
            await self._handle_command("/plan reject")
            return

        # Compatibility fallback for direct/non-persistent callers. The main
        # TTY path above never enters an alternate-screen selector or nested
        # PromptSession.
        selector = TransientSelector(
            "Plan ready",
            (
                SelectorItem(
                    label="Approve and implement",
                    value="approve",
                    detail="Exit Plan mode and start implementation",
                ),
                SelectorItem(
                    label="Request changes",
                    value="modify",
                    detail="Tell the agent what to revise in the plan",
                ),
                SelectorItem(
                    label="Reject for now",
                    value="reject",
                    detail="Stay in Plan mode without continuing automatically",
                ),
            ),
        )
        choice = await selector.run()
        if choice is None or choice.value == "reject":
            await self._handle_command("/plan reject")
            return
        if choice.value == "approve":
            await self._handle_command("/plan approve")
            return

        feedback_prompt = PromptSession(
            self._runtime,
            self._queue,
            bottom_hint="Describe the changes you want in the plan · Ctrl-C to cancel",
        )
        submission = await feedback_prompt.read()
        if submission.kind is not SubmissionKind.SUBMIT or not submission.text.strip():
            return
        feedback = submission.text.strip()
        print_user_submitted(feedback, brightness=self._brightness)
        await self._handle_command(f"/plan reject {shlex.quote(feedback)}")

    async def _drain_queue(self) -> None:
        """Pop queued inputs in FIFO order after a turn finishes.

        Each :class:`QueuedInput` is dispatched based on its
        ``kind``: ``slash`` entries go through :meth:`_handle_command`
        so they never reach the model as a prompt; ``prompt`` entries
        re-enter the agent via :meth:`_run_turn`. If a slash command
        causes the runtime to be replaced (e.g. ``/clear`` /
        ``/resume`` / ``/connect``), we keep draining because the
        command dispatcher has already updated ``self._runtime`` and
        reset the prompt session.

        The loop stops at the first empty pop; ``self._queue`` is
        the single source of truth.
        """

        while True:
            item = self._queue.pop()
            if item is None:
                return
            if self._terminal_app is not None:
                self._terminal_app.append_user(item.text)
            if item.kind == "slash":
                await self._handle_command(item.text)
                if self._runtime is None:
                    # A command (e.g. ``/exit``) closed the REPL.
                    return
                continue
            await self._run_turn(item.text)

    async def _agent_events(self, line: str):
        """Yield agent events for ``line``, collecting attachments first.

        Exceptions raised by the loop stream are surfaced as a single
        synthetic ``error`` event so the streaming preview can render
        them in line rather than crashing the REPL.
        """

        attachments = ()
        if self._runtime.attachment_collector is not None:
            attachments = (
                await self._runtime.attachment_collector.collect_for_user_turn(
                    line,
                    self._runtime.state,
                    self._runtime.message_store.current_messages(),
                    is_main_thread=True,
                )
            )
        command_attachments = tuple(self._pending_attachments)
        self._pending_attachments.clear()
        plan_attachments = ()
        if self._runtime.plan_store is not None:
            plan_attachments = tuple(
                build_plan_attachments_for_state(
                    self._runtime.state,
                    self._runtime.plan_store,
                )
            )
        attachments = (*attachments, *command_attachments, *plan_attachments)
        try:
            async for event in self._runtime.loop.stream(line, attachments=attachments):
                yield event
        except Exception as exc:
            self._runtime.error_log_recorder.record_error(
                exc,
                source="cli_main_loop",
                attributes={"turn_count": self._runtime.state.turn_count},
            )
            self._runtime.error_log_recorder.flush()
            yield _error_event(actionable_error_message(exc))

    def _cancel_current_turn(self) -> None:
        """Mark the active turn cancelled; the persistent app cancels its task."""

        self._cancel_requested = True
        if self._runtime is not None:
            self._runtime.state.status = RunStatus.CANCELLED
            self._runtime.state.resume()

    # --- shutdown ---------------------------------------------------------

    def _shutdown(self) -> None:
        """Synchronous shutdown used only outside an active event loop."""
        runtime = self._runtime
        if runtime is None:
            return
        runtime.persist_session_state()
        runtime.message_store.flush_transcript()
        runtime.trace_recorder.flush()
        runtime.error_log_recorder.flush()
        if runtime.mcp_manager is not None:
            try:
                asyncio.run(runtime.mcp_manager.close_all())
            except RuntimeError:
                # ``asyncio.run`` raises if a loop is already running
                # in the caller's thread; in that case we let the
                # process exit and rely on the atexit handler to
                # close transports.
                pass

    async def _shutdown_async(self) -> None:
        """Flush and close transports while the TTY loop is still running."""

        runtime = self._runtime
        if runtime is None:
            return
        runtime.message_store.flush_transcript()
        runtime.trace_recorder.flush()
        runtime.error_log_recorder.flush()
        if runtime.mcp_manager is not None:
            await runtime.mcp_manager.close_all()

    def _print_untrusted_mcp_notices(self, runtime: CliRuntime) -> None:
        raw = runtime.state.metadata.get("mcp_untrusted_servers", ())
        if not isinstance(raw, (list, tuple)):
            return
        from ui.cli.terminal.static_output import print_untrusted_mcp_notice

        for item in raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "unknown")
            command = str(item.get("command") or "")
            args = str(item.get("args") or "")
            detail = command if args == "(none)" else f"{command} {args}".strip()
            print_untrusted_mcp_notice(name, detail)


# --- helpers --------------------------------------------------------------


def _error_event(message: str) -> object:
    from core.stream_events import AgentEvent

    return AgentEvent(type="error", text=message)


__all__ = ["InlineRepl"]
