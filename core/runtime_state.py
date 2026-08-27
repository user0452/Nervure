"""Mutable state for a single agent runtime session."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
import uuid

from core.transitions import TransitionReason
from services.model.types import ModelUsage


class PermissionMode(StrEnum):
    """The active permission mode of the runtime.

    Plan mode is a first-class mode that hard-restricts tool visibility and
    execution. It is intentionally not encoded via ``RuntimeState.metadata`` so
    that permissions, the registry, the attachment projector, and the CLI all
    share a single structured source of truth.
    """

    DEFAULT = "default"
    FULL_ACCESS = "full_access"
    PLAN = "plan"


class InteractionKind(StrEnum):
    """Human-in-the-loop suspension reasons understood by the runtime."""

    ASK_USER = "ask_user"
    PERMISSION = "permission"
    PLAN_REVIEW = "plan_review"
    USER_INTERRUPT = "user_interrupt"


@dataclass(frozen=True)
class RuntimeInteraction:
    """One active human-in-the-loop suspension point."""

    interaction_id: str
    kind: InteractionKind
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class PlanState:
    """Structured state for the plan-mode lifecycle.

    This object replaces the previous ad-hoc ``metadata["plan_file_path"]``,
    ``metadata["permission_mode"]`` style flags. It carries everything the
    runtime needs to (re)enter, transition between, and exit plan mode without
    consulting the metadata dict.
    """

    pre_plan_mode: PermissionMode | None = None
    has_exited_plan_mode: bool = False
    needs_plan_mode_attachment: bool = False
    needs_plan_mode_exit_attachment: bool = False
    plan_slug: str | None = None
    parent_session_id: str | None = None

    def reset(self) -> None:
        """Clear all plan-mode state without losing session-level config."""

        self.pre_plan_mode = None
        self.has_exited_plan_mode = False
        self.needs_plan_mode_attachment = False
        self.needs_plan_mode_exit_attachment = False
        self.plan_slug = None
        self.parent_session_id = None


@dataclass
class RuntimeState:
    usage: ModelUsage = field(default_factory=ModelUsage)
    turn_count: int = 0
    max_turns: int | None = None
    has_attempted_reactive_compact: bool = False
    has_escalated_max_output_tokens: bool = False
    max_output_recovery_count: int = 0
    last_transition: TransitionReason | None = None
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    # Stable identity for one user interaction. ``turn_count`` counts model
    # steps and therefore must not be used for per-user-turn caches.
    user_turn_id: str | None = None
    # First-class permission mode. Plan mode replaces the previous
    # ``metadata["permission_mode"]`` implicit protocol.
    permission_mode: PermissionMode = PermissionMode.DEFAULT
    # Structured plan-mode state. Tools, permissions, and the attachment
    # projector all read this object instead of poking at ``metadata``.
    plan: PlanState = field(default_factory=PlanState)
    # One active human-in-the-loop checkpoint, if the runtime is suspended.
    interaction: RuntimeInteraction | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_usage(self, usage: ModelUsage) -> None:
        self.usage.add(usage)

    def set_transition(self, transition: TransitionReason) -> None:
        self.last_transition = transition

    def begin_user_turn(self) -> str:
        """Start a new user interaction without resetting model-step state."""

        self.user_turn_id = str(uuid.uuid4())
        return self.user_turn_id

    def is_plan_mode(self) -> bool:
        """Return whether the runtime is currently in plan mode."""

        return self.permission_mode == PermissionMode.PLAN

    def is_suspended(self) -> bool:
        """Return whether execution is waiting at a human-in-the-loop checkpoint."""

        return self.interaction is not None

    def suspend(
        self,
        kind: InteractionKind,
        *,
        payload: dict[str, Any] | None = None,
        interaction_id: str | None = None,
    ) -> RuntimeInteraction:
        """Mark the runtime as waiting for a human decision or input."""

        interaction = RuntimeInteraction(
            interaction_id=interaction_id or str(uuid.uuid4()),
            kind=kind,
            payload=dict(payload or {}),
        )
        self.interaction = interaction
        return interaction

    def resume(self, *, interaction_id: str | None = None) -> RuntimeInteraction | None:
        """Clear the active HITL suspension and return the interaction that ended."""

        current = self.interaction
        if current is None:
            return None
        if interaction_id is not None and current.interaction_id != interaction_id:
            return None
        self.interaction = None
        return current

    def start_new_session(self) -> str:
        """开启新的运行时会话。

        用于未来 `/clear` 这类清空当前对话的入口。该方法会生成新的
        session UUID，并重置和当前消息链相关的运行时计数、恢复状态与
        metadata；`max_turns` 代表运行时配置，因此不会被重置。`None`
        表示当前 runtime 不设置轮数上限。
        """

        next_permission_mode = (
            self.plan.pre_plan_mode or PermissionMode.DEFAULT
            if self.permission_mode == PermissionMode.PLAN
            else self.permission_mode
        )
        self.session_id = str(uuid.uuid4())
        self.user_turn_id = None
        self.usage = ModelUsage()
        self.turn_count = 0
        self.has_attempted_reactive_compact = False
        self.has_escalated_max_output_tokens = False
        self.max_output_recovery_count = 0
        self.last_transition = None
        self.permission_mode = next_permission_mode
        self.plan.reset()
        self.interaction = None
        self.metadata.clear()
        # ``model_turn_counter`` 由 ``core/loop.py`` 在每次模型调用
        # 时自增,这里不需要清零 — 它本来就在 metadata 里,会被
        # ``metadata.clear()`` 一起清掉,确保新 session 的 checkpoint
        # 归属 id 从 1 重新开始。
        return self.session_id
