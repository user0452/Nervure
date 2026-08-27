"""TTY adapter for the structured user-question tool."""

from __future__ import annotations

from services.questions.types import QuestionRequest, QuestionResponse
from ui.cli.terminal.interaction_host import TerminalInteractionHost


class TtyUserQuestionPrompter:
    def __init__(self, interaction_host: TerminalInteractionHost) -> None:
        self._interaction_host = interaction_host

    async def ask_questions(
        self,
        questions: tuple[QuestionRequest, ...],
    ) -> QuestionResponse:
        return await self._interaction_host.request_user_question(questions)


__all__ = ["TtyUserQuestionPrompter"]
