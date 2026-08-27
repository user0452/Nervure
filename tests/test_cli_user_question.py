from __future__ import annotations

import asyncio

from services.questions.types import QuestionOption, QuestionRequest
from services.tools.types import ToolRuntime
from tools.ask_user_question import descriptor as ask_user_question_descriptor
from ui.cli.terminal.interaction_host import TerminalInteractionHost
from ui.cli.terminal.question_prompt import TtyUserQuestionPrompter


class _RunningApp:
    is_running = True

    def invalidate(self) -> None:
        pass


def test_tty_question_waits_for_keyboard_choice_and_returns_selected_option() -> None:
    async def scenario() -> str:
        host = TerminalInteractionHost()
        host._active_app = _RunningApp()  # type: ignore[assignment]
        prompter = TtyUserQuestionPrompter(host)
        question = QuestionRequest(
            question="Choose a mode",
            header="Mode",
            options=(QuestionOption("Safe"), QuestionOption("Fast")),
        )
        pending = asyncio.create_task(prompter.ask_questions((question,)))
        while host.active_question is None:
            await asyncio.sleep(0)
        assert not pending.done()
        assert host.handle_key("down")
        assert host.handle_key("enter")
        response = await pending
        assert host.active_question is None
        return str(response.answers[0].answer)

    assert asyncio.run(scenario()) == "Fast"


def test_tty_question_supports_multi_select_and_escape_decline() -> None:
    async def scenario() -> tuple[tuple[str, ...], bool]:
        host = TerminalInteractionHost()
        host._active_app = _RunningApp()  # type: ignore[assignment]
        prompter = TtyUserQuestionPrompter(host)
        question = QuestionRequest(
            question="Choose features",
            header="Features",
            options=(QuestionOption("One"), QuestionOption("Two")),
            multi_select=True,
        )
        pending = asyncio.create_task(prompter.ask_questions((question,)))
        while host.active_question is None:
            await asyncio.sleep(0)
        host.handle_key("space")
        host.handle_key("down")
        host.handle_key("space")
        host.handle_key("enter")
        response = await pending
        assert response.answers[0].answer == ("One", "Two")

        pending = asyncio.create_task(prompter.ask_questions((question,)))
        while host.active_question is None:
            await asyncio.sleep(0)
        host.handle_key("cancel")
        declined = await pending
        return response.answers[0].answer, declined.declined

    assert asyncio.run(scenario()) == (("One", "Two"), True)


def test_ask_user_question_tool_suspends_runtime_until_tty_answer() -> None:
    async def scenario() -> tuple[str, object]:
        from core.runtime_state import InteractionKind, RuntimeState

        state = RuntimeState()
        host = TerminalInteractionHost()
        host._active_app = _RunningApp()  # type: ignore[assignment]
        descriptor = ask_user_question_descriptor(TtyUserQuestionPrompter(host))
        pending = asyncio.create_task(
            descriptor.handler(
                {
                    "questions": [
                        {
                            "question": "Which path?",
                            "header": "Path",
                            "options": [
                                {"label": "A", "description": "first"},
                                {"label": "B", "description": "second"},
                            ],
                        }
                    ]
                },
                ToolRuntime(state=state, tool_call_id="question-1"),
            )
        )
        while host.active_question is None:
            await asyncio.sleep(0)
        assert state.interaction is not None
        assert state.interaction.kind is InteractionKind.ASK_USER
        host.handle_key("down")
        host.handle_key("enter")
        result = await pending
        assert state.interaction is None
        return result.content, result.metadata["status"]

    content, status = asyncio.run(scenario())
    assert status == "answered"
    assert '"answer": "B"' in content
