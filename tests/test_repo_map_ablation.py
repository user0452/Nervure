from __future__ import annotations

from core.runtime_state import RuntimeState
from services.tools.registry import ToolRegistry
from tools.glob import descriptor as glob_descriptor
from tools.grep import descriptor as grep_descriptor
from tools.read_file import descriptor as read_file_descriptor
from tools.repo_map import descriptor as repo_map_descriptor
from tools.symbol_search import descriptor as symbol_search_descriptor
from ui.cli.app import _repo_map_descriptors, _symbol_search_descriptors


def _registry_with_ablation() -> ToolRegistry:
    return ToolRegistry(
        (
            read_file_descriptor(),
            glob_descriptor(),
            grep_descriptor(),
            *_repo_map_descriptors(),
            *_symbol_search_descriptors(),
        )
    )


def _visible_names_and_prompts() -> tuple[set[str], tuple[str, ...]]:
    registry = _registry_with_ablation()
    state = RuntimeState()
    return (
        {descriptor.name for descriptor in registry.visible_descriptors(state)},
        registry.tool_prompt_sections(state),
    )


def test_repo_map_is_enabled_by_default_and_false_values_keep_it_visible(monkeypatch) -> None:
    monkeypatch.delenv("NERVURE_DISABLE_REPO_MAP", raising=False)
    names, prompts = _visible_names_and_prompts()
    assert "repo_map" in names
    assert repo_map_descriptor().prompt in prompts

    monkeypatch.setenv("NERVURE_DISABLE_REPO_MAP", "false")
    names, _ = _visible_names_and_prompts()
    assert "repo_map" in names


def test_repo_map_ablation_removes_schema_and_prompt_only(monkeypatch) -> None:
    monkeypatch.setenv("NERVURE_DISABLE_REPO_MAP", "1")
    names, prompts = _visible_names_and_prompts()
    assert "repo_map" not in names
    assert repo_map_descriptor().prompt not in prompts
    assert {"read_file", "glob", "grep"}.issubset(names)

    monkeypatch.setenv("NERVURE_DISABLE_REPO_MAP", "true")
    names, _ = _visible_names_and_prompts()
    assert "repo_map" not in names


def test_repo_map_ablation_environment_is_restored_between_builds(monkeypatch) -> None:
    monkeypatch.setenv("NERVURE_DISABLE_REPO_MAP", "1")
    disabled_names, _ = _visible_names_and_prompts()
    assert "repo_map" not in disabled_names

    monkeypatch.delenv("NERVURE_DISABLE_REPO_MAP", raising=False)
    enabled_names, _ = _visible_names_and_prompts()
    assert "repo_map" in enabled_names


def test_factorial_variants_expose_exactly_the_requested_understanding_tools(monkeypatch) -> None:
    expected = {
        ("1", "1"): set(),
        ("0", "1"): {"repo_map"},
        ("1", "0"): {"symbol_search"},
        ("0", "0"): {"repo_map", "symbol_search"},
    }
    for (map_disabled, symbol_disabled), understanding_tools in expected.items():
        monkeypatch.setenv("NERVURE_DISABLE_REPO_MAP", map_disabled)
        monkeypatch.setenv("NERVURE_DISABLE_SYMBOL_SEARCH", symbol_disabled)
        names, prompts = _visible_names_and_prompts()
        assert {"repo_map", "symbol_search"}.intersection(names) == understanding_tools
        assert {"read_file", "glob", "grep"}.issubset(names)
        assert (repo_map_descriptor().prompt in prompts) is ("repo_map" in understanding_tools)
        assert (symbol_search_descriptor().prompt in prompts) is ("symbol_search" in understanding_tools)

    monkeypatch.delenv("NERVURE_DISABLE_REPO_MAP", raising=False)
    monkeypatch.delenv("NERVURE_DISABLE_SYMBOL_SEARCH", raising=False)
    names, _ = _visible_names_and_prompts()
    assert {"repo_map", "symbol_search"}.issubset(names)
