from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from core.context_engine import ContextEngine, StaticPromptAssembler
from core.loop import AgentLoop
from core.runtime_state import InteractionKind, PermissionMode, RuntimeState
from services.context.message_store import MessageStore
from services.model.stream import ModelStreamEvent
from services.permissions import PermissionPolicy, PermissionResponse, SessionPermissionStore
from services.plans import PlanStore
from services.questions.types import AnswerRecord, QuestionResponse
from services.tools.executor import RegistryToolExecutor, ToolExecutionUpdate
from services.tools.registry import ToolRegistry
from services.tools.types import (
    ToolCall,
    ToolCallClassification,
    ToolDescriptor,
    ToolExecutionResult,
    ToolRuntime,
    ToolTarget,
)
from tools.ask_user_question import descriptor as ask_user_question_descriptor
from tools.exit_plan_mode import descriptor as exit_plan_mode_descriptor
from ui.cli.commands import CommandInvocation, _permissions
from ui.cli.terminal.stream_session import StreamingSession


def _descriptor(name: str = "bash") -> ToolDescriptor:
    def handler(tool_input, runtime):
        return ToolExecutionResult(tool_call_id="", tool_name=name, content="ok")

    return ToolDescriptor(
        name=name,
        description=name,
        input_schema={"type": "object"},
        handler=handler,
    )


def test_runtime_suspend_and_resume_are_first_class() -> None:
    state = RuntimeState()

    interaction = state.suspend(
        InteractionKind.ASK_USER,
        interaction_id="question-1",
        payload={"question_count": 2},
    )

    assert state.is_suspended() is True
    assert interaction.kind == InteractionKind.ASK_USER
    assert state.resume(interaction_id="wrong") is None
    assert state.is_suspended() is True
    assert state.resume(interaction_id="question-1") == interaction
    assert state.is_suspended() is False


def test_full_access_survives_new_session_reset() -> None:
    state = RuntimeState(permission_mode=PermissionMode.FULL_ACCESS)

    state.start_new_session()

    assert state.permission_mode == PermissionMode.FULL_ACCESS
    assert state.interaction is None


def test_permissions_mode_command_switches_normal_and_full_access() -> None:
    state = RuntimeState()
    runtime = SimpleNamespace(state=state)

    _permissions(
        runtime,
        CommandInvocation(
            raw="/permissions mode full",
            name="permissions",
            args=("mode", "full"),
            arg_text="mode full",
        ),
    )
    assert state.permission_mode == PermissionMode.FULL_ACCESS

    _permissions(
        runtime,
        CommandInvocation(
            raw="/permissions mode normal",
            name="permissions",
            args=("mode", "normal"),
            arg_text="mode normal",
        ),
    )
    assert state.permission_mode == PermissionMode.DEFAULT


def test_full_access_bypasses_ask_but_not_hard_deny() -> None:
    store = SessionPermissionStore()
    policy = PermissionPolicy(store)
    descriptor = _descriptor("bash")
    classification = ToolCallClassification(
        read_only=False,
        modifies_filesystem=True,
        concurrency_safe=False,
        targets=(ToolTarget(kind="command", operation="execute", value="npm install"),),
    )
    call = ToolCall(id="call-1", name="bash", input={})

    normal = RuntimeState(permission_mode=PermissionMode.DEFAULT)
    normal_decision = policy.evaluate(
        tool_call=call,
        descriptor=descriptor,
        classification=classification,
        guard_policies=(),
        state=normal,
    )
    assert normal_decision.action == "ask"

    full = RuntimeState(permission_mode=PermissionMode.FULL_ACCESS)
    full_decision = policy.evaluate(
        tool_call=call,
        descriptor=descriptor,
        classification=classification,
        guard_policies=(),
        state=full,
    )
    assert full_decision.action == "allow"
    assert full_decision.source == "full_access"

    store.deny_tool("bash")
    denied = policy.evaluate(
        tool_call=call,
        descriptor=descriptor,
        classification=classification,
        guard_policies=(),
        state=full,
    )
    assert denied.action == "deny"


