"""Scripted chat client for agent tests: replies are fixed, in order."""

import json
from collections.abc import Sequence
from typing import Any

from agent_framework import BaseChatClient, ChatResponse, Message, UsageDetails
from agent_framework._tools import FunctionInvocationLayer

from pitch_ledger.engine.recompute import recompute_trigger
from pitch_ledger.engine.season import SeasonView
from pitch_ledger.matchlab.generator import SimulatedMatch
from pitch_ledger.schema import TrackingSample, Trigger

FILTERED = "<filtered>"
"""A scripted reply the content filter cut (finish reason ``content_filter``)."""
CUT = '{"headline": "Cut off'
"""A scripted reply cut off at the output cap (finish reason ``length``)."""


class ScriptedChatClient(FunctionInvocationLayer[Any], BaseChatClient[Any]):
    """Returns each scripted reply in turn (an Exception is raised instead of returned).
    ``requests`` records every request payload received, ``caps`` its output cap."""

    def __init__(self, replies: list[str | Exception]) -> None:
        super().__init__()
        self.replies = list(replies)
        self.requests: list[dict[str, Any]] = []
        self.caps: list[int | None] = []

    async def _inner_get_response(
        self, *, messages: Any, stream: bool, options: Any, **kwargs: Any
    ) -> Any:
        self.requests.append(json.loads(messages[-1].text))
        self.caps.append(dict(options or {}).get("max_tokens"))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        usage = UsageDetails(input_token_count=100, output_token_count=20)
        finish = {FILTERED: "content_filter", CUT: "length"}.get(reply, "stop")
        return ChatResponse(messages=[Message(role="assistant", contents=[reply])],
                            usage_details=usage, finish_reason=finish)  # fmt: skip


class SimStats:
    """Stats gateway over an in-memory simulated match (recomputes like the MCP tools)."""

    def __init__(
        self,
        sim: SimulatedMatch,
        triggers: Sequence[Trigger],
        tracking: Sequence[TrackingSample],
        season: SeasonView | None = None,
    ) -> None:
        self.sim = sim
        self.season = season
        self.triggers = {t.trigger_id: t for t in triggers}
        self.tracking = list(tracking)
        self.calls = 0

    async def recompute_trigger(self, trigger_id: str) -> dict[str, int | float]:
        self.calls += 1
        trigger = self.triggers[trigger_id]
        return recompute_trigger(
            trigger, self.sim.match, self.sim.events, self.tracking, season=self.season
        )

    async def get_events(self, event_ids: list[str]) -> list[dict[str, Any]]:
        wanted = set(event_ids)
        return [e.model_dump(mode="json") for e in self.sim.events if e.event_id in wanted]
