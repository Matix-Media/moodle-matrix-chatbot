"""Reasoning reaches the chat for every provider, through its real client and wire format.

Each test mocks only the provider's HTTP endpoint with its streaming format: a model
step that thinks and calls a tool, then a step that thinks and answers. With
``show_thinking=full`` both thoughts must arrive as ``thinking`` frames, each before
what its step did. Where the provider verifies reasoning across tool calls
(Anthropic's signatures), the second request must carry the first step's thinking back.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import anthropic
import httpx
import httpx2
import pytest
from google import genai
from google.genai import types as genai_types
from pydantic import SecretStr

from moodle_llm_chat.config import LLMConfig, Provider, ThinkingLevel
from moodle_llm_chat.llm.factory import build_chat_model
from support.harness import MOODLE, Api, document, ws_until

USER = 7
#: Each step's reasoning, streamed in two deltas.
THOUGHTS = (("Ich suche ", "den Raum."), ("Gefunden: ", "B204."))
ANSWER = "Raum B204 [1]."


@pytest.fixture(autouse=True)
def full_thinking(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MLC_AGENT__SHOW_THINKING", "full")


def config(provider: Provider, base_url: str | None = None) -> LLMConfig:
    return LLMConfig(
        provider=provider,
        model="test-model",
        utility_model="test-model",
        api_key=None if provider is Provider.OLLAMA else SecretStr("k"),
        base_url=base_url,
        temperature=0.0,
        max_tool_calls=25,
        thinking=ThinkingLevel.MEDIUM,
        thinking_budget_tokens=None,
        thinking_params=None,
    )


def is_second_step(body: dict[str, Any]) -> bool:
    """Whether the request already carries the tool result (any provider's shape)."""
    text = json.dumps(body)
    return "B204" in text


def sse(events: list[dict[str, Any]], *, named: bool = False) -> str:
    def one(e: dict[str, Any]) -> str:
        head = f"event: {e['type']}\n" if named else ""
        return f"{head}data: {json.dumps(e)}\n\n"

    return "".join(one(e) for e in events)


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


def assert_thoughts_stream_in_order(frames: list[dict[str, Any]]) -> None:
    kinds = [f["type"] for f in frames]
    thinking = [i for i, k in enumerate(kinds) if k == "thinking"]
    assert thinking, f"no thinking frames in {kinds}"
    text = "".join(frames[i]["delta"] or "" for i in thinking)
    assert text == "".join(THOUGHTS[0] + THOUGHTS[1])
    call, result, token = kinds.index("tool_call"), kinds.index("tool_result"), kinds.index("token")
    assert thinking[0] < call, "first step's thought before its tool call"
    assert any(result < i < token for i in thinking), "second step's thought before the answer"
    final = next(f for f in frames if f["type"] == "final")
    assert final["text"] == ANSWER


# ---------------------------------------------------------------------------- #
# OpenAI-compatible chat completions: OpenRouter, DeepSeek, vLLM, LM Studio, …
# ---------------------------------------------------------------------------- #


def reasoning_content(text: str) -> dict[str, Any]:
    """DeepSeek, vLLM, LM Studio, llama.cpp."""
    return {"reasoning_content": text}


def reasoning(text: str) -> dict[str, Any]:
    """OpenRouter, newer vLLM / LM Studio, Groq."""
    return {"reasoning": text}


def reasoning_details(text: str) -> dict[str, Any]:
    """OpenRouter's structured form, without the plain string."""
    return {
        "reasoning_details": [
            {"type": "reasoning.text", "text": text, "format": "unknown", "index": 0}
        ]
    }


def reasoning_summary(text: str) -> dict[str, Any]:
    """OpenRouter relaying summarised reasoning (e.g. OpenAI models)."""
    return {
        "reasoning_details": [
            {"type": "reasoning.summary", "summary": text, "format": "openai-responses-v1"}
        ]
    }


def chat_completions_handler(
    shape: Callable[[str], dict[str, Any]], seen: list[dict[str, Any]]
) -> Callable[[Any], Any]:
    base = {"id": "x", "object": "chat.completion.chunk", "created": 0, "model": "test-model"}

    def chunk(delta: dict[str, Any], finish: str | None = None) -> dict[str, Any]:
        return {**base, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}

    def handler(request: Any) -> Any:
        body = json.loads(request.content)
        seen.append(body)
        assert body["stream"] is True
        step = 1 if is_second_step(body) else 0
        chunks = [chunk({"role": "assistant", **shape(t)}) for t in THOUGHTS[step]]
        if step == 0:
            chunks += [
                chunk(
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "search_text",
                                    "arguments": '{"query": "Raum"}',
                                },
                            }
                        ]
                    }
                ),
                chunk({}, "tool_calls"),
            ]
        else:
            chunks += [chunk({"content": ANSWER}), chunk({}, "stop")]
        chunks.append(
            {
                **base,
                "choices": [],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
            }
        )
        body_text = sse(chunks) + "data: [DONE]\n\n"
        return httpx2.Response(200, text=body_text, headers={"content-type": "text/event-stream"})

    return handler