def test_permission_prompt_marks_runtime_suspended_while_waiting() -> None:
    state = RuntimeState()
    classification = ToolCallClassification(
        read_only=False,
        modifies_filesystem=True,
        concurrency_safe=False,
        targets=(ToolTarget(kind="command", operation="execute", value="npm install"),),
    )

    def handler(tool_input, runtime):
        return ToolExecutionResult(tool_call_id="", tool_name="bash", content="ok")

    def classify(tool_input, runtime):
        return classification

    descriptor = ToolDescriptor(
        name="bash",
        description="bash",
        input_schema={"type": "object"},
        handler=handler,
        classify_input=classify,
    )
    policy = PermissionPolicy(SessionPermissionStore())
    seen: list[InteractionKind | None] = []

    class Prompter:
        async def request_permission(self, request):
            seen.append(state.interaction.kind if state.interaction else None)
            return PermissionResponse(action="allow", scope="once")

    executor = RegistryToolExecutor(
        ToolRegistry((descriptor,), permission_policy=policy),
        permission_policy=policy,
        permission_prompter=Prompter(),
    )

    async def collect():
        return [
            update
            async for update in executor.execute(
                (ToolCall(id="permission-call", name="bash", input={}),),
                state,
            )
        ]

    updates = asyncio.run(collect())

    assert seen == [InteractionKind.PERMISSION]
    assert state.interaction is None
    assert updates[-1].result is not None
    assert updates[-1].result.content == "ok"


def test_ask_user_question_marks_runtime_suspended_while_waiting() -> None:
    state = RuntimeState()
    seen: list[InteractionKind | None] = []

    class Prompter:
        async def ask_questions(self, questions):
            seen.append(state.interaction.kind if state.interaction else None)
            question = questions[0]
            return QuestionResponse(
                answers=(
                    AnswerRecord(
                        question=question.question,
                        answer=question.options[0].label,
                    ),
                )
            )

    descriptor = ask_user_question_descriptor(Prompter())
    runtime = ToolRuntime(state=state, tool_call_id="question-call")
    result = asyncio.run(
        descriptor.handler(
            {
                "questions": [
                    {
                        "question": "Pick one",
                        "header": "Pick",
                        "options": [{"label": "A"}, {"label": "B"}],
                    }
                ]
            },
            runtime,
        )
    )

    assert seen == [InteractionKind.ASK_USER]
    assert state.interaction is None
    assert json.loads(result.content)["status"] == "answered"


def test_ctrl_c_cancel_creates_user_interrupt_checkpoint() -> None:
    state = RuntimeState()
    runtime = SimpleNamespace(state=state)
    session = StreamingSession(runtime=runtime)

    class App:
        exited = False

        def exit(self):
            self.exited = True

    app = App()
    session._cancel_turn(SimpleNamespace(app=app))

    assert session.cancelled is True
    assert app.exited is True
    assert state.interaction is not None
    assert state.interaction.kind == InteractionKind.USER_INTERRUPT


def test_exit_plan_mode_creates_plan_review_checkpoint(tmp_path: Path) -> None:
    state = RuntimeState(permission_mode=PermissionMode.PLAN)
    state.metadata["workspace"] = str(tmp_path)
    descriptor = exit_plan_mode_descriptor(PlanStore(tmp_path))
    runtime = ToolRuntime(state=state, tool_call_id="plan-review-call")

    result = asyncio.run(descriptor.handler({"summary": "ready"}, runtime))

    assert json.loads(result.content)["status"] == "awaiting_approval"
    assert state.interaction is not None
    assert state.interaction.kind == InteractionKind.PLAN_REVIEW
    assert state.interaction.interaction_id == "plan-review-call"


def test_agent_loop_stops_when_tool_leaves_runtime_suspended(tmp_path: Path) -> None:
    state = RuntimeState()
    store = MessageStore.ephemeral(session_id=state.session_id)

    class Model:
        async def stream(self, snapshot):
            call = ToolCall(id="plan-review", name="exit_plan_mode", input={})
            yield ModelStreamEvent.message_completed(
                assistant_message={"role": "assistant", "content": []},
                final_text="",
                tool_calls=(call,),
            )

    class Executor:
        async def execute(self, tool_calls, runtime_state):
            runtime_state.suspend(
                InteractionKind.PLAN_REVIEW,
                interaction_id="plan-review",
                payload={"plan_path": "plan.md"},
            )
            yield ToolExecutionUpdate(
                type="result",
                tool_call_id="plan-review",
                tool_name="exit_plan_mode",
                result=ToolExecutionResult(
                    tool_call_id="plan-review",
                    tool_name="exit_plan_mode",
                    content='{"status":"awaiting_approval"}',
                ),
            )

    loop = AgentLoop(
        state=state,
        message_store=store,
        context_engine=ContextEngine(store, prompt_assembler=StaticPromptAssembler("test")),
        model_client=Model(),
        tool_executor=Executor(),
    )

    async def collect():
        return [event async for event in loop.stream("make a plan")]

    events = asyncio.run(collect())

    assert events[-1].type == "suspended"
    assert events[-1].metadata["interaction_kind"] == "plan_review"
    assert state.is_suspended() is True
    assert store.current_messages()[-1]["role"] == "tool_result"
