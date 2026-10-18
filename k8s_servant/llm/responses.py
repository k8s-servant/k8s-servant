"""Responses API client for Foundry and OpenAI models.

Why: on gpt-5.6 and later, Chat Completions rejects function tools combined with
reasoning, so these providers use the Responses API. The AsyncOpenAI client is the
same as for Chat Completions (see chat_completions.build_client); only the endpoint
and the message shapes differ.

The agent keeps the conversation, so every request is stateless (`store=False`).
Reasoning items are carried back as encrypted content: the model keeps its chain of
thought across tool calls without the provider storing anything. They travel in
`Turn.provider_state`, the assistant's output items exactly as the API returned them.

This module is the only place that converts the neutral Turn / ToolSpec types
(llm/types.py) to Responses input items and back.
"""

import logging
import time

from openai import AsyncOpenAI

from k8s_servant.llm.types import ModelOutputTruncated, ToolCall, ToolSpec, Turn, log_usage

logger = logging.getLogger(__name__)


class ResponsesChat:
    """A ChatModel on the Responses API."""

    def __init__(self, client: AsyncOpenAI, model: str, reasoning_effort: str):
        self.client = client
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.total_tokens = 0

    async def complete(self, turns: list[Turn], tools: list[ToolSpec], *, use_tools: bool = True) -> Turn:
        """Send the conversation (and tool definitions) and return the model's reply.

        No temperature is set on purpose: reasoning models only accept the default.

        Args:
            turns: The conversation so far.
            tools: Tool definitions; omitted from the request when empty.
            use_tools: False keeps the definitions (the history refers to them) but
                sets tool_choice to "none" so the model cannot call any.

        Returns:
            The assistant turn: text, tool calls, and the raw output items as
            provider_state so reasoning survives the next request.

        Raises:
            ModelOutputTruncated: If the reply hit the output token limit.
        """
        kwargs: dict = {
            "model": self.model,
            "input": [item for t in turns for item in _to_items(t)],
            "store": False,
            "include": ["reasoning.encrypted_content"],
        }
        if self.reasoning_effort:
            kwargs["reasoning"] = {"effort": self.reasoning_effort}
        if tools:
            kwargs["tools"] = [_to_tool(t) for t in tools]
            if not use_tools:
                kwargs["tool_choice"] = "none"
        started = time.monotonic()
        response = await self.client.responses.create(**kwargs)
        usage = getattr(response, "usage", None)
        if usage is not None:
            reasoning = getattr(getattr(usage, "output_tokens_details", None), "reasoning_tokens", None) or 0
            cached = getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", None) or 0
            self.total_tokens += usage.input_tokens + usage.output_tokens
            log_usage(usage.input_tokens, cached, usage.output_tokens, reasoning, time.monotonic() - started)
        if response.status == "incomplete":
            raise ModelOutputTruncated("the reply hit the output token limit (status=incomplete)")
        calls = [ToolCall(o.call_id, o.name, o.arguments) for o in response.output if o.type == "function_call"]
        # exclude_none: the input types reject the explicit nulls the output models carry.
        state = [o.model_dump(mode="json", exclude_none=True) for o in response.output]
        return Turn("assistant", response.output_text, calls, provider_state=state)


def _to_tool(spec: ToolSpec) -> dict:
    """A tool definition in the Responses format.

    Non-strict: strict mode demands every property be required and no extras, which
    the MCP tools' JSON schemas do not satisfy.
    """
    return {
        "type": "function",
        "name": spec.name,
        "description": spec.description,
        "parameters": spec.parameters,
        "strict": False,
    }


def _to_items(turn: Turn) -> list[dict]:
    """A turn as Responses input items."""
    if turn.role == "tool":
        return [{"type": "function_call_output", "call_id": turn.tool_call_id, "output": turn.content}]
    if turn.role == "assistant":
        if turn.provider_state is not None:
            return list(turn.provider_state)
        items: list[dict] = []
        if turn.content:  # a tool-only turn must not send an empty message
            items.append({"role": "assistant", "content": turn.content})
        items += [
            {"type": "function_call", "call_id": c.id, "name": c.name, "arguments": c.arguments}
            for c in turn.tool_calls
        ]
        return items
    return [{"role": turn.role, "content": turn.content}]