class TestOpenAICompatible:
    @pytest.mark.parametrize(
        "shape", [reasoning_content, reasoning, reasoning_details, reasoning_summary]
    )
    def test_streamed_reasoning_reaches_the_chat(
        self, api: Api, shape: Callable[[str], dict[str, Any]]
    ) -> None:
        seen: list[dict[str, Any]] = []
        transport = httpx2.MockTransport(chat_completions_handler(shape, seen))
        api.model = build_chat_model(
            config(Provider.OPENAI, "http://llm.test/v1"),
            http_async_client=httpx2.AsyncClient(transport=transport),
        )
        assert_thoughts_stream_in_order(run_chat(api))

    def test_openrouter_is_asked_for_reasoning_in_its_own_format(self, api: Api) -> None:
        seen: list[dict[str, Any]] = []
        transport = httpx2.MockTransport(chat_completions_handler(reasoning, seen))
        api.model = build_chat_model(
            config(Provider.OPENAI, "https://openrouter.ai/api/v1"),
            http_async_client=httpx2.AsyncClient(transport=transport),
        )
        assert_thoughts_stream_in_order(run_chat(api))
        assert seen[0]["reasoning"] == {"effort": "medium"}
        assert "reasoning_effort" not in seen[0]


# ---------------------------------------------------------------------------- #
# Hosted OpenAI: Responses API with reasoning summaries
# ---------------------------------------------------------------------------- #


