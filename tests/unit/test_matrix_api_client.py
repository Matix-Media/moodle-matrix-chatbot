"""Verifies `ApiClient` — matrix's HTTP client for `api`, replacing direct
Store access. See specs/015-microservice-split.md.
"""

from __future__ import annotations

import httpx
import respx

from bsbot.matrix.api_client import ApiClient

BASE_URL = "http://api:8000"


@respx.mock
def test_answer_calls_api_ask_with_bearer_token_and_returns_an_answer() -> None:
    route = respx.post(f"{BASE_URL}/api/ask").mock(
        return_value=httpx.Response(
            200, json={"text": "Die Prüfung ist am Montag.", "grounded": True}
        )
    )
    client = ApiClient(BASE_URL, "s3cret")

    answer = client.answer("Wann ist die Prüfung?", history=[("a", "b")], room_id="!r:example.org")

    assert answer.text == "Die Prüfung ist am Montag."
    assert answer.grounded is True
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer s3cret"
    assert request.read()  # body was sent


@respx.mock
def test_answer_raises_on_a_non_2xx_response() -> None:
    respx.post(f"{BASE_URL}/api/ask").mock(return_value=httpx.Response(401))
    client = ApiClient(BASE_URL, "wrong")

    try:
        client.answer("Wer ist da?")
    except httpx.HTTPStatusError:
        pass
    else:
        raise AssertionError("expected an HTTPStatusError")


@respx.mock
def test_ingest_message_posts_the_expected_payload() -> None:
    route = respx.post(f"{BASE_URL}/internal/ingest-message").mock(
        return_value=httpx.Response(200, json={"chunk_ids": [1]})
    )
    client = ApiClient(BASE_URL, "s3cret")

    chunk_ids = client.ingest_message(
        room_id="!r:example.org",
        event_id="$e1",
        sender="@teacher:example.org",
        text="Klausur verschoben.",
        timemodified=100,
        room_name="IT4",
        max_history_per_room=10,
    )

    assert chunk_ids == [1]
    body = route.calls.last.request.content
    assert b"Klausur verschoben." in body
    assert route.calls.last.request.headers["Authorization"] == "Bearer s3cret"
