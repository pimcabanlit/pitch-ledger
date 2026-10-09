"""Stats gateway used by the Auditor: the MCP server over stdio, or the tools in-process."""

import json
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any, Protocol

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from pitch_ledger.mcp_server.tools import StatsTools


class StatsGateway(Protocol):
    async def recompute_trigger(self, trigger_id: str) -> dict[str, int | float]: ...

    async def get_events(self, event_ids: list[str]) -> list[dict[str, Any]]: ...


class LocalStats:
    """In-process gateway (tests, and runs that skip the MCP hop)."""

    def __init__(self, tools: StatsTools) -> None:
        self.tools = tools
        self.calls = 0

    async def recompute_trigger(self, trigger_id: str) -> dict[str, int | float]:
        self.calls += 1
        return self.tools.recompute_trigger(trigger_id)

    async def get_events(self, event_ids: list[str]) -> list[dict[str, Any]]:
        self.calls += 1
        return self.tools.get_events(event_ids)


class McpStats:
    """Gateway that talks to ``pitch-ledger mcp`` over stdio (one server per match)."""

    def __init__(self, match_dir: Path | str) -> None:
        self.params = StdioServerParameters(
            command=sys.executable, args=["-m", "pitch_ledger.mcp_server", str(match_dir)]
        )
        self._stack = AsyncExitStack()
        self.session: ClientSession | None = None
        self.calls = 0

    async def __aenter__(self) -> "McpStats":
        read, write = await self._stack.enter_async_context(stdio_client(self.params))
        self.session = await self._stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._stack.aclose()
        self.session = None

    async def call(self, tool: str, **arguments: Any) -> Any:
        if self.session is None:
            raise RuntimeError("McpStats is not open")
        self.calls += 1
        result = await self.session.call_tool(tool, arguments)
        if result.isError:
            raise RuntimeError(f"{tool} failed: {result.content}")
        if result.structuredContent is not None:
            data = result.structuredContent
            return data.get("result", data) if set(data) == {"result"} else data
        return json.loads(result.content[0].text)  # type: ignore[union-attr]

    async def recompute_trigger(self, trigger_id: str) -> dict[str, int | float]:
        return dict(await self.call("recompute_trigger", trigger_id=trigger_id))

    async def get_events(self, event_ids: list[str]) -> list[dict[str, Any]]:
        return list(await self.call("get_events", event_ids=event_ids))
