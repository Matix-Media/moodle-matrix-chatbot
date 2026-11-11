"""Integration tier: every real component in-process, only two things faked.

* the chat model (:class:`support.ScriptedModel`) and embeddings
  (:class:`support.HashEmbeddings`) — no provider, no network;
* the Moodle plugin's callback endpoint, mocked at the HTTP level with ``respx`` so
  the real :class:`~moodle_llm_chat.access.moodle.MoodleAccessClient` (auth header,
  caching, fail-closed) is exercised.

Everything else — SQLCipher store, job pipeline, extraction, search, tools,
LangGraph agent, encrypted checkpointer, chat service, websocket protocol, quotas —
is the production code path.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
import respx

from support.harness import CALLBACK, EMBED_DIM, Api, FakeMoodle


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "tests/integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)


@pytest.fixture
def moodle() -> FakeMoodle:
    return FakeMoodle()


@pytest.fixture
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, moodle: FakeMoodle) -> Iterator[Api]:
    monkeypatch.setenv("MLC_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MLC_EMBEDDING__DIM", str(EMBED_DIM))
    monkeypatch.setenv("MLC_PIPELINE__PER_HOST_RATE", "1000")
    harness = Api(tmp_path, os.urandom(32), moodle)
    with respx.mock(assert_all_called=False) as router:
        router.post(CALLBACK).mock(side_effect=moodle.handle)
        router.route(host="testserver").pass_through()
        harness.web = router
        yield harness
