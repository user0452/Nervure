"""Persistent single-owner TTY renderer.

The interactive CLI owns one prompt_toolkit ``Application`` for its entire
life. Agent events only mutate this view model; no stream path writes to
stdout or commits Activity rows into scrollback.
"""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import ANSI, FormattedText, to_formatted_text
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import ConditionalContainer, HSplit, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.scrollable_pane import ScrollablePane
from prompt_toolkit.layout.processors import BeforeInput
from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType
from prompt_toolkit.styles import Style
from rich.console import Console

from core.stream_events import AgentEvent
from ui.cli.terminal.activity import (
    ActivityGroup,
    format_activity_compact_tools,
    format_tool_call,
)
from ui.cli.terminal.interaction_host import TerminalInteractionHost
from ui.cli.terminal.markdown_rendering import render_cached_markdown
from ui.cli.terminal.queue import InputQueue
from ui.cli.terminal.stream_reducer import reduce_stream_event
from ui.cli.terminal.stream_state import CliStreamUiState
from ui.cli.terminal.completer import InlineCompleter
from ui.cli.types import CliRuntime


_STYLE = Style.from_dict(
    {
        "header": "bold",
        "header-dim": "#888888",
        "prompt-border": "#666666",
        "prompt-gutter": "ansicyan bold",
        "user": "ansicyan",
        "user-label": "ansicyan bold",
        "user-border": "#666666",
        "activity": "ansiyellow",
        "activity-detail": "#888888",
        "assistant": "",
        "status": "#888888",
    }
)


@dataclass
class _TranscriptEntry:
    kind: str
    text: str = ""
    activity: ActivityGroup | None = None


@dataclass
class _AssistantTurnText:
    text: str
    assistant_call_id: str
    model_turn_index: int


class _TranscriptPane(ScrollablePane):
    """Scrollable transcript surface with explicit follow-bottom semantics.

    ``ScrollablePane`` normally keeps the focused window visible.  The CLI
    focus is intentionally on the input Buffer, so that default policy never
    advances the transcript after new content arrives.  This small wrapper
    tracks the real wrapped content height and lets the owning app decide
    whether to follow the bottom or preserve a user's manual scroll position.
    """

    def __init__(self, owner: "PersistentTerminalApp", content: Window) -> None:
        super().__init__(content, show_scrollbar=False)
        self._owner = owner

    def write_to_screen(
        self,
        screen: Any,
        mouse_handlers: Any,
        write_position: Any,
        parent_style: str,
        erase_bg: bool,
        z_index: int | None,
    ) -> None:
        # Keep geometry calculation identical to ScrollablePane: preferred
        # height includes wrapped visual lines, not just logical newlines.
        virtual_width = write_position.width
        preferred = self.content.preferred_height(
            virtual_width, self.max_available_height
        ).preferred
        virtual_height = min(
            max(preferred, write_position.height), self.max_available_height
        )
        self._owner._update_transcript_geometry(
            virtual_height=virtual_height,
            viewport_height=write_position.height,
        )
        super().write_to_screen(
            screen,
            mouse_handlers,
            write_position,
            parent_style,
            erase_bg,
            z_index,
        )

    def _copy_over_mouse_handlers(
        self,
        mouse_handlers: Any,
        temp_mouse_handlers: Any,
        write_position: Any,
        virtual_width: int,
    ) -> None:
        # First let ScrollablePane copy and coordinate Activity handlers.
        super()._copy_over_mouse_handlers(
            mouse_handlers,
            temp_mouse_handlers,
            write_position,
            virtual_width,
        )

        # Install a pane-wide fallback.  It runs before any Activity handler,
        # so wheel events over Activity/text/blank rows all scroll the pane;
        # non-wheel events delegate to the handler that ScrollablePane copied.
        rows = mouse_handlers.mouse_handlers
        for row_offset in range(write_position.height):
            row = rows[write_position.ypos + row_offset]
            for column_offset in range(virtual_width):
                column = write_position.xpos + column_offset
                delegated = row[column]

                def handler(
                    event: MouseEvent,
                    delegated_handler=delegated,
                ) -> Any:
                    if event.event_type == MouseEventType.SCROLL_UP:
                        self._owner._scroll_transcript(-3)
                        return None
                    if event.event_type == MouseEventType.SCROLL_DOWN:
                        self._owner._scroll_transcript(3)
                        return None
                    return delegated_handler(event)

                row[column] = handler


