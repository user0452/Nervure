"""Registry for enabled runtime tools and provider exposure policy."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from services.compaction.token_estimator import estimate_serialized_tokens
from services.tools.discovery import ToolDiscovery
from services.tools.schema import descriptor_to_openai_tool_schema
from services.tools.types import ToolDescriptor

if TYPE_CHECKING:
    from core.runtime_state import RuntimeState
    from services.permissions import PermissionPolicy


LOADED_TOOL_NAMES_METADATA_KEY = "deferred_tool_search_loaded"


@dataclass(frozen=True)
class ToolExposureConfig:
    """Small, explicit limits for direct provider tool-schema exposure."""

    max_direct_tool_count: int = 30
    max_direct_schema_tokens: int = 12_000
    max_loaded_from_search: int = 5
    max_loaded_schema_tokens: int = 12_000
    core_tool_names: tuple[str, ...] = (
        "read_file",
        "grep",
        "glob",
        "edit_file",
        "write_file",
        "bash",
    )
    tool_search_name: str = "tool_search"
    agent_tool_name: str = "agent"


@dataclass(frozen=True)
class ToolSearchSelection:
    """The descriptors returned and made provider-visible by one search."""

    candidates: tuple[ToolDescriptor, ...] = ()
    loaded: tuple[ToolDescriptor, ...] = ()
    deferred_mode: bool = False
    schema_budget_reached: bool = False


class ToolRegistry:
    def __init__(
        self,
        descriptors: Iterable[ToolDescriptor] = (),
        *,
        disabled_tools: Iterable[str] = (),
        denied_tools: Iterable[str] = (),
        permission_policy: PermissionPolicy | None = None,
        exposure_config: ToolExposureConfig | None = None,
        discovery: ToolDiscovery | None = None,
    ) -> None:
        self._descriptors: dict[str, ToolDescriptor] = {}
        self._disabled_tools = {name for name in disabled_tools if name}
        self._denied_tools = {name for name in denied_tools if name}
        self._permission_policy = permission_policy
        self._exposure_config = exposure_config or ToolExposureConfig()
        self._discovery = discovery or ToolDiscovery()
        for descriptor in descriptors:
            self.register(descriptor)

    @property
    def exposure_config(self) -> ToolExposureConfig:
        return self._exposure_config

    def register(self, descriptor: ToolDescriptor) -> None:
        if not descriptor.name:
            raise ValueError("Tool descriptor name must not be empty.")
        if descriptor.name in self._descriptors:
            raise ValueError(f"Tool descriptor already registered: {descriptor.name}")
        self._descriptors[descriptor.name] = descriptor

    def get(self, name: str) -> ToolDescriptor | None:
        return self._descriptors.get(name)

    def descriptors(self) -> tuple[ToolDescriptor, ...]:
        # 固定排序让 provider payload 和测试结果稳定，即使工具来自多个
        # 装配入口。
        return tuple(self._descriptors[name] for name in sorted(self._descriptors))

    def allowed_descriptors(self, state: RuntimeState) -> tuple[ToolDescriptor, ...]:
        """Return descriptors allowed by existing visibility/permission rules."""

        hidden_tools = self._hidden_tool_names(state)
        return tuple(
            descriptor
            for descriptor in self.descriptors()
            if descriptor.name not in hidden_tools
            and (
                self._permission_policy is None
                or self._permission_policy.is_tool_visible(descriptor, state)
            )
        )

    def is_deferred_mode(self, state: RuntimeState) -> bool:
        direct = self._normal_allowed_descriptors(state)
        return (
            len(direct) > self._exposure_config.max_direct_tool_count
            or self._schema_tokens(direct)
            > self._exposure_config.max_direct_schema_tokens
        )

    def deferred_descriptors(self, state: RuntimeState) -> tuple[ToolDescriptor, ...]:
        """Return allowed descriptors whose schemas are not initially exposed."""

        if not self.is_deferred_mode(state):
            return ()
        initially_visible = self._initial_deferred_names(state)
        loaded_names = self._loaded_tool_names(state)
        return tuple(
            descriptor
            for descriptor in self.allowed_descriptors(state)
            if descriptor.name not in initially_visible
            and descriptor.name not in loaded_names
        )

    def visible_descriptors(self, state: RuntimeState) -> tuple[ToolDescriptor, ...]:
        """Return the provider-visible schema/prompt view for this state."""

        allowed = self.allowed_descriptors(state)
        if not self.is_deferred_mode(state):
            return tuple(
                descriptor
                for descriptor in allowed
                if descriptor.name != self._exposure_config.tool_search_name
            )

        visible_names = self._initial_deferred_names(state)
        visible_names.update(self._loaded_tool_names(state))
        return tuple(
            descriptor for descriptor in allowed if descriptor.name in visible_names
        )

    def tool_schemas(self, state: RuntimeState) -> tuple[dict[str, Any], ...]:
        return tuple(
            descriptor_to_openai_tool_schema(descriptor)
            for descriptor in self.visible_descriptors(state)
        )

    def tool_prompt_sections(self, state: RuntimeState) -> tuple[str, ...]:
        return tuple(
            descriptor.prompt
            for descriptor in self.visible_descriptors(state)
            if descriptor.prompt.strip()
        )

    def search_and_load(
        self,
        state: RuntimeState,
        query: str,
    ) -> ToolSearchSelection:
        """Rank allowed deferred tools and retain bounded matches for this session."""

        if not self.is_deferred_mode(state):
            return ToolSearchSelection()

        ranked = self._discovery.rank(self.deferred_descriptors(state), query)
        if not ranked:
            return ToolSearchSelection(deferred_mode=True)

        current_names = self._loaded_tool_names(state)
        current_visible_schema_tokens = self._schema_tokens(
            self.visible_descriptors(state)
        )
        selected: list[ToolDescriptor] = []
        selected_schema_tokens = 0
        schema_budget_reached = False
        for descriptor in ranked:
            if len(selected) >= self._exposure_config.max_loaded_from_search:
                break
            candidate_tokens = self._schema_tokens((descriptor,))
            if (
                current_visible_schema_tokens
                + selected_schema_tokens
                + candidate_tokens
                > self._exposure_config.max_loaded_schema_tokens
            ):
                schema_budget_reached = True
                continue
            selected.append(descriptor)
            selected_schema_tokens += candidate_tokens

        if selected:
            current_names.update(descriptor.name for descriptor in selected)
            state.metadata[LOADED_TOOL_NAMES_METADATA_KEY] = tuple(sorted(current_names))
        return ToolSearchSelection(
            candidates=tuple(selected),
            loaded=tuple(selected),
            deferred_mode=True,
            schema_budget_reached=schema_budget_reached,
        )

    def _normal_allowed_descriptors(
        self,
        state: RuntimeState,
    ) -> tuple[ToolDescriptor, ...]:
        return tuple(
            descriptor
            for descriptor in self.allowed_descriptors(state)
            if descriptor.name != self._exposure_config.tool_search_name
        )

    def _initial_deferred_names(self, state: RuntimeState) -> set[str]:
        allowed_names = {descriptor.name for descriptor in self.allowed_descriptors(state)}
        initial = set(self._exposure_config.core_tool_names) & allowed_names
        for name in (
            self._exposure_config.agent_tool_name,
            self._exposure_config.tool_search_name,
        ):
            if name in allowed_names:
                initial.add(name)
        return initial

    def _loaded_tool_names(self, state: RuntimeState) -> set[str]:
        return _metadata_tool_names(state, LOADED_TOOL_NAMES_METADATA_KEY)

    def _schema_tokens(self, descriptors: tuple[ToolDescriptor, ...]) -> int:
        schemas = tuple(descriptor_to_openai_tool_schema(descriptor) for descriptor in descriptors)
        return estimate_serialized_tokens(schemas)

    def _hidden_tool_names(self, state: RuntimeState) -> set[str]:
        hidden = set(self._disabled_tools)
        hidden.update(self._denied_tools)
        hidden.update(_metadata_tool_names(state, "disabled_tools"))
        hidden.update(_metadata_tool_names(state, "denied_tools"))
        hidden.update(_metadata_tool_names(state, "hidden_tools"))
        return hidden


def _metadata_tool_names(state: RuntimeState, key: str) -> set[str]:
    value = state.metadata.get(key, ())
    if value is None:
        return set()
    if isinstance(value, str):
        return {value} if value else set()
    try:
        return {str(item) for item in value if str(item)}
    except TypeError:
        return {str(value)} if str(value) else set()
