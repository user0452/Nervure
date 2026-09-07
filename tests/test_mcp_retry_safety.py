from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.mcp.manager import McpConnectionManager


@pytest.mark.parametrize("annotations", [{}, {"readOnlyHint": False}, {"idempotentHint": False}])
def test_ambiguous_write_failure_is_not_executed_twice(tmp_path, annotations):
    calls = []

    async def lost_reply(name, arguments):
        calls.append(name)
        raise ConnectionError("operation applied, reply lost")

    manager = McpConnectionManager(tmp_path, {})
    manager.ensure_connected = AsyncMock(return_value=SimpleNamespace(
        session=SimpleNamespace(call_tool=lost_reply),
        tools=(SimpleNamespace(tool_name="write", annotations=annotations),),
    ))
    manager._disconnect = AsyncMock()

    result = asyncio.run(manager.call_tool("fixture", "write", {}, "one"))
    assert result.is_error
    assert calls == ["write"]
    assert result.metadata["execution_outcome"] == "unknown"


def test_connect_failure_can_retry_before_dispatch(tmp_path):
    session = SimpleNamespace(call_tool=AsyncMock(return_value={"content": [], "isError": False}))
    manager = McpConnectionManager(tmp_path, {})
    manager.ensure_connected = AsyncMock(side_effect=[
        ConnectionError("not yet sent"), SimpleNamespace(session=session, tools=()),
    ])
    manager._disconnect = AsyncMock()

    result = asyncio.run(manager.call_tool("fixture", "write", {}, "one"))
    assert not result.is_error
    session.call_tool.assert_awaited_once()
