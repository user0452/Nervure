# Agent Profile Architecture

`AgentProfile` is a declarative layer above the existing `AgentDefinition` and `SubagentRunner`; it does not create a second agent API. `ExploreAgent`, `ImplementAgent`, and `ReviewAgent` define system prompt, tool allowlist, permission mode, budget, model preference, and expected output format. `agent(profile=...)` resolves the profile into the same child runtime composition used by the existing `agent(subagent_type=...)` path.

The child registry filters its descriptors to profile tools, the existing permission policy runs for every call, and child runtimes hide `agent`, so a profile cannot escalate into delegation or acquire arbitrary parent tools.
