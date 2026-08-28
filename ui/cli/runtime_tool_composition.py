"""Stable provider-visible tool composition for CLI runtime rebuilds."""

from __future__ import annotations

from dataclasses import dataclass, field

from services.background_tasks import BackgroundTaskManager
from services.permissions import PermissionPolicy
from services.subagents.runner import SubagentRunner
from services.tools.registry import ToolExposureConfig, ToolRegistry
from services.tools.types import ToolDescriptor
from tools.agent import descriptor as agent_descriptor


def runtime_bound_agent_descriptors(
    *,
    enabled: bool,
    subagent_runner: SubagentRunner | None,
    background_task_manager: BackgroundTaskManager | None,
) -> tuple[ToolDescriptor, ...]:
    if not enabled or subagent_runner is None:
        return ()
    return (agent_descriptor(subagent_runner, background_task_manager),)


@dataclass(frozen=True)
class RuntimeToolComposition:
    """Startup-selected tool policy reused by session/model rebuilds."""

    base_descriptors: tuple[ToolDescriptor, ...]
    exposure_config: ToolExposureConfig = field(default_factory=ToolExposureConfig)
    agent_enabled: bool = True

    def build_registry(
        self,
        *,
        permission_policy: PermissionPolicy | None,
        subagent_runner: SubagentRunner | None = None,
        background_task_manager: BackgroundTaskManager | None = None,
    ) -> ToolRegistry:
        registry = ToolRegistry(
            self.base_descriptors,
            permission_policy=permission_policy,
            exposure_config=self.exposure_config,
        )
        self.register_runtime_bound_tools(
            registry,
            subagent_runner=subagent_runner,
            background_task_manager=background_task_manager,
        )
        return registry

    def register_runtime_bound_tools(
        self,
        registry: ToolRegistry,
        *,
        subagent_runner: SubagentRunner | None,
        background_task_manager: BackgroundTaskManager | None,
    ) -> None:
        for descriptor in runtime_bound_agent_descriptors(
            enabled=self.agent_enabled,
            subagent_runner=subagent_runner,
            background_task_manager=background_task_manager,
        ):
            registry.register(descriptor)


__all__ = ["RuntimeToolComposition", "runtime_bound_agent_descriptors"]
