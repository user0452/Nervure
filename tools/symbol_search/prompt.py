"""Prompt section for the symbol_search tool."""

PROMPT = """Purpose:
Locate Python class, function, and method definitions when you already know or suspect a symbol name.

Use when:
- You need the exact definition path and line before reading or editing a focused area.
- You have a concept or likely symbol name; use repo_map first when you need broad repository orientation.
- When a class, function, or method name is known or suspected but its file is not, prefer this over guessing with glob or grep.
- This is a focused lookup, not a mandatory step for every task; use it when a symbol definition is the missing locator.

Returns:
- Workspace-relative path, line, symbol kind, qualified name, and a compact signature.
- Deterministically ranked definition matches; syntax-error files are reported as short warnings.

This is a definition search only. It does not search usages, imports, references, or call graphs; keep using grep for arbitrary text or references.
"""
