"""Transient TTY state and rendering for ``ask_user_question``."""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass, field

from prompt_toolkit.formatted_text import ANSI, FormattedText
from rich.console import Console
from rich.text import Text

from services.questions.types import (
    AnswerRecord,
    QuestionRequest,
    QuestionResponse,
)
from ui.cli.theme import RICH_THEME


@dataclass
class QuestionModal:
    questions: tuple[QuestionRequest, ...]
    future: asyncio.Future[QuestionResponse]
    question_index: int = 0
    selected_index: int = 0
    selected_indices: set[int] = field(default_factory=set)
    completed_answers: tuple[AnswerRecord, ...] = ()

    @property
    def current(self) -> QuestionRequest:
        return self.questions[self.question_index]

    @property
    def expects_text(self) -> bool:
        return not self.current.options

    def move(self, delta: int) -> None:
        options = self.current.options
        if options:
            self.selected_index = (self.selected_index + delta) % len(options)

    def toggle(self) -> None:
        if not self.current.multi_select or not self.current.options:
            return
        if self.selected_index in self.selected_indices:
            self.selected_indices.remove(self.selected_index)
        else:
            self.selected_indices.add(self.selected_index)

    def advance(self) -> QuestionResponse | None:
        question = self.current
        if not question.options:
            return None
        if question.multi_select:
            selected = sorted(self.selected_indices) or [self.selected_index]
            answer: str | tuple[str, ...] = tuple(
                question.options[index].label for index in selected
            )
        else:
            answer = question.options[self.selected_index].label
        return self._commit_answer(answer)

    def advance_text(self, text: str) -> QuestionResponse | None:
        if not self.expects_text:
            return None
        value = text.strip()
        if not value:
            return None
        return self._commit_answer(value)

    def _commit_answer(self, answer: str | tuple[str, ...]) -> QuestionResponse | None:
        question = self.current
        current_answer = AnswerRecord(question=question.question, answer=answer)
        if self.question_index + 1 < len(self.questions):
            self.completed_answers = (*self.completed_answers, current_answer)
            self.question_index += 1
            self.selected_index = 0
            self.selected_indices.clear()
            return None
        return QuestionResponse(answers=(*self.completed_answers, current_answer))


def render_question_modal_ansi(modal: QuestionModal, *, width: int) -> ANSI:
    out = io.StringIO()
    console = Console(
        file=out,
        force_terminal=True,
        color_system="standard",
        width=max(width, 20),
        theme=RICH_THEME,
    )
    question = modal.current
    console.print(Text(question.header or "需要确认", style="nervure.metric"))
    console.print(Text(question.question, style="nervure.permission"))
    console.print()

    if modal.expects_text:
        console.print(Text("在下方输入框直接回答。", style="nervure.subtle"))
        console.print()
        console.print(Text("Enter 确认 · Esc 取消", style="nervure.subtle"))
        return ANSI(out.getvalue())

    for index, option in enumerate(question.options):
        focused = index == modal.selected_index
        checked = index in modal.selected_indices
        marker = "▶" if focused else " "
        box = "●" if checked else "○"
        prefix = f"{marker} {box} " if question.multi_select else f"{marker} "
        style = "nervure.permission" if focused else "nervure.metric"
        console.print(Text(f"{prefix}{option.label}", style=style))
        if option.description:
            console.print(Text(f"    {option.description}", style="nervure.subtle"))
    console.print()
    footer = "↑↓ 选择 · Enter 确认 · Esc 取消"
    if question.multi_select:
        footer = "↑↓ 选择 · Space 多选 · " + footer[footer.index("Enter"):]
    console.print(Text(footer, style="nervure.subtle"))
    return ANSI(out.getvalue())


def render_question_status_fragments(modal: QuestionModal) -> FormattedText:
    if modal.expects_text:
        hint = "输入回答, Enter, Esc"
    else:
        hint = "↑↓, Enter, Esc"
    return FormattedText(
        [
            ("class:stream-prefix", "Nervure> "),
            (
                "class:stream-status",
                f"question {modal.question_index + 1}/{len(modal.questions)}  ({hint})",
            ),
        ]
    )


__all__ = ["QuestionModal", "render_question_modal_ansi", "render_question_status_fragments"]