def responses_handler(request: Any) -> Any:
    body = json.loads(request.content)
    assert body["stream"] is True
    step = 1 if is_second_step(body) else 0
    rid = f"resp_{step}"
    events: list[dict[str, Any]] = [
        {
            "type": "response.created",
            "sequence_number": 0,
            "response": {
                "id": rid,
                "object": "response",
                "created_at": 0,
                "status": "in_progress",
                "model": "test-model",
                "output": [],
            },
        },
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {"id": f"rs_{step}", "type": "reasoning", "summary": []},
        },
        {
            "type": "response.reasoning_summary_part.added",
            "item_id": f"rs_{step}",
            "output_index": 0,
            "summary_index": 0,
            "part": {"type": "summary_text", "text": ""},
        },
    ]
    events += [
        {
            "type": "response.reasoning_summary_text.delta",
            "item_id": f"rs_{step}",
            "output_index": 0,
            "summary_index": 0,
            "delta": t,
        }
        for t in THOUGHTS[step]
    ]
    summary = "".join(THOUGHTS[step])
    reasoning_item = {
        "id": f"rs_{step}",
        "type": "reasoning",
        "summary": [{"type": "summary_text", "text": summary}],
    }
    events += [
        {
            "type": "response.reasoning_summary_text.done",
            "item_id": f"rs_{step}",
            "output_index": 0,
            "summary_index": 0,
            "text": summary,
        },
        {
            "type": "response.reasoning_summary_part.done",
            "item_id": f"rs_{step}",
            "output_index": 0,
            "summary_index": 0,
            "part": {"type": "summary_text", "text": summary},
        },
        {"type": "response.output_item.done", "output_index": 0, "item": reasoning_item},
    ]
    if step == 0:
        call = {
            "id": "fc_1",
            "type": "function_call",
            "call_id": "call_1",
            "name": "search_text",
            "arguments": '{"query": "Raum"}',
            "status": "completed",
        }
        events += [
            {
                "type": "response.output_item.added",
                "output_index": 1,
                "item": {**call, "arguments": "", "status": "in_progress"},
            },
            {
                "type": "response.function_call_arguments.delta",
                "item_id": "fc_1",
                "output_index": 1,
                "delta": call["arguments"],
            },
            {
                "type": "response.function_call_arguments.done",
                "item_id": "fc_1",
                "output_index": 1,
                "arguments": call["arguments"],
            },
            {"type": "response.output_item.done", "output_index": 1, "item": call},
        ]
        last = call
    else:
        part = {"type": "output_text", "text": ANSWER, "annotations": []}
        message = {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [part],
        }
        events += [
            {
                "type": "response.output_item.added",
                "output_index": 1,
                "item": {**message, "content": [], "status": "in_progress"},
            },
            {
                "type": "response.content_part.added",
                "item_id": "msg_1",
                "output_index": 1,
                "content_index": 0,
                "part": {**part, "text": ""},
            },
            {
                "type": "response.output_text.delta",
                "item_id": "msg_1",
                "output_index": 1,
                "content_index": 0,
                "delta": ANSWER,
            },
            {
                "type": "response.output_text.done",
                "item_id": "msg_1",
                "output_index": 1,
                "content_index": 0,
                "text": ANSWER,
            },
            {
                "type": "response.content_part.done",
                "item_id": "msg_1",
                "output_index": 1,
                "content_index": 0,
                "part": part,
            },
            {"type": "response.output_item.done", "output_index": 1, "item": message},
        ]
        last = message
    events.append(
        {
            "type": "response.completed",
            "response": {
                "id": rid,
                "object": "response",
                "created_at": 0,
                "status": "completed",
                "model": "test-model",
                "output": [reasoning_item, last],
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 10,
                    "total_tokens": 110,
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 5},
                },
            },
        }
    )
    for n, event in enumerate(events):
        event["sequence_number"] = n
    return httpx2.Response(
        200, text=sse(events, named=True), headers={"content-type": "text/event-stream"}
    )


class TestOpenAIHosted:
    def test_reasoning_summaries_reach_the_chat(self, api: Api) -> None:
        api.model = build_chat_model(
            config(Provider.OPENAI),
            http_async_client=httpx2.AsyncClient(transport=httpx2.MockTransport(responses_handler)),
        )
        assert_thoughts_stream_in_order(run_chat(api))


# ---------------------------------------------------------------------------- #
# Anthropic: thinking blocks with signatures
# ---------------------------------------------------------------------------- #


def anthropic_handler(seen: list[dict[str, Any]]) -> Callable[[Any], Any]:
    def handler(request: Any) -> Any:
        body = json.loads(request.content)
        seen.append(body)
        assert body["stream"] is True
        step = 1 if is_second_step(body) else 0
        events: list[dict[str, Any]] = [
            {
                "type": "message_start",
                "message": {
                    "id": f"msg_{step}",
                    "type": "message",
                    "role": "assistant",
                    "model": "test-model",
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 100, "output_tokens": 1},
                },
            },
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "thinking", "thinking": "", "signature": ""},
            },
        ]
        events += [
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": t},
            }
            for t in THOUGHTS[step]
        ]
        events += [
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "signature_delta", "signature": f"sig{step}"},
            },
            {"type": "content_block_stop", "index": 0},
        ]
        if step == 0:
            events += [
                {
                    "type": "content_block_start",
                    "index": 1,
                    "content_block": {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "search_text",
                        "input": {},
                    },
                },
                {
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {"type": "input_json_delta", "partial_json": '{"query": "Raum"}'},
                },
            ]
            stop = "tool_use"
        else:
            events += [
                {
                    "type": "content_block_start",
                    "index": 1,
                    "content_block": {"type": "text", "text": ""},
                },
                {
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {"type": "text_delta", "text": ANSWER},
                },
            ]
            stop = "end_turn"
        events += [
            {"type": "content_block_stop", "index": 1},
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop, "stop_sequence": None},
                "usage": {"output_tokens": 10},
            },
            {"type": "message_stop"},
        ]
        return httpx2.Response(
            200, text=sse(events, named=True), headers={"content-type": "text/event-stream"}
        )

    return handler


