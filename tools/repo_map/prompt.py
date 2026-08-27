"""Prompt section for the repo_map tool."""

PROMPT = """Purpose:
Build a compact structural map of a Python repository before investigating an unfamiliar codebase.

Use when:
- You need repository orientation, likely entry points, or a high-level view of modules and symbols.
- You want to reduce repeated glob/read_file calls while deciding what to inspect next.

Rules:
- The map is read-only and covers Python files under the selected workspace path.
- It reports relative paths, top-level classes/functions, class methods, and concise signatures.
- Use `read_file` or `grep` afterward for exact implementation details; this tool does not analyze references or call graphs.

Returns:
- A deterministic tree of Python files and discovered symbols.
- Short warnings for files that could not be parsed, plus an explicit truncation notice when a budget is reached.

If it fails:
- Narrow the `path` or `max_depth`, then use `glob`/`read_file` to inspect the intended area.
"""
