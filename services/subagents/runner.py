"""Subagent runner composition."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from time import perf_counter
from typing import Any

from core.context_engine import ContextEngine, StaticPromptAssembler
from core.loop import AgentLoop
from core.runtime_state import PermissionMode, RuntimeState
from services.context.message_store import MessageStore
from services.context.current_model_context import CurrentModelContext
from services.context.snapshot import ContextSnapshot
from services.guard import SandboxGuard
from services.model.client import ModelClient
from services.observability import TraceRecorder
from services.permissions import PermissionPolicy, PermissionPrompter
from services.subagents.definitions import get_agent_definition
from services.subagents.forking import build_forked_messages
from services.subagents.types import AgentDefinition, SubagentRequest, SubagentResult
from services.subagents.profiles import get_agent_profile
from services.tools.executor import RegistryToolExecutor
from services.tools.registry import ToolRegistry
from services.tools.types import ToolDescriptor
from services.skills import SkillCommand
from services.checkpoints import CheckpointStore


class SubagentRunner:
    def __init__(
        self,
        *,
        workspace: Path,
        transcript_root: Path,
        parent_message_store: MessageStore,
        current_model_context: CurrentModelContext,
        model_client: ModelClient,
        base_descriptors: tuple[ToolDescriptor, ...],
        guard: SandboxGuard,
        permission_policy: PermissionPolicy,
        permission_prompter: PermissionPrompter | None,
        trace_recorder: TraceRecorder,
        checkpoint_store: CheckpointStore | None = None,
    ) -> None:
        self._workspace = workspace
        self._transcript_root = transcript_root
        self._parent_message_store = parent_message_store
        self._current_model_context = current_model_context
        self._model_client = model_client
        self._base_descriptors = base_descriptors
        self._guard = guard
        self._permission_policy = permission_policy
        self._permission_prompter = permission_prompter
        self._trace_recorder = trace_recorder
        self._checkpoint_store = checkpoint_store

    @property
    def checkpoint_store(self) -> CheckpointStore | None:
        return self._checkpoint_store

    def bind_parent_message_store(self, message_store: MessageStore) -> None:
        """Rebind fork source messages after CLI resume or session clear."""

        self._parent_message_store = message_store

    async def run(self, request: SubagentRequest) -> SubagentResult:
        """Run one child loop and collapse its work into a final summary."""

        definition = self._definition_for_request(request)
        if definition is None:
            return self._error_result(
                agent_type=request.subagent_type or "unknown",
                message=f"Unknown subagent_type: {request.subagent_type}",
                error="unknown_subagent",
            )
        is_fork = request.subagent_type is None
        is_compact = _is_compact_request(request)
        is_long_term_memory_extraction = _is_long_term_memory_extraction_request(request)
        is_background_agent = _is_background_agent_request(request)
        child_state = RuntimeState(
            max_turns=_request_max_turns(request) or definition.max_turns or 20
        )
        child_state.metadata["workspace"] = str(self._workspace)
        child_state.metadata["checkpoint_session_id"] = request.parent_session_id
        if request.profile:
            child_state.metadata["agent_profile"] = request.profile
            self._trace_recorder.event("agent_profile_selected", {"profile": request.profile, "purpose": definition.when_to_use})
        _copy_shared_runtime_metadata(request, child_state)
        # The agent tool must never recurse into another agent (fork, explore,
        # memory extraction, etc). Plan mode hides the agent tool from regular
        # children too: only the top-level parent can spin up explore agents.
        child_state.metadata["hidden_tools"] = {"agent"}
        if definition.read_only or is_compact:
            child_state.metadata["read_only_agent"] = True
        is_explore = _is_explore_request(request)
        if is_explore:
            # ``explore`` is the only agent flavor the plan-mode policy allows.
            # Force the child into read-only mode and record focus paths so the
            # parent's executor can do conflict-aware concurrency.
            child_state.metadata["read_only_agent"] = True
            child_state.metadata["is_explore_agent"] = True
            focus_paths = request.metadata.get("focus_paths")
            if isinstance(focus_paths, tuple) and focus_paths:
                child_state.metadata["focus_paths"] = focus_paths
        # Plan mode is sticky: if the parent is in plan mode, the child must
        # also stay in plan mode and read-only, regardless of the agent type.
        # ``_parent_plan_mode`` is set by ``_parent_state`` via the loop, but
        # we conservatively check the request metadata here.
        if request.metadata.get("parent_plan_mode") is True:
            child_state.permission_mode = PermissionMode.PLAN
            child_state.metadata["read_only_agent"] = True
            child_state.metadata["hidden_tools"] = set(
                child_state.metadata.get("hidden_tools", set()) | {"agent"}
            )
        if is_fork:
            child_state.metadata["is_fork_child"] = True
        if is_compact:
            child_state.metadata["compact_child"] = True
        if is_long_term_memory_extraction:
            self._configure_long_term_memory_extraction_child(child_state, request)
        child_store = MessageStore.ephemeral(session_id=child_state.session_id)
        fork_snapshot = (
            self._current_model_context.snapshot_copy() if is_fork else None
        )
        seed_result = self._seed_child_messages(
            child_store,
            definition,
            request,
            is_fork=is_fork,
            snapshot=fork_snapshot,
        )
        if seed_result is not None:
            return seed_result

        registry = ToolRegistry(
            _child_descriptors(
                definition,
                self._base_descriptors,
                long_term_memory_extraction=is_long_term_memory_extraction,
                compact=is_compact,
            ),
            permission_policy=self._permission_policy,
        )
        prompt_assembler = self._prompt_assembler(
            definition,
            is_fork=is_fork,
            snapshot=fork_snapshot,
        )
        context_engine = ContextEngine(
            child_store,
            prompt_assembler=prompt_assembler,
            tool_schema_provider=registry,
        )
        tool_executor = RegistryToolExecutor(
            registry,
            guard=self._guard,
            permission_policy=self._permission_policy,
            permission_prompter=(
                None
                if (is_background_agent or is_compact)
                else self._permission_prompter
            ),
            trace_recorder=self._trace_recorder,
            checkpoint_store=self._checkpoint_store,
        )
        loop = AgentLoop(
            state=child_state,
            message_store=child_store,
            context_engine=context_engine,
            model_client=self._model_client,
            tool_executor=tool_executor,
            trace_recorder=self._trace_recorder,
        )
        return await self._drain_loop(
            loop,
            child_store,
            child_state,
            definition,
            request,
            is_fork=is_fork,
        )

    async def run_skill(
        self,
        *,
        skill: SkillCommand,
        args: str,
        parent_session_id: str,
        parent_tool_call_id: str,
    ) -> SubagentResult:
        """Run a fork-context skill in a clean child runtime."""

        definition = AgentDefinition(
            agent_type=f"skill:{skill.name}",
            when_to_use=skill.when_to_use or skill.description,
            system_prompt="You are a clean Nervure child agent running one loaded skill.",
            tools=skill.allowed_tools or ("*",),
            disallowed_tools=("agent", "skill"),
            max_turns=20,
            model=skill.model,
        )
        request = SubagentRequest(
            prompt=_skill_child_prompt(skill, args),
            subagent_type=definition.agent_type,
            parent_session_id=parent_session_id,
            parent_tool_call_id=parent_tool_call_id,
            mode="clean",
            metadata={"purpose": "skill", "skill_name": skill.name},
        )
        child_state = RuntimeState(max_turns=definition.max_turns or 20)
        child_state.metadata["workspace"] = str(self._workspace)
        child_state.metadata["checkpoint_session_id"] = parent_session_id
        _copy_shared_runtime_metadata(request, child_state)
        child_state.metadata["hidden_tools"] = {"agent", "skill"}
        child_store = MessageStore.ephemeral(session_id=child_state.session_id)
        child_store.seed_messages(({"role": "user", "content": request.prompt},))

        permission_policy = self._permission_policy.with_scoped_allowed_tools(
            skill.allowed_tools
        )
        registry = ToolRegistry(
            _child_descriptors(definition, self._base_descriptors),
            permission_policy=permission_policy,
        )
        context_engine = ContextEngine(
            child_store,
            prompt_assembler=StaticPromptAssembler(definition.system_prompt),
            tool_schema_provider=registry,
        )
        tool_executor = RegistryToolExecutor(
            registry,
            guard=self._guard,
            permission_policy=permission_policy,
            permission_prompter=self._permission_prompter,
            trace_recorder=self._trace_recorder,
            checkpoint_store=self._checkpoint_store,
        )
        loop = AgentLoop(
            state=child_state,
            message_store=child_store,
            context_engine=context_engine,
            model_client=self._model_client,
            tool_executor=tool_executor,
            trace_recorder=self._trace_recorder,
        )
        return await self._drain_loop(
            loop,
            child_store,
            child_state,
            definition,
            request,
            is_fork=False,
        )

    def _configure_long_term_memory_extraction_child(
        self,
        child_state: RuntimeState,
        request: SubagentRequest,
    ) -> None:
        """Mark the child as an internal writer for workspace long-term memory."""

        allowed_dir = request.metadata.get("allowed_memory_dir")
        if not isinstance(allowed_dir, str) or not allowed_dir:
            return
        normalized = str(Path(allowed_dir).resolve())
        child_state.metadata["long_term_memory_extraction_agent"] = True
        child_state.metadata["allowed_memory_dir"] = normalized
        child_state.metadata["hidden_tools"] = {
            "agent",
            "bash",
            "skill",
        }

    def _definition_for_request(
        self,
        request: SubagentRequest,
    ) -> AgentDefinition | None:
        # Omitted subagent_type is the explicit fork signal for the first version.
        profile = get_agent_profile(request.profile)
        if profile is not None:
            return profile.to_definition()
        return get_agent_definition(request.subagent_type or "fork")

    def _seed_child_messages(
        self,
        child_store: MessageStore,
        definition: AgentDefinition,
        request: SubagentRequest,
        *,
        is_fork: bool,
        snapshot: ContextSnapshot | None,
    ) -> SubagentResult | None:
        # Seed before continuing the child loop so fork does not duplicate prompts.
        if not is_fork:
            child_store.seed_messages(({"role": "user", "content": request.prompt},))
            return None
        if snapshot is None:
            # A fork can be requested during startup/recovery before a parent
            # model call has populated CurrentModelContext. Continue with only
            # the explicit directive; never reconstruct the full transcript.
            child_store.seed_messages(({"role": "user", "content": request.prompt},))
            return None
        forked_messages = build_forked_messages(
            snapshot.messages,
            request.prompt,
        )
        child_store.seed_messages(forked_messages)
        return None

    def _prompt_assembler(
        self,
        definition: AgentDefinition,
        *,
        is_fork: bool,
        snapshot: ContextSnapshot | None,
    ) -> StaticPromptAssembler:
        # Fork must inherit the exact bytes already rendered for the parent turn.
        if is_fork:
            return StaticPromptAssembler(snapshot.system_prompt if snapshot else "")
        return StaticPromptAssembler(definition.system_prompt)

    async def _drain_loop(
        self,
        loop: AgentLoop,
        child_store: MessageStore,
        child_state: RuntimeState,
        definition: AgentDefinition,
        request: SubagentRequest,
        *,
        is_fork: bool,
    ) -> SubagentResult:
        started = perf_counter()
        self._trace_recorder.event(
            "subagent_start",
            {
                "agent_type": definition.agent_type,
                "parent_session_id": request.parent_session_id,
                "child_session_id": child_state.session_id,
                "is_fork": is_fork,
                "read_only": definition.read_only,
                "purpose": request.metadata.get("purpose"),
                "prompt_length": len(request.prompt),
            },
        )
        final_text = ""
        try:
            async for event in loop.continue_stream():
                if event.type == "completed":
                    final_text = event.text
        except Exception as exc:
            child_store.flush_transcript()
            self._trace_recorder.event(
                "subagent_error",
                {
                    "agent_type": definition.agent_type,
                    "child_session_id": child_state.session_id,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "model_calls": child_state.turn_count,
                    "input_tokens": child_state.usage.input_tokens,
                    "cache_read_input_tokens": child_state.usage.cache_read_input_tokens,
                    "uncached_input_tokens": max(
                        0,
                        child_state.usage.input_tokens
                        - child_state.usage.cache_read_input_tokens,
                    ),
                    "output_tokens": child_state.usage.output_tokens,
                    "reasoning_tokens": child_state.usage.reasoning_tokens,
                    "visible_output_tokens": child_state.usage.visible_output_tokens,
                },
            )
            return self._error_result(
                agent_type=definition.agent_type,
                session_id=child_state.session_id,
                message=f"Subagent failed: {type(exc).__name__}: {exc}",
                error="subagent_error",
                transition=(
                    child_state.last_transition.value
                    if child_state.last_transition is not None
                    else None
                ),
            )
        result = SubagentResult(
            agent_type=definition.agent_type,
            session_id=child_state.session_id,
            final_text=final_text,
            transition=(
                child_state.last_transition.value
                if child_state.last_transition is not None
                else None
            ),
            usage=replace(child_state.usage),
            tool_result_count=_tool_result_count(child_store),
            metadata={"is_fork": is_fork, "read_only": definition.read_only},
        )
        child_store.flush_transcript()
        self._trace_recorder.event(
            "subagent_completed",
            {
                "agent_type": result.agent_type,
                "child_session_id": result.session_id,
                "transition": result.transition,
                "tool_result_count": result.tool_result_count,
                "model_calls": child_state.turn_count,
                "input_tokens": result.usage.input_tokens if result.usage else 0,
                "cache_read_input_tokens": (
                    result.usage.cache_read_input_tokens if result.usage else 0
                ),
                "uncached_input_tokens": (
                    max(
                        0,
                        result.usage.input_tokens
                        - result.usage.cache_read_input_tokens,
                    )
                    if result.usage
                    else 0
                ),
                "output_tokens": result.usage.output_tokens if result.usage else 0,
                "reasoning_tokens": (
                    result.usage.reasoning_tokens if result.usage else None
                ),
                "visible_output_tokens": (
                    result.usage.visible_output_tokens if result.usage else None
                ),
                "duration_ms": round((perf_counter() - started) * 1000, 3),
            },
        )
        return result

    def _error_result(
        self,
        *,
        agent_type: str,
        message: str,
        error: str,
        session_id: str = "",
        transition: str | None = None,
    ) -> SubagentResult:
        return SubagentResult(
            agent_type=agent_type,
            session_id=session_id,
            final_text=message,
            is_error=True,
            transition=transition,
            metadata={"error": error},
        )


def _child_descriptors(
    definition: AgentDefinition,
    base_descriptors: tuple[ToolDescriptor, ...],
    *,
    long_term_memory_extraction: bool = False,
    compact: bool = False,
) -> tuple[ToolDescriptor, ...]:
    if compact:
        # Internal compaction is a pure summarization task; expose no tools so the
        # model literally cannot call read/edit/bash. Capability is enforced by the
        # empty registry rather than prompt text.
        return ()
    if long_term_memory_extraction:
        allowed = {"read_file", "grep", "glob", "write_file", "edit_file"}
        return tuple(
            descriptor
            for descriptor in base_descriptors
            if descriptor.name in allowed
        )
    allowed_names = set(definition.tools)
    disallowed = set(definition.disallowed_tools)
    disallowed.add("agent")
    descriptors: list[ToolDescriptor] = []
    for descriptor in base_descriptors:
        if descriptor.name in disallowed:
            continue
        if "*" not in allowed_names and descriptor.name not in allowed_names:
            continue
        descriptors.append(descriptor)
    return tuple(descriptors)


def _is_compact_request(request: SubagentRequest) -> bool:
    return request.metadata.get("query_source") == "compact"


def _is_explore_request(request: SubagentRequest) -> bool:
    return request.subagent_type == "explore" or request.metadata.get(
        "purpose"
    ) == "plan_explore"


def _is_long_term_memory_extraction_request(request: SubagentRequest) -> bool:
    return request.metadata.get("purpose") == "long_term_memory_extraction"


def _is_background_agent_request(request: SubagentRequest) -> bool:
    return (
        request.metadata.get("background_task_id") is not None
        and request.metadata.get("purpose") != "long_term_memory_extraction"
    )


def _request_max_turns(request: SubagentRequest) -> int | None:
    value = request.metadata.get("max_turns")
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    return None


def _copy_shared_runtime_metadata(
    request: SubagentRequest,
    child_state: RuntimeState,
) -> None:
    """Carry parent-scoped runtime facts that child tools must share."""

    for key in ("task_list_id", "parent_task_list_id"):
        value = request.metadata.get(key)
        if isinstance(value, str) and value:
            child_state.metadata[key] = value


def _tool_result_count(message_store: MessageStore) -> int:
    return sum(
        1
        for message in message_store.current_messages()
        if message.get("role") == "tool_result"
    )


def _skill_child_prompt(skill: SkillCommand, args: str) -> str:
    """Build the single clean-context user message for a fork skill."""

    root = str(skill.root) if skill.root is not None else ""
    content = skill.content
    if root:
        content = (
            f"Base directory for this skill: {root}\n\n"
            + content.replace("${NERVURE_SKILL_DIR}", root).replace(
                "${ONECODE_SKILL_DIR}", root
            )
        )
    return (
        f"[skill loaded: {skill.name}]\n"
        f"Arguments: {args}\n"
        f"Source: {skill.source}\n\n"
        f"{content}"
    )
