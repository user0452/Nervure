"""Transient TTY state and rendering for plan approval/revision."""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
from typing import Literal

from prompt_toolkit.formatted_text import ANSI, FormattedText
from rich.console import Console
from rich.text import Text

from ui.cli.theme import RICH_THEME


PlanReviewAction = Literal["approve", "modify", "reject"]


@dataclass(frozen=True)
class PlanReviewResponse:
    action: PlanReviewAction
    feedback: str = ""


@dataclass
class PlanReviewModal:
    future: asyncio.Future[PlanReviewResponse]
    summary: str = ""
    plan_path: str = ""
    selected_index: int = 0
    accepting_feedback: bool = False

    @property
    def actions(self) -> tuple[PlanReviewAction, ...]:
        return ("approve", "modify", "reject")

    def move(self, delta: int) -> None:
        if self.accepting_feedback:
            return
        self.selected_index = (self.selected_index + delta) % len(self.actions)

    def choose_index(self, index: int) -> None:
        if self.accepting_feedback:
            return
        if 0 <= index < len(self.actions):
            self.selected_index = index

    def activate(self) -> PlanReviewResponse | None:
        action = self.actions[self.selected_index]
        if action == "modify":
            self.accepting_feedback = True
            return None
        return PlanReviewResponse(action=action)

    def cancel(self) -> PlanReviewResponse | None:
        if self.accepting_feedback:
            self.accepting_feedback = False
            return None
        return PlanReviewResponse(action="reject")


_LABELS: tuple[tuple[str, str], ...] = (
    ("Approve and implement", "Exit Plan mode and start implementation"),
    ("Request changes", "Send revision feedback and keep Plan mode active"),
    ("Reject for now", "Stay in Plan mode without continuing automatically"),
)


def render_plan_review_modal_ansi(modal: PlanReviewModal, *, width: int) -> ANSI:
    out = io.StringIO()
    console = Console(
        file=out,
        force_terminal=True,
        color_system="standard",
        width=max(width, 20),
        theme=RICH_THEME,
    )
    console.print(Text("Plan ready", style="nervure.metric"))
    if modal.summary:
        console.print(Text(modal.summary, style="nervure.permission"))
    if modal.plan_path:
        console.print(Text(modal.plan_path, style="nervure.subtle"))
    console.print()

    if modal.accepting_feedback:
        console.print(Text("Describe the changes you want in the input below.", style="nervure.permission"))
        console.print()
        console.print(Text("Enter 提交修改意见 · Esc 返回", style="nervure.subtle"))
        return ANSI(out.getvalue())

    for index, (label, detail) in enumerate(_LABELS):
        focused = index == modal.selected_index
        marker = "▶" if focused else " "
        style = "nervure.permission" if focused else "nervure.metric"
        console.print(Text(f"{marker} {label}", style=style))
        console.print(Text(f"    {detail}", style="nervure.subtle"))
    console.print()
    console.print(Text("↑↓ 选择 · Enter 确认 · Esc 暂不执行", style="nervure.subtle"))
    return ANSI(out.getvalue())


def render_plan_review_status_fragments(modal: PlanReviewModal) -> FormattedText:
    if modal.accepting_feedback:
        status = "plan review  (输入修改意见 · Enter 提交 · Esc 返回)"
    else:
        status = "plan review  (↑↓, Enter, Esc)"
    return FormattedText(
        [
            ("class:stream-prefix", "Nervure> "),
            ("class:stream-status", status),
        ]
    )


__all__ = [
    "PlanReviewAction",
    "PlanReviewModal",
    "PlanReviewResponse",
    "render_plan_review_modal_ansi",
    "render_plan_review_status_fragments",
]
