"""Built-in subagent runtime services."""

from services.context.current_model_context import CurrentModelContext
from services.subagents.definitions import BUILT_IN_AGENTS, get_agent_definition
from services.subagents.forking import build_forked_messages
from services.subagents.types import AgentDefinition, SubagentRequest, SubagentResult
from services.subagents.profiles import AgentProfile, BUILT_IN_PROFILES, get_agent_profile

__all__ = [
    "AgentDefinition",
    "AgentProfile",
    "BUILT_IN_PROFILES",
    "BUILT_IN_AGENTS",
    "CurrentModelContext",
    "SubagentRequest",
    "SubagentResult",
    "build_forked_messages",
    "get_agent_definition",
    "get_agent_profile",
]
