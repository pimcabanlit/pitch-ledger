"""Scripted chat client for agent tests: replies are fixed, in order."""

import json
from typing import Any

from agent_framework import BaseChatClient, ChatResponse, Message, UsageDetails
from agent_framework._tools import FunctionInvocationLayer


class ScriptedChatClient(FunctionInvocationLayer[Any], BaseChatClient[Any]):
    """Returns each scripted reply in turn (an Exception is raised instead of returned).
    ``requests`` records every request payload received."""

    def __init__(self, replies: list[str | Exception]) -> None:
        super().__init__()
        self.replies = list(replies)
        self.requests: list[dict[str, Any]] = []

    async def _inner_get_response(
        self, *, messages: Any, stream: bool, options: Any, **kwargs: Any
    ) -> Any:
        self.requests.append(json.loads(messages[-1].text))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        usage = UsageDetails(input_token_count=100, output_token_count=20)
        return ChatResponse(messages=[Message(role="assistant", contents=[reply])],
                            usage_details=usage)  # fmt: skip
