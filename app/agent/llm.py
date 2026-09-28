"""LLM access. The rest of the app depends on `LLMClient`, never on the Anthropic SDK."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

log = logging.getLogger(__name__)

# Messages use the Anthropic wire format: {"role": "user"|"assistant", "content": str | [blocks]}
Message = dict[str, Any]
ToolSchema = dict[str, Any]


class LLMError(RuntimeError):
    """Any failure talking to the model: timeout, rate limit, server or auth error."""


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class LLMResponse:
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    stop_reason: str = "end_turn"

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)

    def assistant_content(self) -> list[dict[str, Any]]:
        """This response as Anthropic content blocks, for the next request in the loop."""
        blocks: list[dict[str, Any]] = []
        if self.text:
            blocks.append({"type": "text", "text": self.text})
        blocks += [
            {"type": "tool_use", "id": c.id, "name": c.name, "input": c.input}
            for c in self.tool_calls
        ]
        return blocks


class LLMClient(Protocol):
    async def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSchema] | None = None,
        max_tokens: int = 500,
    ) -> LLMResponse: ...


class AnthropicLLM:
    """Adapter over the Anthropic SDK. Raises LLMError for every API failure."""

    def __init__(self, api_key: str, model: str, timeout: float = 30.0, **client_kwargs: Any):
        from anthropic import AsyncAnthropic

        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY is not set")
        if not model:
            raise ValueError("ANTHROPIC_MODEL is not set")
        self.model = model
        self._client = AsyncAnthropic(api_key=api_key, timeout=timeout, **client_kwargs)

    async def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSchema] | None = None,
        max_tokens: int = 500,
    ) -> LLMResponse:
        import anthropic

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": list(messages),
        }
        if tools:
            kwargs["tools"] = list(tools)
        try:
            message = await self._client.messages.create(**kwargs)
        except anthropic.APIError as exc:  # timeouts, rate limits, 5xx, auth
            log.warning("Anthropic call failed: %s", exc.__class__.__name__)
            raise LLMError(exc.__class__.__name__) from exc

        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in message.content:
            if getattr(block, "type", None) == "text":
                text_parts.append(block.text)
            elif getattr(block, "type", None) == "tool_use":
                calls.append(ToolCall(id=block.id, name=block.name, input=dict(block.input or {})))
        return LLMResponse(
            text="".join(text_parts).strip(),
            tool_calls=tuple(calls),
            stop_reason=message.stop_reason or "end_turn",
        )


@dataclass
class ScriptedLLM:
    """Test double: returns queued responses and records every request it was given."""

    responses: list[LLMResponse | Exception] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def queue(self, *responses: LLMResponse | Exception) -> "ScriptedLLM":
        self.responses.extend(responses)
        return self

    @property
    def context_text(self) -> str:
        """Everything ever sent to the model, flattened — handy for leak assertions."""
        return repr(self.calls)

    async def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSchema] | None = None,
        max_tokens: int = 500,
    ) -> LLMResponse:
        self.calls.append(
            {
                "system": system,
                "messages": [dict(m) for m in messages],
                "tools": [t["name"] for t in tools or []],
                "max_tokens": max_tokens,
            }
        )
        if not self.responses:
            raise AssertionError("ScriptedLLM ran out of queued responses")
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt
