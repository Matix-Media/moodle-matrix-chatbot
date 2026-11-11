"""A script-driven, OpenAI-compatible model server for end-to-end tests.

The API under test is configured with ``MLC_LLM__PROVIDER=openai`` and
``MLC_LLM__BASE_URL=http://mock-llm:9000/v1``, so it talks to this server through the
real OpenAI client and wire format. Every other component is real; only the model's
*decisions* are scripted, which keeps browser tests deterministic.

Control endpoints (used by the test runner):

* ``POST /__script``  ``[step, ...]`` — replace the queue. A step is
  ``{"tool": name, "args": {...}}`` or ``{"text": "...", "delay": seconds}``; an
  optional ``"expect"`` substring must occur in the latest tool result, otherwise the
  model answers ``EXPECTATION FAILED: ...`` so the browser test sees why. An optional
  ``"wait"`` (seconds) holds back the first chunk, like a model that thinks silently;
  an optional ``"reasoning"`` text streams first as OpenRouter-style ``delta.reasoning``,
  spread over ``"reasoning_delay"`` seconds.
* ``GET /__calls`` — every chat request received (messages only), for assertions.
* ``POST /__reset`` — clear queue and history.

``/v1/embeddings`` returns deterministic bag-of-words vectors (``MOCK_EMBED_DIM``).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import os
import re
import struct
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

DIM = int(os.environ.get("MOCK_EMBED_DIM", "64"))
WORD = re.compile(r"\w+", re.UNICODE)

app = FastAPI()
queue: list[dict[str, Any]] = []
calls: list[list[dict[str, Any]]] = []


def embed(text: str) -> list[float]:
    vector = [0.0] * DIM
    for word in WORD.findall(text.casefold()):
        if len(word) >= 3:
            vector[int(hashlib.sha256(word.encode()).hexdigest(), 16) % DIM] += 1.0
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


@app.post("/__script")
async def script(request: Request) -> dict[str, int]:
    steps = await request.json()
    queue.clear()
    queue.extend(steps)
    return {"queued": len(queue)}


@app.post("/__reset")
async def reset() -> dict[str, bool]:
    queue.clear()
    calls.clear()
    return {"ok": True}


@app.get("/__calls")
async def get_calls() -> list[list[dict[str, Any]]]:
    return calls


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/embeddings")
async def embeddings(request: Request) -> JSONResponse:
    body = await request.json()
    inputs = body["input"] if isinstance(body["input"], list) else [body["input"]]
    data = []
    for i, text in enumerate(inputs):
        vector = embed(str(text))
        value: Any = vector
        if body.get("encoding_format") == "base64":
            value = base64.b64encode(struct.pack(f"<{len(vector)}f", *vector)).decode()
        data.append({"object": "embedding", "index": i, "embedding": value})
    return JSONResponse(
        {
            "object": "list",
            "data": data,
            "model": body.get("model", "mock-embed"),
            "usage": {"prompt_tokens": len(inputs), "total_tokens": len(inputs)},
        }
    )


def _last_tool_output(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "tool":
            content = message.get("content")
            return content if isinstance(content, str) else json.dumps(content)
    return ""


def _next_step(messages: list[dict[str, Any]]) -> dict[str, Any]:
    if not queue:
        return {"text": "Ich habe keine weiteren Schritte."}
    step = queue.pop(0)
    expect = step.get("expect")
    if expect and expect not in _last_tool_output(messages):
        return {"text": f"EXPECTATION FAILED: {expect!r} not in the last tool result."}
    return step


def _chunk(delta: dict[str, Any], finish: str | None = None) -> str:
    payload = {
        "id": "mock",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": "mock",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(payload)}\n\n"


async def _stream(step: dict[str, Any], prompt_tokens: int) -> AsyncIterator[str]:
    if step.get("wait"):
        await asyncio.sleep(float(step["wait"]))
    if step.get("reasoning"):
        thoughts = re.findall(r"\S+\s*", str(step["reasoning"]))
        pause = float(step.get("reasoning_delay", 0)) / max(1, len(thoughts))
        for word in thoughts:
            if pause:
                await asyncio.sleep(pause)
            yield _chunk({"role": "assistant", "reasoning": word})
    if "tool" in step:
        call_id = f"call_{int(time.time() * 1000)}"
        yield _chunk(
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": call_id,
                        "type": "function",
                        "function": {"name": step["tool"], "arguments": ""},
                    }
                ],
            }
        )
        yield _chunk(
            {
                "tool_calls": [
                    {"index": 0, "function": {"arguments": json.dumps(step.get("args", {}))}}
                ]
            }
        )
        yield _chunk({}, "tool_calls")
        completion = 10
    else:
        text = str(step["text"])
        words = re.findall(r"\S+\s*", text) or [text]
        delay = float(step.get("delay", 0)) / max(1, len(words))
        yield _chunk({"role": "assistant", "content": ""})
        for word in words:
            if delay:
                await asyncio.sleep(delay)
            yield _chunk({"content": word})
        yield _chunk({}, "stop")
        completion = len(words)
    usage = {
        "id": "mock",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": "mock",
        "choices": [],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion,
            "total_tokens": prompt_tokens + completion,
        },
    }
    yield f"data: {json.dumps(usage)}\n\n"
    yield "data: [DONE]\n\n"


@app.post("/v1/chat/completions")
async def chat(request: Request) -> Any:
    body = await request.json()
    messages = body.get("messages", [])
    calls.append(messages)
    step = _next_step(messages)
    prompt_tokens = sum(len(json.dumps(m)) for m in messages) // 4
    if body.get("stream"):
        return StreamingResponse(_stream(step, prompt_tokens), media_type="text/event-stream")
    message: dict[str, Any] = {"role": "assistant", "content": step.get("text")}
    if "tool" in step:
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": step["tool"],
                        "arguments": json.dumps(step.get("args", {})),
                    },
                }
            ],
        }
    return JSONResponse(
        {
            "id": "mock",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": "mock",
            "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": 5,
                "total_tokens": prompt_tokens + 5,
            },
        }
    )
