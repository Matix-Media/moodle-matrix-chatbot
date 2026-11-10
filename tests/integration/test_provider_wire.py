"""Real provider clients against mocked HTTP: streaming tool calls end to end.

The chat model here is built from the production kwargs mapping, not a fake; only
the provider's HTTP endpoint is mocked with its streaming wire format (``respx`` for
httpx-based SDKs like Ollama, an ``httpx2.MockTransport`` for OpenAI's SDK). A provider
package upgrade that changes how streamed tool calls or usage are parsed fails here.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import httpx2
from langchain.chat_models import init_chat_model
from pydantic import SecretStr

from moodle_llm_chat.config import LLMConfig, Provider, ThinkingLevel
from moodle_llm_chat.llm.factory import build_chat_model, chat_model_kwargs
from support.harness import MOODLE, Api, document, ws_until

USER = 7


def config(provider: Provider, base_url: str) -> LLMConfig:
    return LLMConfig(
        provider=provider,
        model="test-model",
        utility_model="test-model",
        api_key=SecretStr("k") if provider is Provider.OPENAI else None,
        base_url=base_url,
        temperature=0.0,
        max_tool_calls=25,
        thinking=ThinkingLevel.OFF,
        thinking_budget_tokens=None,
        thinking_params=None,
    )


def has_tool_result(body: dict[str, Any]) -> bool:
    return any(m.get("role") == "tool" for m in body.get("messages", []))


def sse(chunks: list[dict[str, Any]]) -> str:
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"


def openai_handler(request: Any) -> Any:
    body = json.loads(request.content)
    assert body["stream"] is True
    base = {"id": "x", "object": "chat.completion.chunk", "created": 0, "model": "test-model"}
    if not has_tool_result(body):
        chunks = [
            {
                **base,
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "search_text", "arguments": ""},
                                }
                            ],
                        },
                    }
                ],
            },
            {
                **base,
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {"index": 0, "function": {"arguments": '{"query": "Raum"}'}}
                            ]
                        },
                    }
                ],
            },
            {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
            {
                **base,
                "choices": [],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
            },
        ]
    else:
        tool_output = next(m["content"] for m in body["messages"] if m["role"] == "tool")
        assert "B204" in tool_output
        chunks = [
            {**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": "Raum "}}]},
            {**base, "choices": [{"index": 0, "delta": {"content": "B204 [1]."}}]},
            {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            {
                **base,
                "choices": [],
                "usage": {"prompt_tokens": 150, "completion_tokens": 5, "total_tokens": 155},
            },
        ]
    return httpx2.Response(200, text=sse(chunks), headers={"content-type": "text/event-stream"})


def ollama_handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    common = {"model": "test-model", "created_at": "2026-11-10T12:00:00Z"}
    if not has_tool_result(body):
        lines = [
            {
                **common,
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "search_text", "arguments": {"query": "Raum"}}}
                    ],
                },
                "done": False,
            },
            {
                **common,
                "message": {"role": "assistant", "content": ""},
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 80,
                "eval_count": 12,
            },
        ]
    else:
        lines = [
            {
                **common,
                "message": {"role": "assistant", "content": "Raum B204 [1]."},
                "done": False,
            },
            {
                **common,
                "message": {"role": "assistant", "content": ""},
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 120,
                "eval_count": 6,
            },
        ]
    return httpx.Response(200, text="\n".join(json.dumps(line) for line in lines) + "\n")


def run_chat(api: Api) -> list[dict[str, Any]]:
    scope = {"cmids": {10}, "courses": {2}}
    with api.running():
        api.push_courses({"course_id": 2, "fullname": "Netzwerke LF7", "shortname": "LF7"})
        api.push_documents(document("2:10:plan", text="Unterricht in Raum B204.", module="Plan"))
        api.idle()
        api.moodle.visible[USER] = {10}
        with api.http.websocket_connect(
            "wss://testserver/ws/chat", headers={"origin": MOODLE}
        ) as ws:
            ws.send_json({"type": "auth", "token": api.token(USER, **scope)})
            assert ws.receive_json()["type"] == "ready"
            ws.send_json(
                {"type": "send", "text": "Wo ist Unterricht?", "token": api.token(USER, **scope)}
            )
            return ws_until(ws, "quota")


def assert_tool_loop(frames: list[dict[str, Any]], used_tokens: int) -> None:
    call = next(f for f in frames if f["type"] == "tool_call")
    assert call["name"] == "search_text" and call["args"] == {"query": "Raum"}
    tokens = "".join(f["delta"] for f in frames if f["type"] == "token")
    final = next(f for f in frames if f["type"] == "final")
    assert final["text"] == "Raum B204 [1]." == tokens
    assert final["citations"][0]["path"] == "/LF7/Woche 1/Plan"
    assert frames[-1]["used_today"] == used_tokens


class TestOpenAICompatible:
    def test_streamed_tool_call_and_usage(self, api: Api) -> None:
        # The OpenAI SDK speaks httpx2, which respx does not patch: inject a mock
        # transport into the client built from the production kwargs mapping.
        provider, kwargs = chat_model_kwargs(config(Provider.OPENAI, "http://llm.test/v1"))
        kwargs["http_async_client"] = httpx2.AsyncClient(
            transport=httpx2.MockTransport(openai_handler)
        )
        api.model = init_chat_model(kwargs.pop("model"), model_provider=provider, **kwargs)
        assert_tool_loop(run_chat(api), used_tokens=110 + 155)


class TestOllama:
    def test_streamed_tool_call_and_usage(self, api: Api) -> None:
        api.web.post("http://ollama.test/api/chat").mock(side_effect=ollama_handler)
        api.model = build_chat_model(config(Provider.OLLAMA, "http://ollama.test"))
        assert_tool_loop(run_chat(api), used_tokens=(80 + 12) + (120 + 6))
