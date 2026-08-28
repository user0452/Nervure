PROMPT = """Purpose:
Search deferred tools when the currently visible tools do not provide the needed capability.

Rules:
- Use a concise capability-oriented query, then call the returned tool on a following turn.
- Matching tools become available automatically; do not search again just to load them.

Returns:
- A small ranked set of tool names and descriptions, or an empty result when no allowed deferred tool matches.
"""
