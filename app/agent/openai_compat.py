"""Adapter for any OpenAI-compatible chat API: Ollama (free, local), Groq, Google AI Studio,
OpenRouter, vLLM, LM Studio.

The rest of the app only sees `LLMClient`, so switching providers changes configuration, not code.
Messages are kept in Anthropic shape internally and converted here.
"""

import json
import logging
from collections.abc import Sequence
from typing import Any

import httpx

from app.agent.llm import LLMError, LLMResponse, Message, ToolCall, ToolSchema

log = logging.getLogger(__name__)


class OpenAICompatLLM:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("LLM_BASE_URL is not set")
        if not model:
            raise ValueError("LLM_MODEL is not set")
        self.base_url = base_url.rstrip("/")
        self.model = model
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = client or httpx.AsyncClient(timeout=timeout, headers=headers)

    async def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSchema] | None = None,
        max_tokens: int = 500,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": to_openai_messages(system, messages),
            "temperature": 0,
        }
        if tools:
            payload["tools"] = [to_openai_tool(t) for t in tools]
        try:
            response = await self._client.post(f"{self.base_url}/chat/completions", json=payload)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("LLM call failed: %s", exc.__class__.__name__)
            raise LLMError(exc.__class__.__name__) from exc

        try:
            choice = data["choices"][0]
        except (KeyError, IndexError) as exc:
            raise LLMError("MalformedResponse") from exc
        message = choice.get("message") or {}
        calls = []
        for i, call in enumerate(message.get("tool_calls") or []):
            function = call.get("function") or {}
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}  # run_tool turns invalid input into an escalation
            calls.append(
                ToolCall(
                    id=str(call.get("id") or f"call_{i}"),
                    name=str(function.get("name", "")),
                    input=arguments if isinstance(arguments, dict) else {},
                )
            )
        return LLMResponse(
            text=(message.get("content") or "").strip(),
            tool_calls=tuple(calls),
            stop_reason=str(choice.get("finish_reason") or "stop"),
        )


def to_openai_tool(tool: ToolSchema) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool.get("input_schema", {"type": "object"}),
        },
    }


def to_openai_messages(system: str, messages: Sequence[Message]) -> list[dict[str, Any]]:
    """Anthropic-shaped messages (including tool_use / tool_result blocks) -> OpenAI chat format."""
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for message in messages:
        role, content = message["role"], message["content"]
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue

        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        tool_results: list[dict[str, Any]] = []
        for block in content:
            kind = block.get("type")
            if kind == "text":
                text_parts.append(block.get("text", ""))
            elif kind == "tool_use":
                tool_calls.append(
                    {
                        "id": block["id"],
                        "type": "function",
                        "function": {
                            "name": block["name"],
                            "arguments": json.dumps(block.get("input") or {}),
                        },
                    }
                )
            elif kind == "tool_result":
                tool_results.append(
                    {
                        "role": "tool",
                        "tool_call_id": block["tool_use_id"],
                        "content": str(block.get("content", "")),
                    }
                )

        if role == "assistant" and (text_parts or tool_calls):
            entry: dict[str, Any] = {"role": "assistant", "content": "\n".join(text_parts)}
            if tool_calls:
                entry["tool_calls"] = tool_calls
            out.append(entry)
        out.extend(tool_results)
        if role == "user" and text_parts:
            out.append({"role": "user", "content": "\n".join(text_parts)})
    return out