class PersistentTerminalApp:
    """One application and one render tree for the complete interactive CLI."""

    def __init__(
        self,
        runtime: CliRuntime,
        *,
        interaction_host: TerminalInteractionHost,
        on_submit: Callable[[str], Awaitable[None]],
        on_exit: Callable[[], Awaitable[None]],
        on_cancel: Callable[[], None] | None = None,
        input=None,  # type: ignore[no-untyped-def]
        output=None,  # type: ignore[no-untyped-def]
    ) -> None:
        self.runtime = runtime
        self.interaction_host = interaction_host
        self._on_submit = on_submit
        self._on_exit = on_exit
        self._on_cancel = on_cancel
        self._input = input
        self._output = output
        self._queue = InputQueue()
        self._transcript: list[_TranscriptEntry] = []
        self._active_state: CliStreamUiState | None = None
        # Stable semantic Activity objects for the active user turn. Mouse and
        # Ctrl+O mutate these objects, and the same instances move into the
        # completed transcript when the turn finishes.
        self._active_activity_groups: dict[str, ActivityGroup] = {}
        self._turn_texts: list[_AssistantTurnText] = []
        self._busy = False
        self._closing = False
        self.follow_bottom = True
        self.user_scrolled = False
        self._transcript_content_height = 0
        self._transcript_viewport_height = 0
        self._transcript_max_scroll = 0
        self._submit_task: asyncio.Task[None] | None = None
        self._buffer = Buffer(
            completer=InlineCompleter(runtime),
            complete_while_typing=True,
        )
        self._header_window = Window(
            height=Dimension(min=1, max=1),
            content=FormattedTextControl(self._render_header),
        )
        self._transcript_control = FormattedTextControl(self._render_transcript)
        self._transcript_window = Window(
            content=self._transcript_control,
            wrap_lines=True,
            height=Dimension(min=1),
        )
        self._transcript_pane = _TranscriptPane(self, self._transcript_window)
        self._input_control = BufferControl(
            buffer=self._buffer,
            input_processors=[BeforeInput("❯ ", style="class:prompt-gutter")],
            include_default_input_processors=True,
        )
        self._input_window = Window(
            content=self._input_control,
            height=Dimension(min=1, max=1),
            wrap_lines=False,
        )
        self._status_window = Window(
            height=Dimension(min=1, max=1),
            content=FormattedTextControl(self._render_status),
        )
        self._interaction_window = Window(
            content=FormattedTextControl(self._render_interaction),
            wrap_lines=True,
        )
        self._interaction_pane = ConditionalContainer(
            content=HSplit(
                [
                    Window(
                        height=Dimension(min=1, max=1),
                        char="─",
                        style="class:prompt-border",
                    ),
                    self._interaction_window,
                ]
            ),
            filter=Condition(self._interaction_active),
        )
        self._input_separator_window = Window(
            height=Dimension(min=1, max=1),
            content=FormattedTextControl(self._render_input_separator),
        )
        self._app = self._build_app()

    @property
    def app(self) -> Application[None]:
        return self._app

    @property
    def queue(self) -> InputQueue:
        return self._queue

    def append_banner(self) -> None:
        """Seed the full-screen workspace with one compact welcome row."""

        self._transcript.append(
            _TranscriptEntry(
                "banner",
                text="Ready. Describe what you want to inspect, change, or build.",
            )
        )
        self.invalidate()

    def append_user(self, text: str) -> None:
        self._transcript.append(_TranscriptEntry("user", text=text))
        self.invalidate()

    def append_notice(self, text: str) -> None:
        if text:
            self._transcript.append(_TranscriptEntry("notice", text=text))
            self.invalidate()

    def append_renderable(self, renderable: object) -> None:
        """Capture a slash-command renderable without writing to stdout."""

        console = Console(
            file=io.StringIO(), record=True, width=120, force_terminal=False
        )
        console.print(renderable)
        text = console.export_text(styles=False).rstrip()
        if text:
            self.append_notice(text)

    def append_replay_messages(self, messages: Any) -> None:
        """Put restored messages into this app's transcript view model."""

        for message in messages:
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            text = self._message_text(message.get("content"))
            if role == "user" and text:
                self._transcript.append(_TranscriptEntry("user", text=text))
            elif role == "assistant" and text:
                self._transcript.append(_TranscriptEntry("assistant", text=text))
            elif role == "tool_result":
                tool_name = str(message.get("tool_name") or "tool")
                summary = self._message_text(message.get("content"))
                if len(summary) > 240:
                    summary = summary[:237] + "..."
                self._transcript.append(
                    _TranscriptEntry("notice", text=f"{tool_name}: {summary}")
                )
        self.invalidate()

    def reset_main_view(self, renderable: object | None = None) -> None:
        """Reset only the persistent transcript, keeping the render owner."""

        self._transcript.clear()
        self.append_banner()
        if renderable is not None:
            self.append_renderable(renderable)

    def set_runtime(self, runtime: CliRuntime) -> None:
        self.runtime = runtime
        self._buffer.completer = InlineCompleter(runtime)
        self.invalidate()

    def begin_turn(self) -> None:
        self._active_state = CliStreamUiState()
        self._active_activity_groups = {}
        self._turn_texts = []
        self._busy = True
        self.invalidate()

    async def consume_events(self, events: Any) -> CliStreamUiState:
        if self._active_state is None:
            self.begin_turn()
        assert self._active_state is not None
        try:
            async for event in events:
                event_type = getattr(event, "type", None)
                if event_type in {"assistant_message_completed", "completed"}:
                    text = str(getattr(event, "text", "") or "")
                    if text:
                        self._remember_turn_text(event, text)
                reduce_stream_event(self._active_state, event)
                self.invalidate()
        finally:
            self._finish_turn()
        return self._active_state

    def _remember_turn_text(self, event: Any, text: str) -> None:
        metadata = getattr(event, "metadata", None) or {}
        assistant_call_id = str(metadata.get("assistant_call_id") or "")
        raw_turn_index = metadata.get("model_turn_index")
        try:
            model_turn_index = int(raw_turn_index)
        except (TypeError, ValueError):
            current = self._active_state.current_model_turn_index if self._active_state else None
            model_turn_index = int(current or 0)

        if assistant_call_id:
            for item in self._turn_texts:
                if item.assistant_call_id == assistant_call_id:
                    item.text = text
                    item.model_turn_index = model_turn_index
                    return
        elif any(
            item.model_turn_index == model_turn_index and item.text == text
            for item in self._turn_texts
        ):
            return

        self._turn_texts.append(
            _AssistantTurnText(
                text=text,
                assistant_call_id=assistant_call_id,
                model_turn_index=model_turn_index,
            )
        )

    def _ordered_active_turn_items(
        self,
        state: CliStreamUiState,
        *,
        include_streaming: bool = False,
    ) -> list[tuple[str, Any]]:
        ordered: list[tuple[int, int, int, str, Any]] = []
        for index, item in enumerate(self._turn_texts):
            ordered.append((item.model_turn_index, 0, index, "assistant", item))
        if include_streaming and state.streaming_text:
            ordered.append(
                (
                    int(state.current_model_turn_index or 0),
                    0,
                    len(self._turn_texts),
                    "streaming_assistant",
                    state.streaming_text,
                )
            )
        for index, group in enumerate(self._semantic_groups(state)):
            ordered.append((group.model_turn_index, 1, index, "activity", group))
        ordered.sort(key=lambda item: item[:3])
        return [(kind, payload) for _, _, _, kind, payload in ordered]

    def _finish_turn(self) -> None:
        state = self._active_state
        if state is not None:
            for kind, payload in self._ordered_active_turn_items(state):
                if kind == "activity":
                    self._transcript.append(
                        _TranscriptEntry("activity", activity=payload)  # type: ignore[arg-type]
                    )
                else:
                    self._transcript.append(
                        _TranscriptEntry("assistant", text=payload.text)  # type: ignore[union-attr]
                    )
        self._active_state = None
        self._active_activity_groups = {}
        self._turn_texts = []
        self._busy = False
        self.invalidate()

    async def run(self) -> None:
        self.interaction_host.bind_app(self._app)
        try:
            await self._app.run_async()
        finally:
            self.interaction_host.unbind_app(self._app)

    def invalidate(self) -> None:
        if self._app.is_running:
            self._app.invalidate()

    @property
    def transcript_vertical_scroll(self) -> int:
        return self._transcript_pane.vertical_scroll

    def _update_transcript_geometry(
        self,
        *,
        virtual_height: int,
        viewport_height: int,
    ) -> None:
        self._transcript_content_height = virtual_height
        self._transcript_viewport_height = viewport_height
        self._transcript_max_scroll = max(0, virtual_height - viewport_height)
        if self.follow_bottom:
            self._transcript_pane.vertical_scroll = self._transcript_max_scroll
            return
        self._transcript_pane.vertical_scroll = min(
            max(0, self._transcript_pane.vertical_scroll),
            self._transcript_max_scroll,
        )

    def _scroll_transcript(self, delta: int) -> None:
        max_scroll = self._transcript_max_scroll
        if max_scroll <= 0:
            self.follow_bottom = True
            self.user_scrolled = False
            self._transcript_pane.vertical_scroll = 0
            self.invalidate()
            return

        current = self._transcript_pane.vertical_scroll
        target = min(max(current + delta, 0), max_scroll)
        self._transcript_pane.vertical_scroll = target
        if target >= max_scroll:
            self.follow_bottom = True
            self.user_scrolled = False
        elif target != current:
            self.follow_bottom = False
            self.user_scrolled = True
        self.invalidate()

    def toggle_all_activities(self) -> None:
        groups = self._visible_groups()
        if groups:
            expanded = not all(group.expanded for group in groups)
            for group in groups:
                group.expanded = expanded
            self.invalidate()

    def _build_app(self) -> Application[None]:
        own_bindings = self._build_key_bindings()
        host_bindings = self.interaction_host.key_bindings(
            fallback_cancel=lambda event: self._cancel(event),
            exit_on_complete=False,
        )
        layout = Layout(
            HSplit(
                [
                    self._header_window,
                    self._transcript_pane,
                    self._interaction_pane,
                    self._input_separator_window,
                    self._input_window,
                    self._status_window,
                ]
            ),
            focused_element=self._input_window,
        )
        return Application(
            layout=layout,
            style=_STYLE,
            full_screen=True,
            erase_when_done=False,
            mouse_support=True,
            key_bindings=merge_key_bindings([host_bindings, own_bindings]),
            input=self._input,
            output=self._output,
        )

    def _build_key_bindings(self) -> KeyBindings:
        bindings = KeyBindings()
        normal_input_active = Condition(lambda: not self.interaction_host.is_active())
        submit_input_active = Condition(
            lambda: normal_input_active() or self.interaction_host.text_input_active()
        )

        @bindings.add(Keys.Enter, eager=True, filter=submit_input_active)
        def _on_enter(event) -> None:  # type: ignore[no-untyped-def]
            text = self._buffer.text.strip()
            if self.interaction_host.text_input_active():
                if not text:
                    return
                self._buffer.reset()
                self.interaction_host.submit_text(text)
                self.invalidate()
                return
            if not text:
                return
            self._buffer.reset()
            if self._busy:
                self._queue.push(text)
                self.invalidate()
                return
            self.append_user(text)
            self._submit_task = asyncio.create_task(self._on_submit(text))

        @bindings.add(Keys.ControlO, eager=True)
        def _on_toggle(event) -> None:  # type: ignore[no-untyped-def]
            self.toggle_all_activities()

        @bindings.add(Keys.ControlC, eager=True, filter=normal_input_active)
        def _on_ctrl_c(event) -> None:  # type: ignore[no-untyped-def]
            if self._busy:
                self._cancel(event)
                return
            if self._buffer.text:
                self._buffer.reset()
                return
            self._closing = True
            asyncio.create_task(self._on_exit())
            event.app.exit()

        @bindings.add(Keys.ControlD, eager=True, filter=normal_input_active)
        def _on_ctrl_d(event) -> None:  # type: ignore[no-untyped-def]
            if not self._buffer.text:
                self._closing = True
                asyncio.create_task(self._on_exit())
                event.app.exit()

        @bindings.add(Keys.Escape, eager=True, filter=normal_input_active)
        def _on_escape(event) -> None:  # type: ignore[no-untyped-def]
            if self._busy:
                self._cancel(event)

        return bindings

    def _cancel(self, event: Any) -> None:
        if self.interaction_host.is_active():
            return
        if self._submit_task is not None and not self._submit_task.done():
            self._submit_task.cancel()
        if self._on_cancel is not None:
            self._on_cancel()
        if event is not None and getattr(event, "app", None) is not None:
            event.app.invalidate()

    def _terminal_width(self) -> int:
        try:
            return max(int(self._app.output.get_size().columns), 20)
        except Exception:
            return 120

    def _render_header(self) -> FormattedText:
        return FormattedText(
            [
                ("class:header", " Nervure "),
                ("class:header-dim", f"  {self.runtime.workspace}  ·  {self.runtime.model}"),
            ]
        )

    def _render_input_separator(self) -> FormattedText:
        width = self._terminal_width()
        label = " Input "
        prefix = "─"
        remaining = max(0, width - len(prefix) - len(label))
        return FormattedText(
            [
                ("class:prompt-border", prefix),
                ("class:prompt-gutter", label),
                ("class:prompt-border", "─" * remaining),
            ]
        )

    def _render_status(self) -> FormattedText:
        if self.interaction_host.is_active():
            return self.interaction_host.render_status()
        if self._busy:
            return FormattedText([("class:status", "Nervure> working…  (Ctrl+O 展开 Activity · Esc 取消)")])
        return FormattedText([("class:status", "Nervure> ")])

    def _interaction_active(self) -> bool:
        return self.interaction_host.is_active()

    def _render_interaction(self):  # type: ignore[no-untyped-def]
        if not self._interaction_active():
            return ""
        try:
            width = self._app.output.get_size().columns
        except Exception:
            width = 120
        body = self.interaction_host.render_body(width=max(int(width), 20))
        return body if body is not None else ""

    def _render_transcript(self) -> FormattedText:
        fragments: list[tuple] = []
        for entry in self._transcript:
            if entry.kind == "banner":
                self._line(fragments, "class:status", entry.text)
            elif entry.kind == "user":
                self._user_fragments(fragments, entry.text)
            elif entry.kind == "notice":
                self._line(fragments, "class:status", entry.text)
            elif entry.kind == "assistant":
                self._assistant_fragments(fragments, entry.text)
            elif entry.activity is not None:
                self._activity_fragments(fragments, entry.activity)

        if self._active_state is not None:
            for kind, payload in self._ordered_active_turn_items(
                self._active_state,
                include_streaming=True,
            ):
                if kind == "activity":
                    self._activity_fragments(fragments, payload)
                elif kind == "streaming_assistant":
                    self._assistant_fragments(fragments, payload)
                else:
                    self._assistant_fragments(fragments, payload.text)
        return FormattedText(fragments)

    def _user_fragments(self, fragments: list[tuple], text: str) -> None:
        """Render one user turn as a visually isolated transcript block."""

        width = max(24, min(self._terminal_width() - 2, 96))
        label = " You "
        top = "╭─" + label + "─" * max(0, width - len(label) - 2)
        bottom = "╰" + "─" * max(1, width - 1)
        self._line(fragments, "class:user-label", top)
        lines = text.splitlines() or [text]
        for line in lines:
            fragments.append(("class:user-border", "│ "))
            fragments.append(("class:user", line))
            fragments.append(("", "\n"))
        self._line(fragments, "class:user-border", bottom)
        fragments.append(("", "\n"))

    def _assistant_fragments(self, fragments: list[tuple], text: str) -> None:
        """Render assistant Markdown inside the persistent transcript tree."""

        if not text:
            return
        self._line(fragments, "class:assistant", "Nervure>")
        try:
            width = self._app.output.get_size().columns
        except Exception:
            width = 120
        rendered_lines = render_cached_markdown(text, width=max(int(width), 20))
        if not rendered_lines:
            return
        fragments.extend(to_formatted_text(ANSI("\n".join(rendered_lines))))
        fragments.append(("", "\n"))

    def _line(self, fragments: list[tuple], style: str, text: str) -> None:
        fragments.append((style, text))
        fragments.append(("", "\n"))

    @staticmethod
    def _message_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and isinstance(block.get("text"), str)
            )
        if content is None:
            return ""
        return str(content)

    def _activity_fragments(self, fragments: list[tuple], group: ActivityGroup) -> None:
        arrow = "▼" if group.expanded else "▶"
        marker = "✓" if group.complete else "●"
        parts = [f"{arrow} {marker} {group.title}"]
        compact_tools = format_activity_compact_tools(group)
        if compact_tools:
            parts.append(compact_tools)
        if group.error_count:
            parts.append(f"{group.error_count} failed")
        label = " · ".join(parts)
        if group.complete:
            label = f"{label} — Completed"

        def toggle(event: MouseEvent, target: ActivityGroup = group):
            if event.event_type == MouseEventType.MOUSE_UP and event.button == MouseButton.LEFT:
                target.expanded = not target.expanded
                self.invalidate()
                return None
            return NotImplemented

        fragments.append(("class:activity", label, toggle))
        fragments.append(("", "\n"))
        if group.expanded:
            for index, tool in enumerate(group.tools):
                branch = "└─" if index == len(group.tools) - 1 else "├─"
                self._line(
                    fragments,
                    "class:activity-detail",
                    f"  {branch} {format_tool_call(tool.tool_name, tool.arguments)}",
                )

    def _visible_groups(self) -> list[ActivityGroup]:
        groups: list[ActivityGroup] = []
        for entry in self._transcript:
            if entry.activity is not None:
                groups.append(entry.activity)
        if self._active_state is not None:
            groups.extend(self._semantic_groups(self._active_state))
        return groups

    def _semantic_groups(self, state: CliStreamUiState) -> list[ActivityGroup]:
        """Return stable model-turn Activity objects for the active user turn.

        Reducer groups are already scoped to one assistant/model invocation. Keep
        that boundary in the persistent UI: a later model turn must become a new
        Activity row even when it has the same semantic title. This lets progress
        text and tool work interleave chronologically instead of collapsing every
        ``Read``/``Search`` phase into one giant session-wide group.

        The stable key is the assistant call id (with a turn-scoped fallback), so
        click/Ctrl+O state still survives repaints and active -> completed while
        never merging separate model turns by title alone.
        """

        for group in state.visible_activity_groups():
            key = group.assistant_call_id or (
                f"turn:{group.model_turn_index}:"
                f"{group.activity_id or group.title or 'activity'}"
            )
            target = self._active_activity_groups.get(key)
            if target is None:
                target = ActivityGroup(
                    assistant_call_id=group.assistant_call_id,
                    model_turn_index=group.model_turn_index,
                    title=group.title,
                    activity_id=group.activity_id or key,
                    expanded=group.expanded,
                    status=group.status,
                )
                self._active_activity_groups[key] = target
            else:
                target.title = group.title
                target.status = group.status

            existing_by_id = {tool.call_id: tool for tool in target.tools}
            for tool in group.tools:
                existing = existing_by_id.get(tool.call_id)
                if existing is None:
                    # Keep the reducer-owned tool object itself so result
                    # status updates flow into the stable semantic group.
                    target.tools.append(tool)
                    existing_by_id[tool.call_id] = tool
                elif existing is not tool:
                    existing.tool_name = tool.tool_name
                    existing.arguments = tool.arguments
                    existing.result_is_error = tool.result_is_error
                    existing.started = tool.started
        return list(self._active_activity_groups.values())


__all__ = ["PersistentTerminalApp"]
