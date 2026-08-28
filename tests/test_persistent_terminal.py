from __future__ import annotations

import asyncio
from pathlib import Path

from core.stream_events import AgentEvent
from prompt_toolkit.application.current import set_app
from prompt_toolkit.data_structures import Point
from prompt_toolkit.layout.containers import WritePosition
from prompt_toolkit.layout.mouse_handlers import MouseHandlers
from prompt_toolkit.layout.screen import Screen
from prompt_toolkit.key_binding.key_processor import KeyPress
from prompt_toolkit.keys import Keys
from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType
from prompt_toolkit.output import DummyOutput
from rich.text import Text
from services.permissions.types import PermissionDecision, PermissionOption, PermissionRequest
from services.questions.types import QuestionOption, QuestionRequest
from services.tools.types import ToolCall, ToolCallClassification, ToolDescriptor
from services.tools.types import ToolExecutionResult
from ui.cli.terminal.persistent_app import PersistentTerminalApp
from ui.cli.terminal.interaction_host import TerminalInteractionHost
from ui.cli.terminal.permission_modal import PermissionModal, build_permission_choices
from ui.cli.terminal.question_modal import QuestionModal
from ui.cli.terminal.stream_reducer import reduce_stream_event

from test_cli_commands import make_runtime


def _meta(call_id: str, turn: int, **extra: object) -> dict[str, object]:
    return {
        "assistant_call_id": call_id,
        "model_turn_index": turn,
        **extra,
    }


async def _render(app: PersistentTerminalApp) -> None:
    """Render through the real prompt_toolkit renderer, not just the callback."""

    with set_app(app.app):
        app.app.renderer.render(app.app, app.app.layout)


def test_persistent_renderer_splits_activity_by_model_turn_and_mouse_toggles(
    tmp_path: Path,
) -> None:
    runtime = make_runtime(tmp_path)
    app = PersistentTerminalApp(
        runtime,
        interaction_host=TerminalInteractionHost(),
        on_submit=lambda _text: asyncio.sleep(0),
        on_exit=lambda: asyncio.sleep(0),
        output=DummyOutput(),
    )

    async def events():
        first_text = "我先看第一个文件。"
        yield AgentEvent(
            type="assistant_delta",
            text=first_text,
            metadata=_meta("ac1", 1),
        )
        first_call = ToolCall(
            id="read-1",
            name="read_file",
            input={"file_path": "first.py"},
        )
        yield AgentEvent(
            type="tool_call_ready",
            metadata=_meta("ac1", 1, tool_call=first_call),
        )
        yield AgentEvent(
            type="assistant_message_completed",
            text=first_text,
            metadata=_meta("ac1", 1),
        )
        yield AgentEvent(
            type="tool_result",
            result=ToolExecutionResult(
                tool_call_id=first_call.id,
                tool_name=first_call.name,
                content="ok",
            ),
            metadata=_meta("ac1", 1, tool_call_id=first_call.id),
        )

        second_text = "第一个看完了，我继续看第二个文件。"
        yield AgentEvent(
            type="assistant_delta",
            text=second_text,
            metadata=_meta("ac2", 2),
        )
        second_call = ToolCall(
            id="read-2",
            name="read_file",
            input={"file_path": "second.py"},
        )
        yield AgentEvent(
            type="tool_call_ready",
            metadata=_meta("ac2", 2, tool_call=second_call),
        )
        yield AgentEvent(
            type="assistant_message_completed",
            text=second_text,
            metadata=_meta("ac2", 2),
        )
        yield AgentEvent(
            type="tool_result",
            result=ToolExecutionResult(
                tool_call_id=second_call.id,
                tool_name=second_call.name,
                content="ok",
            ),
            metadata=_meta("ac2", 2, tool_call_id=second_call.id),
        )
        yield AgentEvent(type="completed", text=second_text, metadata=_meta("ac2", 2))

    async def run() -> None:
        app.begin_turn()
        await app.consume_events(events())

    asyncio.run(run())
    fragments = app._render_transcript()  # noqa: SLF001
    rendered = "".join(fragment[1] for fragment in fragments)
    assert rendered.index("我先看第一个文件") < rendered.index("Read first.py")
    assert rendered.index("Read first.py") < rendered.index("第一个看完了，我继续看第二个文件")
    assert rendered.index("第一个看完了，我继续看第二个文件") < rendered.index("Read second.py")
    assert "+1 more" not in rendered

    handlers = [fragment[2] for fragment in fragments if len(fragment) == 3]
    assert len(handlers) == 2, "each model turn must expose its own Activity row"
    event = MouseEvent(
        position=Point(x=2, y=0),
        event_type=MouseEventType.MOUSE_UP,
        button=MouseButton.LEFT,
        modifiers=(),
    )
    handlers[0](event)
    rendered_expanded = "".join(
        fragment[1] for fragment in app._render_transcript()  # noqa: SLF001
    )
    assert "▼" in rendered_expanded
    assert rendered_expanded.count("Read first.py") == 2
    assert rendered_expanded.count("Read second.py") == 1


