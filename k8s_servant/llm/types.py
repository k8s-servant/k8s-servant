"""Provider-neutral types for talking to a chat model.

The agent loop, the health check and the morning report speak only these types.
Each API client (one module in this package per wire protocol) converts them to
its own request format and converts the reply back, so nothing outside this
package depends on a model SDK, and a new API means one new module.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

logger = logging.getLogger(__name__)

# Stands in for an empty assistant reply when it is sent back: the APIs reject an
# assistant message with no content, and an empty reply is exactly what the health
# check's JSON retry sends back.
NO_ANSWER = "(no answer)"


@dataclass(frozen=True)
class ToolCall:
    """One tool call the model asked for."""

    id: str
    name: str
    arguments: str  # JSON text, exactly as the model produced it


@dataclass(frozen=True)
class ToolSpec:
    """A tool definition offered to the model."""

    name: str
    description: str
    parameters: dict  # JSON schema


@dataclass
class Turn:
    """One message of the conversation, in any role."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str = ""  # set on role="tool"
    provider_state: Any = None  # opaque; only the API client that produced it reads it


class ModelOutputTruncated(Exception):
    """The reply was cut off by a token limit; never treated as an answer."""


class ModelCallFailed(Exception):
    """The provider reported the call as failed or cancelled; never treated as an answer."""


class ChatModel(Protocol):
    """The one operation the agent needs from a model: continue a conversation."""

    # Tokens used by this client so far (prompt + completion), for the summary
    # line and for sizing the deployment's tokens-per-minute capacity.
    total_tokens: int

    async def complete(self, turns: list[Turn], tools: list[ToolSpec], *, use_tools: bool = True) -> Turn:
        """Send the conversation and return the model's next turn.

        Args:
            turns: The conversation so far.
            tools: Tool definitions. They are sent even when `use_tools` is False,
                because some APIs reject a history that contains tool calls
                without the definitions.
            use_tools: False forbids the model to call tools in this reply.
        """


def log_usage(prompt: int, cached: int, completion: int, reasoning: int, seconds: float) -> None:
    """Log one INFO line per model call; it shows what a check really costs.

    Args:
        prompt: Prompt tokens, including the cached ones.
        cached: Prompt tokens served from the provider's cache (billed at a discount).
        completion: Completion tokens, including the reasoning ones.
        reasoning: Tokens spent on reasoning.
        seconds: Wall-clock duration of the call.
    """
    logger.info(
        "model call: %d prompt (%d cached) + %d completion tokens (%d reasoning) in %.1fs",
        prompt,
        cached,
        completion,
        reasoning,
        seconds,
    )
