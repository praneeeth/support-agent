import json

import httpx
import pytest

from app.agent.llm import LLMError
from app.agent.openai_compat import OpenAICompatLLM, to_openai_messages, to_openai_tool
from app.agent.tools import TOOLS
from app.config import Settings

SOURCES = "<sources>\n[S1] Returns take 30 days.\n</sources>\n\nCustomer: how long?"


def _client(handler, **kwargs) -> OpenAICompatLLM:  # type: ignore[no-untyped-def]
    return OpenAICompatLLM(
        base_url="http://localhost:11434/v1",
        model="qwen3:8b",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs),
    )


def _completion(message: dict, finish: str = "stop") -> dict:
    return {"choices": [{"index": 0, "message": message, "finish_reason": finish}]}


async def test_text_response_and_request_shape() -> None:
    seen: dict = {}

    def handle(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        seen["url"] = str(request.url)
        return httpx.Response(
            200, json=_completion({"role": "assistant", "content": " 30 days. [S1] "})
        )

    resp = await _client(handle).complete(
        system="sys", messages=[{"role": "user", "content": SOURCES}], tools=TOOLS, max_tokens=300
    )
    assert resp.text == "30 days. [S1]" and not resp.wants_tools
    assert seen["url"] == "http://localhost:11434/v1/chat/completions"
    assert seen["model"] == "qwen3:8b" and seen["temperature"] == 0
    assert seen["messages"][0] == {"role": "system", "content": "sys"}
    assert [t["function"]["name"] for t in seen["tools"]] == [
        "get_order_status",
        "get_product",
        "escalate",
    ]


async def test_tool_call_response() -> None:
    message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "get_product", "arguments": '{"query": "towel"}'},
            }
        ],
    }

    resp = await _client(
        lambda r: httpx.Response(200, json=_completion(message, "tool_calls"))
    ).complete(system="s", messages=[{"role": "user", "content": "price?"}], tools=TOOLS)
    assert resp.wants_tools
    assert resp.tool_calls[0].name == "get_product"
    assert resp.tool_calls[0].input == {"query": "towel"}


async def test_invalid_tool_arguments_become_empty_input() -> None:
    message = {
        "role": "assistant",
        "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "get_product", "arguments": "{"}}
        ],
    }
    resp = await _client(lambda r: httpx.Response(200, json=_completion(message))).complete(
        system="s", messages=[]
    )
    assert resp.tool_calls[0].input == {}  # the tool layer turns this into an escalation


@pytest.mark.parametrize("status", [401, 429, 500])
async def test_http_errors_become_llm_error(status: int) -> None:
    client = _client(lambda r: httpx.Response(status, json={"error": "nope"}))
    with pytest.raises(LLMError):
        await client.complete(system="s", messages=[])


async def test_connection_error_becomes_llm_error() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(LLMError):
        await _client(handle).complete(system="s", messages=[])


async def test_malformed_body_becomes_llm_error() -> None:
    client = _client(lambda r: httpx.Response(200, json={"unexpected": True}))
    with pytest.raises(LLMError):
        await client.complete(system="s", messages=[])


def test_requires_base_url_and_model() -> None:
    with pytest.raises(ValueError, match="LLM_BASE_URL"):
        OpenAICompatLLM(base_url="", model="m")
    with pytest.raises(ValueError, match="LLM_MODEL"):
        OpenAICompatLLM(base_url="http://x/v1", model="")


def test_tool_schema_conversion() -> None:
    converted = to_openai_tool(TOOLS[0])
    assert converted["type"] == "function"
    assert converted["function"]["name"] == "get_order_status"
    assert converted["function"]["parameters"]["required"] == ["order_number", "email"]


def test_message_conversion_round_trip() -> None:
    """A full tool-use turn: assistant asks for a tool, the result comes back."""
    messages = [
        {"role": "user", "content": "where is NW-123456"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Checking."},
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "get_order_status",
                    "input": {"order_number": "NW-123456", "email": "a@b.c"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "Order NW-123456: shipped."}
            ],
        },
    ]
    out = to_openai_messages("sys", messages)
    assert [m["role"] for m in out] == ["system", "user", "assistant", "tool"]
    assert out[2]["content"] == "Checking."
    assert out[2]["tool_calls"][0]["function"]["name"] == "get_order_status"
    assert json.loads(out[2]["tool_calls"][0]["function"]["arguments"])["email"] == "a@b.c"
    assert out[3] == {
        "role": "tool",
        "tool_call_id": "t1",
        "content": "Order NW-123456: shipped.",
    }


def test_build_llm_picks_provider() -> None:
    from app.agent.routes import build_llm

    local = build_llm(Settings(llm_provider="openai_compatible"))
    assert isinstance(local, OpenAICompatLLM)
    with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
        build_llm(Settings(llm_provider="carrier-pigeon"))