def test_progress_text_is_rendered_before_its_turn_activity(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    app = PersistentTerminalApp(
        runtime,
        interaction_host=TerminalInteractionHost(),
        on_submit=lambda _text: asyncio.sleep(0),
        on_exit=lambda: asyncio.sleep(0),
        output=DummyOutput(),
    )
    app.begin_turn()
    state = app._active_state  # noqa: SLF001
    assert state is not None

    progress = "好的，我先检查相关实现。"
    reduce_stream_event(
        state,
        AgentEvent(type="assistant_delta", text=progress, metadata=_meta("ac-progress", 1)),
    )
    reduce_stream_event(
        state,
        AgentEvent(
            type="tool_call_ready",
            metadata=_meta(
                "ac-progress",
                1,
                tool_call=ToolCall(
                    id="read-progress",
                    name="read_file",
                    input={"file_path": "target.py"},
                ),
            ),
        ),
    )

    live = "".join(fragment[1] for fragment in app._render_transcript())  # noqa: SLF001
    assert live.index("好的，我先检查相关实现") < live.index("Read target.py")

    completed_progress = AgentEvent(
        type="assistant_message_completed",
        text=progress,
        metadata=_meta("ac-progress", 1),
    )
    app._remember_turn_text(completed_progress, progress)  # noqa: SLF001
    reduce_stream_event(state, completed_progress)

    follow_up = "定位到了，接下来修改对应逻辑。"
    follow_up_event = AgentEvent(
        type="assistant_message_completed",
        text=follow_up,
        metadata=_meta("ac-follow-up", 2),
    )
    app._remember_turn_text(follow_up_event, follow_up)  # noqa: SLF001
    reduce_stream_event(state, follow_up_event)
    reduce_stream_event(
        state,
        AgentEvent(
            type="tool_call_ready",
            metadata=_meta(
                "ac-follow-up",
                2,
                tool_call=ToolCall(
                    id="edit-follow-up",
                    name="edit_file",
                    input={"file_path": "target.py"},
                ),
            ),
        ),
    )

    active = "".join(fragment[1] for fragment in app._render_transcript())  # noqa: SLF001
    assert active.index("好的，我先检查相关实现") < active.index("Read target.py")
    assert active.index("Read target.py") < active.index("定位到了，接下来修改对应逻辑")
    assert active.index("定位到了，接下来修改对应逻辑") < active.index("Edit target.py")

    app._finish_turn()  # noqa: SLF001
    completed = "".join(fragment[1] for fragment in app._render_transcript())  # noqa: SLF001
    assert completed.index("好的，我先检查相关实现") < completed.index("Read target.py")
    assert completed.index("Read target.py") < completed.index("定位到了，接下来修改对应逻辑")
    assert completed.index("定位到了，接下来修改对应逻辑") < completed.index("Edit target.py")


def test_error_event_is_visible_and_committed_as_notice(tmp_path: Path) -> None:
    app = PersistentTerminalApp(
        make_runtime(tmp_path),
        interaction_host=TerminalInteractionHost(),
        on_submit=lambda _text: asyncio.sleep(0),
        on_exit=lambda: asyncio.sleep(0),
        output=DummyOutput(),
    )
    app.begin_turn()
    state = app._active_state  # noqa: SLF001
    assert state is not None
    reduce_stream_event(state, AgentEvent(type="error", text="Provider request failed."))

    active = "".join(fragment[1] for fragment in app._render_transcript())  # noqa: SLF001
    assert "Provider request failed." in active

    app._finish_turn()  # noqa: SLF001
    notices = [
        entry.text
        for entry in app._transcript  # noqa: SLF001
        if entry.kind == "notice"
    ]
    assert notices == ["Provider request failed."]


def test_partial_completed_event_preserves_answer_and_notice(tmp_path: Path) -> None:
    app = PersistentTerminalApp(
        make_runtime(tmp_path),
        interaction_host=TerminalInteractionHost(),
        on_submit=lambda _text: asyncio.sleep(0),
        on_exit=lambda: asyncio.sleep(0),
        output=DummyOutput(),
    )

    async def events():
        yield AgentEvent(
            type="completed",
            text="partial answer",
            metadata=_meta(
                "ac-partial",
                1,
                status="partial",
                notice="Generation was truncated; partial response was preserved.",
            ),
        )

    state = asyncio.run(app.consume_events(events()))

    assert state.error_text == "Generation was truncated; partial response was preserved."
    rendered = "".join(fragment[1] for fragment in app._render_transcript())  # noqa: SLF001
    assert "partial answer" in rendered
    assert "Generation was truncated; partial response was preserved." in rendered


def test_persistent_app_uses_full_screen_shell_and_separates_user_turns(
    tmp_path: Path,
) -> None:
    runtime = make_runtime(tmp_path)
    app = PersistentTerminalApp(
        runtime,
        interaction_host=TerminalInteractionHost(),
        on_submit=lambda _text: asyncio.sleep(0),
        on_exit=lambda: asyncio.sleep(0),
        output=DummyOutput(),
    )

    assert app.app.full_screen is True
    header = "".join(fragment[1] for fragment in app._render_header())  # noqa: SLF001
    assert "Nervure" in header
    assert str(runtime.workspace) in header

    app.append_user("检查一下这个实现\n重点看错误处理")
    transcript = "".join(
        fragment[1] for fragment in app._render_transcript()  # noqa: SLF001
    )
    assert "╭─ You " in transcript
    assert "│ 检查一下这个实现" in transcript
    assert "│ 重点看错误处理" in transcript
    assert "╰──" in transcript
    assert "> 检查一下这个实现" not in transcript

    separator = "".join(
        fragment[1] for fragment in app._render_input_separator()  # noqa: SLF001
    )
    assert " Input " in separator
    assert separator.startswith("─")


def test_persistent_app_mouse_support_is_enabled_and_does_not_use_static_output(
    tmp_path: Path,
) -> None:
    runtime = make_runtime(tmp_path)
    app = PersistentTerminalApp(
        runtime,
        interaction_host=TerminalInteractionHost(),
        on_submit=lambda _text: asyncio.sleep(0),
        on_exit=lambda: asyncio.sleep(0),
        output=DummyOutput(),
    )
    assert app.app.mouse_support()
    assert not app._closing  # noqa: SLF001


def test_persistent_command_renderable_is_captured_into_view_model(
    tmp_path: Path, capsys
) -> None:  # type: ignore[no-untyped-def]
    runtime = make_runtime(tmp_path)
    app = PersistentTerminalApp(
        runtime,
        interaction_host=TerminalInteractionHost(),
        on_submit=lambda _text: asyncio.sleep(0),
        on_exit=lambda: asyncio.sleep(0),
        output=DummyOutput(),
    )
    app.append_renderable(Text("status output"))
    assert "status output" in "".join(
        fragment[1] for fragment in app._render_transcript()  # noqa: SLF001
    )
    assert capsys.readouterr().out == ""


def test_explicit_activity_identity_keeps_semantic_stages_separate(
    tmp_path: Path,
) -> None:
    runtime = make_runtime(tmp_path)
    app = PersistentTerminalApp(
        runtime,
        interaction_host=TerminalInteractionHost(),
        on_submit=lambda _text: asyncio.sleep(0),
        on_exit=lambda: asyncio.sleep(0),
        output=DummyOutput(),
    )

    async def events():
        yield AgentEvent(
            type="tool_call_ready",
            metadata=_meta(
                "ac1",
                1,
                activity_id="inspect",
                activity_title="正在分析项目结构",
                tool_call=ToolCall(id="read-1", name="read_file", input={"file_path": "a.py"}),
            ),
        )
        yield AgentEvent(
            type="tool_call_ready",
            metadata=_meta(
                "ac2",
                2,
                activity_id="validate",
                activity_title="正在运行测试",
                tool_call=ToolCall(id="bash-1", name="bash", input={"command": "pytest -q"}),
            ),
        )

    async def run() -> None:
        await app.consume_events(events())

    asyncio.run(run())
    activities = [entry.activity for entry in app._transcript if entry.activity is not None]  # noqa: SLF001
    assert [(activity.activity_id, activity.title) for activity in activities] == [
        ("inspect", "正在分析项目结构"),
        ("validate", "正在运行测试"),
    ]


def test_multi_turn_transcript_follows_bottom_and_preserves_manual_scroll(
    tmp_path: Path,
) -> None:
    runtime = make_runtime(tmp_path)
    app = PersistentTerminalApp(
        runtime,
        interaction_host=TerminalInteractionHost(),
        on_submit=lambda _text: asyncio.sleep(0),
        on_exit=lambda: asyncio.sleep(0),
        output=DummyOutput(),
    )

    async def turn(call_id: str, turn_index: int, text: str, *, with_tool: bool = False):
        if with_tool:
            tool = ToolCall(
                id=f"read-{turn_index}",
                name="read_file",
                input={"file_path": f"turn-{turn_index}.py"},
            )
            yield AgentEvent(
                type="tool_call_ready",
                metadata=_meta(call_id, turn_index, tool_call=tool),
            )
            yield AgentEvent(
                type="tool_result",
                result=ToolExecutionResult(
                    tool_call_id=tool.id,
                    tool_name=tool.name,
                    content="ok",
                ),
                metadata=_meta(
                    call_id,
                    turn_index,
                    tool_call_id=tool.id,
                ),
            )
        yield AgentEvent(
            type="assistant_delta",
            text=text,
            metadata=_meta(call_id, turn_index),
        )
        yield AgentEvent(
            type="completed",
            text=text,
            metadata=_meta(call_id, turn_index),
        )

    async def run() -> None:
        app.append_user("turn 1")
        await app.consume_events(turn("a1", 1, "assistant-1\n" + "a" * 1200))
        app.append_user("turn 2")
        await app.consume_events(turn("a2", 2, "assistant-2\n" + "b" * 1200, with_tool=True))
        app.append_user("turn 3")
        await app.consume_events(turn("a3", 3, "assistant-3\n" + "c" * 3000))
        await _render(app)

        assert app._transcript  # noqa: SLF001
        assert app._transcript_content_height > app._transcript_viewport_height  # noqa: SLF001
        assert app.follow_bottom is True
        assert app.transcript_vertical_scroll == app._transcript_max_scroll  # noqa: SLF001

        app._scroll_transcript(-3)  # noqa: SLF001
        scrolled_position = app.transcript_vertical_scroll
        assert app.follow_bottom is False
        assert app.user_scrolled is True

        # New content must not yank a user who is reading history back down.
        app.append_user("turn 4")
        await app.consume_events(turn("a4", 4, "assistant-4\n" + "d" * 1200))
        await _render(app)
        assert app.transcript_vertical_scroll == scrolled_position
        assert app.follow_bottom is False
        rendered = "".join(fragment[1] for fragment in app._render_transcript())  # noqa: SLF001
        assert "turn 4" in rendered

        app._scroll_transcript(10000)  # noqa: SLF001
        assert app.follow_bottom is True
        assert app.user_scrolled is False
        assert app.transcript_vertical_scroll == app._transcript_max_scroll  # noqa: SLF001

    asyncio.run(run())


def test_activity_handler_does_not_consume_mouse_wheel(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    app = PersistentTerminalApp(
        runtime,
        interaction_host=TerminalInteractionHost(),
        on_submit=lambda _text: asyncio.sleep(0),
        on_exit=lambda: asyncio.sleep(0),
        output=DummyOutput(),
    )
    async def run() -> None:
        async def events():
            yield AgentEvent(
                type="tool_call_ready",
                metadata=_meta(
                    "ac1",
                    1,
                    tool_call=ToolCall(
                        id="read-1", name="read_file", input={"file_path": "a.py"}
                    ),
                ),
            )

        await app.consume_events(events())

    asyncio.run(run())
    fragments = app._render_transcript()  # noqa: SLF001
    handler = next(fragment[2] for fragment in fragments if len(fragment) == 3)
    event = MouseEvent(
        position=Point(x=1, y=0),
        event_type=MouseEventType.SCROLL_UP,
        button=MouseButton.NONE,
        modifiers=(),
    )
    assert handler(event) is NotImplemented


def test_transcript_pane_routes_wheel_to_viewport_even_on_blank_rows(
    tmp_path: Path,
) -> None:
    runtime = make_runtime(tmp_path)
    app = PersistentTerminalApp(
        runtime,
        interaction_host=TerminalInteractionHost(),
        on_submit=lambda _text: asyncio.sleep(0),
        on_exit=lambda: asyncio.sleep(0),
        output=DummyOutput(),
    )
    for index in range(100):
        app.append_user(f"history {index} " + "x" * 80)

    async def render_pane() -> MouseHandlers:
        handlers = MouseHandlers()
        with set_app(app.app):
            app._transcript_pane.write_to_screen(  # noqa: SLF001
                Screen(),
                handlers,
                WritePosition(xpos=0, ypos=0, width=80, height=30),
                "",
                False,
                None,
            )
        return handlers

    handlers = asyncio.run(render_pane())
    assert app.follow_bottom is True
    before = app.transcript_vertical_scroll
    wheel = MouseEvent(
        position=Point(x=2, y=15),
        event_type=MouseEventType.SCROLL_UP,
        button=MouseButton.NONE,
        modifiers=(),
    )
    handlers.mouse_handlers[15][2](wheel)
    assert app.transcript_vertical_scroll < before
    assert app.follow_bottom is False
    assert app.user_scrolled is True


def test_live_activity_mouse_toggle_survives_rerender_and_completion(
    tmp_path: Path,
) -> None:
    runtime = make_runtime(tmp_path)
    app = PersistentTerminalApp(
        runtime,
        interaction_host=TerminalInteractionHost(),
        on_submit=lambda _text: asyncio.sleep(0),
        on_exit=lambda: asyncio.sleep(0),
        output=DummyOutput(),
    )
    app.begin_turn()
    state = app._active_state  # noqa: SLF001
    assert state is not None
    call = ToolCall(id="read-live", name="read_file", input={"file_path": "live.py"})
    reduce_stream_event(
        state,
        AgentEvent(type="tool_call_ready", metadata=_meta("ac-live", 7, tool_call=call)),
    )

    fragments = app._render_transcript()  # noqa: SLF001
    collapsed = "".join(fragment[1] for fragment in fragments)
    assert "Read live.py" in collapsed
    assert "1 tool" not in collapsed
    handler = next(fragment[2] for fragment in fragments if len(fragment) == 3)
    live_group = app._semantic_groups(state)[0]  # noqa: SLF001
    handler(
        MouseEvent(
            position=Point(x=2, y=0),
            event_type=MouseEventType.MOUSE_UP,
            button=MouseButton.LEFT,
            modifiers=(),
        )
    )

    rerendered = "".join(
        fragment[1] for fragment in app._render_transcript()  # noqa: SLF001
    )
    assert live_group.expanded is True
    assert "▼" in rerendered
    assert "Read live.py" in rerendered

    reduce_stream_event(
        state,
        AgentEvent(
            type="tool_result",
            result=ToolExecutionResult(
                tool_call_id=call.id,
                tool_name=call.name,
                content="ok",
            ),
            metadata=_meta("ac-live", 7, tool_call_id=call.id),
        ),
    )
    app._finish_turn()  # noqa: SLF001

    completed_group = next(
        entry.activity
        for entry in app._transcript  # noqa: SLF001
        if entry.activity is not None
    )
    assert completed_group is live_group
    assert completed_group.expanded is True
    completed = "".join(
        fragment[1] for fragment in app._render_transcript()  # noqa: SLF001
    )
    assert "▼" in completed
    assert "Read live.py" in completed


def test_ctrl_o_toggles_stable_live_activity_objects(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    app = PersistentTerminalApp(
        runtime,
        interaction_host=TerminalInteractionHost(),
        on_submit=lambda _text: asyncio.sleep(0),
        on_exit=lambda: asyncio.sleep(0),
        output=DummyOutput(),
    )
    app.begin_turn()
    state = app._active_state  # noqa: SLF001
    assert state is not None
    reduce_stream_event(
        state,
        AgentEvent(
            type="tool_call_ready",
            metadata=_meta(
                "ac-inspect",
                1,
                activity_id="inspect",
                activity_title="Inspect",
                tool_call=ToolCall(
                    id="inspect-read",
                    name="read_file",
                    input={"file_path": "inspect.py"},
                ),
            ),
        ),
    )
    reduce_stream_event(
        state,
        AgentEvent(
            type="tool_call_ready",
            metadata=_meta(
                "ac-test",
                2,
                activity_id="test",
                activity_title="Test",
                tool_call=ToolCall(
                    id="test-bash",
                    name="bash",
                    input={"command": "pytest -q"},
                ),
            ),
        ),
    )

    groups = app._semantic_groups(state)  # noqa: SLF001
    assert len(groups) == 2
    app.toggle_all_activities()
    assert all(group.expanded for group in groups)
    expanded = "".join(
        fragment[1] for fragment in app._render_transcript()  # noqa: SLF001
    )
    assert "Read inspect.py" in expanded
    assert 'Bash "pytest -q"' in expanded

    app.toggle_all_activities()
    assert all(not group.expanded for group in groups)
    collapsed = "".join(
        fragment[1] for fragment in app._render_transcript()  # noqa: SLF001
    )
    assert "▼" not in collapsed


def test_interaction_pane_is_above_input_not_inside_transcript_and_keeps_focus(
    tmp_path: Path,
) -> None:
    runtime = make_runtime(tmp_path)
    host = TerminalInteractionHost()
    app = PersistentTerminalApp(
        runtime,
        interaction_host=host,
        on_submit=lambda _text: asyncio.sleep(0),
        on_exit=lambda: asyncio.sleep(0),
        output=DummyOutput(),
    )
    app.append_user("history marker")
    loop = asyncio.new_event_loop()
    try:
        host.active_question = QuestionModal(
            questions=(
                QuestionRequest(
                    question="Choose a route",
                    header="Route",
                    options=(QuestionOption("A"), QuestionOption("B")),
                ),
            ),
            future=loop.create_future(),
        )
        question_body = str(app._render_interaction())  # noqa: SLF001
        transcript = "".join(
            fragment[1] for fragment in app._render_transcript()  # noqa: SLF001
        )
        assert "Choose a route" in question_body
        assert "Choose a route" not in transcript
        assert "history marker" in transcript
        assert app.app.layout.current_window is app._input_window  # noqa: SLF001

        def _handler(_tool_input, _runtime):  # type: ignore[no-untyped-def]
            return ToolExecutionResult(tool_call_id="permission", tool_name="bash", content="ok")

        descriptor = ToolDescriptor(
            name="bash",
            description="test permission",
            input_schema={"type": "object"},
            handler=_handler,
        )
        request = PermissionRequest(
            request_id="permission",
            tool_call=ToolCall(id="permission", name="bash", input={"command": "echo ok"}),
            descriptor=descriptor,
            classification=ToolCallClassification(),
            decision=PermissionDecision(action="ask", reason="test", source="test"),
            tool_input={"command": "echo ok"},
            options=(
                PermissionOption(id="once", label="Allow once", action="allow", scope="once"),
                PermissionOption(id="session", label="Allow session", action="allow", scope="session"),
                PermissionOption(id="deny", label="Deny", action="deny", scope="once"),
            ),
        )
        host.active_question = None
        host.active_permission = PermissionModal(
            request=request,
            choices=build_permission_choices(request),
            future=loop.create_future(),
        )
        permission_body = str(app._render_interaction())  # noqa: SLF001
        transcript = "".join(
            fragment[1] for fragment in app._render_transcript()  # noqa: SLF001
        )
        assert "Do you want to proceed?" in permission_body
        assert "Do you want to proceed?" not in transcript
        assert app.app.layout.current_window is app._input_window  # noqa: SLF001
    finally:
        host.active_question = None
        host.active_permission = None
        loop.close()


def test_persistent_assistant_uses_markdown_renderer(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    app = PersistentTerminalApp(
        runtime,
        interaction_host=TerminalInteractionHost(),
        on_submit=lambda _text: asyncio.sleep(0),
        on_exit=lambda: asyncio.sleep(0),
        output=DummyOutput(),
    )
    app.append_replay_messages(
        [
            {
                "role": "assistant",
                "content": "# Heading\n\n**bold**\n\n```python\nx = 1\n```",
            }
        ]
    )

    fragments = app._render_transcript()  # noqa: SLF001
    rendered = "".join(fragment[1] for fragment in fragments)
    assert "Nervure>" in rendered
    assert "Heading" in rendered
    assert "bold" in rendered
    assert "x = 1" in rendered
    assert "**bold**" not in rendered
    assert "```" not in rendered


def test_choice_hitl_enter_wins_over_main_input_binding(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime = make_runtime(tmp_path)
        host = TerminalInteractionHost()
        app = PersistentTerminalApp(
            runtime,
            interaction_host=host,
            on_submit=lambda _text: asyncio.sleep(0),
            on_exit=lambda: asyncio.sleep(0),
            output=DummyOutput(),
        )
        modal = QuestionModal(
            questions=(
                QuestionRequest(
                    question="Choose a route",
                    header="Route",
                    options=(QuestionOption("A"), QuestionOption("B")),
                ),
            ),
            future=asyncio.get_running_loop().create_future(),
        )
        host.active_question = modal
        with set_app(app.app):
            app.app.key_processor.feed(KeyPress(Keys.Enter))
            app.app.key_processor.process_keys()
            await asyncio.sleep(0)

        assert modal.future.done()
        response = modal.future.result()
        assert response.answers[0].answer == "A"
        assert host.active_question is None

    asyncio.run(scenario())
