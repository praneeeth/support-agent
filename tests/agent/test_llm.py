import json

import httpx2 as httpx
import pytest

from app.agent.llm import AnthropicLLM, LLMError, LLMResponse, ScriptedLLM, ToolCall
from app.agent.prompts import SYSTEM_PROMPT, format_sources

TOOLS = [{"name": "escalate", "description": "x", "input_schema": {"type": "object"}}]


def _anthropic_message(blocks: list[dict], stop: str = "end_turn") -> dict:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "test-model",
        "content": blocks,
        "stop_reason": stop,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


def _client(handler: httpx.MockTransport) -> AnthropicLLM:
    return AnthropicLLM(
        api_key="test-key",
        model="test-model",
        http_client=httpx.AsyncClient(transport=handler),
    )


async def test_adapter_builds_request_and_parses_text() -> None:
    seen: dict = {}

    def handle(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        seen["auth"] = "x-api-key" in request.headers
        return httpx.Response(200, json=_anthropic_message([{"type": "text", "text": " Hi [S1] "}]))

    resp = await _client(httpx.MockTransport(handle)).complete(
        system=SYSTEM_PROMPT, messages=[{"role": "user", "content": "hello"}], tools=TOOLS
    )
    assert resp.text == "Hi [S1]" and not resp.wants_tools
    assert seen["model"] == "test-model" and seen["auth"]
    assert seen["system"] == SYSTEM_PROMPT
    assert seen["messages"] == [{"role": "user", "content": "hello"}]
    assert [t["name"] for t in seen["tools"]] == ["escalate"]


async def test_adapter_parses_tool_use() -> None:
    blocks = [
        {"type": "text", "text": "Let me check."},
        {"type": "tool_use", "id": "tu_1", "name": "get_product", "input": {"query": "towel"}},
    ]

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_anthropic_message(blocks, stop="tool_use"))

    resp = await _client(httpx.MockTransport(handle)).complete(
        system="s", messages=[{"role": "user", "content": "hi"}], tools=TOOLS
    )
    assert resp.wants_tools and resp.stop_reason == "tool_use"
    assert resp.tool_calls[0] == ToolCall("tu_1", "get_product", {"query": "towel"})


@pytest.mark.parametrize("status", [429, 500, 529, 401])
async def test_api_errors_become_llm_error(status: int) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"type": "x", "message": "boom"}})

    client = AnthropicLLM(
        api_key="k",
        model="m",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
    with pytest.raises(LLMError):
        await client.complete(system="s", messages=[{"role": "user", "content": "hi"}])


async def test_timeout_becomes_llm_error() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    client = AnthropicLLM(
        api_key="k",
        model="m",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
    with pytest.raises(LLMError):
        await client.complete(system="s", messages=[{"role": "user", "content": "hi"}])


def test_adapter_requires_key_and_model() -> None:
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        AnthropicLLM(api_key="", model="m")
    with pytest.raises(ValueError, match="ANTHROPIC_MODEL"):
        AnthropicLLM(api_key="k", model="")


async def test_scripted_llm_replays_and_records() -> None:
    llm = ScriptedLLM().queue(
        LLMResponse(tool_calls=(ToolCall("t1", "get_product", {"query": "mug"}),)),
        LLMResponse(text="It costs ₹499 [S1]"),
    )
    first = await llm.complete(system="s", messages=[{"role": "user", "content": "price?"}])
    second = await llm.complete(system="s", messages=[{"role": "user", "content": "price?"}])
    assert first.wants_tools and second.text.endswith("[S1]")
    assert len(llm.calls) == 2 and "price?" in llm.context_text


async def test_scripted_llm_raises_queued_exception() -> None:
    llm = ScriptedLLM().queue(LLMError("RateLimitError"))
    with pytest.raises(LLMError):
        await llm.complete(system="s", messages=[])


def test_assistant_content_round_trip() -> None:
    resp = LLMResponse(text="ok", tool_calls=(ToolCall("t1", "escalate", {"reason": "x"}),))
    blocks = resp.assistant_content()
    assert blocks[0] == {"type": "text", "text": "ok"}
    assert blocks[1]["type"] == "tool_use" and blocks[1]["name"] == "escalate"


def test_format_sources() -> None:
    out = format_sources([("S1", "Returns take 30 days."), ("S2", "Shipping is free.")])
    assert out.startswith("<sources>") and "[S1] Returns take 30 days." in out
