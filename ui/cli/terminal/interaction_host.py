"""Shared transient interaction host for the inline terminal UI."""

from __future__ import annotations

import asyncio
from typing import Callable

from prompt_toolkit import Application
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.filters import Condition
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension

from services.permissions import PermissionRequest, PermissionResponse
from ui.cli.terminal.permission_modal import (
    PermissionModal,
    build_permission_choices,
    denied_response,
    render_permission_modal_ansi,
    render_permission_status_fragments,
)
from ui.cli.terminal.plan_review_modal import (
    PlanReviewModal,
    PlanReviewResponse,
    render_plan_review_modal_ansi,
    render_plan_review_status_fragments,
)
from services.questions.types import QuestionRequest, QuestionResponse
from ui.cli.terminal.question_modal import (
    QuestionModal,
    render_question_modal_ansi,
    render_question_status_fragments,
)


class TerminalInteractionHost:
    """Owns transient modals that must disappear after a user decision."""

    def __init__(self) -> None:
        self.active_permission: PermissionModal | None = None
        self.active_question: QuestionModal | None = None
        self.active_plan_review: PlanReviewModal | None = None
        self._active_app: Application[None] | None = None

    async def request_permission(
        self,
        request: PermissionRequest,
    ) -> PermissionResponse:
        loop = asyncio.get_running_loop()
        modal = PermissionModal(
            request=request,
            choices=build_permission_choices(request),
            future=loop.create_future(),
        )
        self.active_permission = modal
        self.invalidate()
        if self._active_app is None or not self._active_app.is_running:
            app = self._build_standalone_app()
            await app.run_async()
        try:
            return await modal.future
        finally:
            if self.active_permission is modal:
                self.active_permission = None
            self.invalidate()

    async def request_user_question(
        self,
        questions: tuple[QuestionRequest, ...],
    ) -> QuestionResponse:
        if not questions:
            return QuestionResponse(declined=True, feedback="no_questions")
        loop = asyncio.get_running_loop()
        modal = QuestionModal(questions=questions, future=loop.create_future())
        self.active_question = modal
        self.invalidate()
        if self._active_app is None or not self._active_app.is_running:
            app = self._build_standalone_app()
            await app.run_async()
        try:
            return await modal.future
        finally:
            if self.active_question is modal:
                self.active_question = None
            self.invalidate()

    async def request_plan_review(
        self,
        *,
        summary: str = "",
        plan_path: str = "",
    ) -> PlanReviewResponse:
        loop = asyncio.get_running_loop()
        modal = PlanReviewModal(
            future=loop.create_future(),
            summary=summary,
            plan_path=plan_path,
        )
        self.active_plan_review = modal
        self.invalidate()
        if self._active_app is None or not self._active_app.is_running:
            app = self._build_standalone_app()
            await app.run_async()
        try:
            return await modal.future
        finally:
            if self.active_plan_review is modal:
                self.active_plan_review = None
            self.invalidate()

    def bind_app(self, app: Application[None]) -> None:
        self._active_app = app

    def unbind_app(self, app: Application[None]) -> None:
        if self._active_app is app:
            self._active_app = None

    def invalidate(self) -> None:
        app = self._active_app
        if app is not None and app.is_running:
            app.invalidate()

    def is_active(self) -> bool:
        return (
            self.active_permission is not None
            or self.active_question is not None
            or self.active_plan_review is not None
        )

    def handle_key(self, key: str) -> bool:
        question = self.active_question
        if question is not None:
            if question.expects_text:
                if key == "cancel":
                    self._complete_question(
                        QuestionResponse(declined=True, feedback="interrupted")
                    )
                    return True
                return False
            if key == "up":
                question.move(-1)
                self.invalidate()
                return True
            if key == "down":
                question.move(1)
                self.invalidate()
                return True
            if key == "space":
                question.toggle()
                self.invalidate()
                return True
            if key == "enter":
                response = question.advance()
                if response is not None:
                    self._complete_question(response)
                else:
                    self.invalidate()
                return True
            if key == "cancel":
                self._complete_question(
                    QuestionResponse(declined=True, feedback="interrupted")
                )
                return True
            return False

        modal = self.active_permission
        if modal is not None:
            if key == "up":
                modal.move(-1)
                self.invalidate()
                return True
            if key == "down":
                modal.move(1)
                self.invalidate()
                return True
            if key in {"1", "2", "3"}:
                modal.choose_index(int(key) - 1)
                self._complete(modal.selected.response)
                return True
            if key == "enter":
                self._complete(modal.selected.response)
                return True
            if key == "cancel":
                self._complete(denied_response(interrupted=True))
                return True
            return False

        plan_review = self.active_plan_review
        if plan_review is None:
            return False
        if key == "cancel":
            response = plan_review.cancel()
            if response is not None:
                self._complete_plan_review(response)
            else:
                self.invalidate()
            return True
        if plan_review.accepting_feedback:
            return False
        if key == "up":
            plan_review.move(-1)
            self.invalidate()
            return True
        if key == "down":
            plan_review.move(1)
            self.invalidate()
            return True
        if key in {"1", "2", "3"}:
            plan_review.choose_index(int(key) - 1)
            response = plan_review.activate()
            if response is not None:
                self._complete_plan_review(response)
            else:
                self.invalidate()
            return True
        if key == "enter":
            response = plan_review.activate()
            if response is not None:
                self._complete_plan_review(response)
            else:
                self.invalidate()
            return True
        return False

    def text_input_active(self) -> bool:
        question = self.active_question
        if question is not None and question.expects_text:
            return True
        plan_review = self.active_plan_review
        return bool(plan_review is not None and plan_review.accepting_feedback)

    def submit_text(self, text: str) -> bool:
        """Route persistent input-box text into an active free-text interaction."""

        value = text.strip()
        question = self.active_question
        if question is not None and question.expects_text:
            if not value:
                return True
            response = question.advance_text(value)
            if response is not None:
                self._complete_question(response)
            else:
                self.invalidate()
            return True

        plan_review = self.active_plan_review
        if plan_review is not None and plan_review.accepting_feedback:
            if not value:
                return True
            self._complete_plan_review(
                PlanReviewResponse(action="modify", feedback=value)
            )
            return True
        return False

    def render_body(self, *, width: int):
        question = self.active_question
        if question is not None:
            return render_question_modal_ansi(question, width=width)
        modal = self.active_permission
        if modal is not None:
            return render_permission_modal_ansi(modal, width=width)
        plan_review = self.active_plan_review
        if plan_review is not None:
            return render_plan_review_modal_ansi(plan_review, width=width)
        return None

    def render_status(self):
        question = self.active_question
        if question is not None:
            return render_question_status_fragments(question)
        modal = self.active_permission
        if modal is not None:
            return render_permission_status_fragments(modal)
        plan_review = self.active_plan_review
        if plan_review is not None:
            return render_plan_review_status_fragments(plan_review)
        return None

    def _complete(self, response: PermissionResponse) -> None:
        modal = self.active_permission
        if modal is None:
            return
        if not modal.future.done():
            modal.future.set_result(response)
        self.active_permission = None
        self.invalidate()

    def _complete_question(self, response: QuestionResponse) -> None:
        modal = self.active_question
        if modal is None:
            return
        if not modal.future.done():
            modal.future.set_result(response)
        self.active_question = None
        self.invalidate()

    def _complete_plan_review(self, response: PlanReviewResponse) -> None:
        modal = self.active_plan_review
        if modal is None:
            return
        if not modal.future.done():
            modal.future.set_result(response)
        self.active_plan_review = None
        self.invalidate()

    def _build_standalone_app(self) -> Application[None]:
        bindings = self.key_bindings(fallback_cancel=None, exit_on_complete=True)

        def body_text():  # type: ignore[no-untyped-def]
            try:
                width = app.output.get_size().columns  # type: ignore[union-attr]
            except Exception:
                width = 80
            body = self.render_body(width=width)
            return body if body is not None else ""

        def status_text():  # type: ignore[no-untyped-def]
            status = self.render_status()
            if status is not None:
                return status
            return FormattedText([("class:stream-status", "")])

        body = Window(
            content=FormattedTextControl(body_text),
            height=Dimension(min=1),
            wrap_lines=True,
        )
        status = Window(
            height=Dimension(min=1, max=1),
            content=FormattedTextControl(status_text),
        )
        app: Application[None] = Application(
            layout=Layout(HSplit([body, status])),
            full_screen=False,
            erase_when_done=True,
            mouse_support=False,
            key_bindings=bindings,
        )

        original_run_async = app.run_async

        async def run_async_with_binding(*args, **kwargs):  # type: ignore[no-untyped-def]
            self.bind_app(app)
            try:
                return await original_run_async(*args, **kwargs)
            finally:
                self.unbind_app(app)

        app.run_async = run_async_with_binding  # type: ignore[method-assign]
        return app

    def key_bindings(
        self,
        *,
        fallback_cancel: Callable[[object], None] | None,
        exit_on_complete: bool = False,
    ) -> KeyBindings:
        bindings = KeyBindings()
        modal_active = Condition(self.is_active)
        choice_active = Condition(
            lambda: (
                self.active_permission is not None
                or (
                    self.active_question is not None
                    and not self.active_question.expects_text
                )
                or (
                    self.active_plan_review is not None
                    and not self.active_plan_review.accepting_feedback
                )
            )
        )

        def complete(event, key: str) -> None:  # type: ignore[no-untyped-def]
            if self.handle_key(key) and exit_on_complete and not self.is_active():
                event.app.exit()

        @bindings.add(Keys.Up, eager=True, filter=choice_active)
        def _on_up(event) -> None:  # type: ignore[no-untyped-def]
            self.handle_key("up")

        @bindings.add(Keys.Down, eager=True, filter=choice_active)
        def _on_down(event) -> None:  # type: ignore[no-untyped-def]
            self.handle_key("down")

        @bindings.add("1", eager=True, filter=choice_active)
        def _on_one(event) -> None:  # type: ignore[no-untyped-def]
            complete(event, "1")

        @bindings.add("2", eager=True, filter=choice_active)
        def _on_two(event) -> None:  # type: ignore[no-untyped-def]
            complete(event, "2")

        @bindings.add("3", eager=True, filter=choice_active)
        def _on_three(event) -> None:  # type: ignore[no-untyped-def]
            complete(event, "3")

        @bindings.add(Keys.Enter, eager=True, filter=choice_active)
        def _on_enter(event) -> None:  # type: ignore[no-untyped-def]
            complete(event, "enter")

        @bindings.add(
            " ",
            eager=True,
            filter=Condition(
                lambda: self.active_question is not None
                and not self.active_question.expects_text
            ),
        )
        def _on_space(event) -> None:  # type: ignore[no-untyped-def]
            self.handle_key("space")

        @bindings.add(Keys.Escape, eager=True, filter=modal_active)
        @bindings.add(Keys.ControlC, eager=True, filter=modal_active)
        def _on_cancel(event) -> None:  # type: ignore[no-untyped-def]
            if self.handle_key("cancel"):
                if exit_on_complete and not self.is_active():
                    event.app.exit()
                return
            if fallback_cancel is not None:
                fallback_cancel(event)

        return bindings


__all__ = ["TerminalInteractionHost"]