class TestAnthropic:
    def test_thinking_reaches_the_chat_and_its_signature_goes_back(self, api: Api) -> None:
        seen: list[dict[str, Any]] = []
        model = build_chat_model(config(Provider.ANTHROPIC))
        # The SDK speaks httpx2: swap in a client on a mock transport.
        model.__dict__["_async_client"] = anthropic.AsyncClient(
            api_key="k",
            base_url="http://anthropic.test",
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(anthropic_handler(seen))),
        )
        api.model = model
        assert_thoughts_stream_in_order(run_chat(api))
        replayed = [
            block
            for m in seen[1]["messages"]
            if m["role"] == "assistant" and isinstance(m["content"], list)
            for block in m["content"]
        ]
        assert {
            "type": "thinking",
            "thinking": "".join(THOUGHTS[0]),
            "signature": "sig0",
        }.items() <= next(b for b in replayed if b["type"] == "thinking").items()


# ---------------------------------------------------------------------------- #
# Gemini: thought parts
# ---------------------------------------------------------------------------- #


def gemini_handler(request: Any) -> Any:
    body = json.loads(request.content)
    step = 1 if is_second_step(body) else 0

    def event(parts: list[dict[str, Any]], finish: str | None = None) -> dict[str, Any]:
        candidate: dict[str, Any] = {"content": {"role": "model", "parts": parts}, "index": 0}
        if finish:
            candidate["finishReason"] = finish
        return {
            "candidates": [candidate],
            "usageMetadata": {
                "promptTokenCount": 100,
                "candidatesTokenCount": 10,
                "totalTokenCount": 110,
            },
            "modelVersion": "test-model",
        }

    events = [event([{"text": t, "thought": True}]) for t in THOUGHTS[step]]
    if step == 0:
        events.append(
            event(
                [
                    {
                        "functionCall": {"name": "search_text", "args": {"query": "Raum"}},
                        "thoughtSignature": "c2lnMA==",
                    }
                ],
                "STOP",
            )
        )
    else:
        events.append(event([{"text": ANSWER}], "STOP"))
    return httpx2.Response(200, text=sse(events), headers={"content-type": "text/event-stream"})


class TestGemini:
    def test_thoughts_reach_the_chat(self, api: Api) -> None:
        model = build_chat_model(config(Provider.GEMINI))
        # The SDK speaks httpx2 (respx does not see it): a client on a mock transport, at a
        # base URL that cannot reach Google even if the transport were bypassed.
        model.client = genai.Client(  # type: ignore[attr-defined]
            api_key="k",
            http_options=genai_types.HttpOptions(
                base_url="http://gemini.test/",
                httpx_async_client=httpx2.AsyncClient(
                    transport=httpx2.MockTransport(gemini_handler)
                ),
            ),
        )
        api.model = model
        assert_thoughts_stream_in_order(run_chat(api))


# ---------------------------------------------------------------------------- #
# Ollama: the ``thinking`` field
# ---------------------------------------------------------------------------- #


def ollama_handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    assert body.get("think") is True
    step = 1 if is_second_step(body) else 0
    common = {"model": "test-model", "created_at": "2026-11-10T12:00:00Z"}
    lines = [
        {**common, "message": {"role": "assistant", "content": "", "thinking": t}, "done": False}
        for t in THOUGHTS[step]
    ]
    if step == 0:
        message: dict[str, Any] = {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "search_text", "arguments": {"query": "Raum"}}}],
        }
    else:
        message = {"role": "assistant", "content": ANSWER}
    lines += [
        {**common, "message": message, "done": False},
        {
            **common,
            "message": {"role": "assistant", "content": ""},
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 80,
            "eval_count": 12,
        },
    ]
    return httpx.Response(200, text="\n".join(json.dumps(line) for line in lines) + "\n")


class TestOllama:
    def test_thinking_reaches_the_chat(self, api: Api) -> None:
        api.web.post("http://ollama.test/api/chat").mock(side_effect=ollama_handler)
        api.model = build_chat_model(config(Provider.OLLAMA, "http://ollama.test"))
        assert_thoughts_stream_in_order(run_chat(api))
