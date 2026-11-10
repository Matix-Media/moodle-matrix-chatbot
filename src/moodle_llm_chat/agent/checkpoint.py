"""An encrypted, persistent LangGraph checkpointer on top of the store.

LangGraph's :class:`InMemorySaver` already implements the subtle parts (channel
blobs, versions, delta-channel history). This subclass keeps its in-memory layout as
a write-through cache:

* a thread's rows are loaded and decrypted on first use;
* every ``put`` / ``put_writes`` persists exactly the entries it added, one sealed
  row each (no whole-thread rewrites as conversations grow);
* everything is sealed with the conversation owner's chat key, bound by AAD to its
  row, so one user's agent state is unreadable with another user's key.

Thread ids are ``"<site_id>:<user_id>:<conversation_id>"`` (see :func:`thread_id_for`),
which is how the owner — and so the key — is known.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator, Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from langgraph.checkpoint.memory import InMemorySaver

from moodle_llm_chat.crypto.envelope import aad, open_box, seal
from moodle_llm_chat.index.store import Store


def thread_id_for(site_id: str, user_id: int, conversation_id: str) -> str:
    return f"{site_id}:{user_id}:{conversation_id}"


def _owner(thread_id: str) -> tuple[str, int]:
    parts = thread_id.rsplit(":", 2)
    if len(parts) != 3:
        raise ValueError(f"thread id {thread_id!r} does not name its owner")
    try:
        return parts[0], int(parts[1])
    except ValueError as exc:
        raise ValueError(f"thread id {thread_id!r} does not name its owner") from exc


def _typed_out(value: tuple[str, bytes]) -> dict[str, str]:
    return {"t": value[0], "d": base64.b64encode(value[1]).decode("ascii")}


def _typed_in(value: dict[str, str]) -> tuple[str, bytes]:
    return value["t"], base64.b64decode(value["d"])


class EncryptedCheckpointer(InMemorySaver):
    def __init__(self, store: Store) -> None:
        super().__init__()
        self._store = store
        self._loaded: set[str] = set()

    # -- sealing ----------------------------------------------------------

    def _seal(self, thread_id: str, payload: Any, *where: str | int) -> bytes:
        site_id, user_id = _owner(thread_id)
        return seal(
            self._store.chat_dek(site_id, user_id),
            json.dumps(payload),
            aad=aad("ckpt", thread_id, *where),
        )

    def _open(self, thread_id: str, box: bytes, *where: str | int) -> Any:
        site_id, user_id = _owner(thread_id)
        return json.loads(
            open_box(
                self._store.chat_dek(site_id, user_id),
                bytes(box),
                aad=aad("ckpt", thread_id, *where),
            )
        )

    # -- loading ----------------------------------------------------------

    def _ensure(self, thread_id: str) -> None:
        if thread_id in self._loaded:
            return
        _owner(thread_id)  # validate before touching anything
        storage, writes, blobs = self._store.ckpt_rows(thread_id)
        for r in storage:
            ns, cid = r["ns"], r["checkpoint_id"]
            data = self._open(thread_id, r["sealed"], "s", ns, cid)
            self.storage[thread_id][ns][cid] = (
                _typed_in(data["checkpoint"]),
                _typed_in(data["metadata"]),
                data["parent"],
            )
        for r in writes:
            ns, cid, task, idx = r["ns"], r["checkpoint_id"], r["task_id"], r["idx"]
            data = self._open(thread_id, r["sealed"], "w", ns, cid, task, idx)
            self.writes[(thread_id, ns, cid)][(task, idx)] = (
                task,
                data["channel"],
                _typed_in(data["value"]),
                data["path"],
            )
        for r in blobs:
            ns, channel, version = r["ns"], r["channel"], r["version"]
            data = self._open(thread_id, r["sealed"], "b", ns, channel, version)
            self.blobs[(thread_id, ns, channel, version)] = _typed_in(data)
        self._loaded.add(thread_id)

    @staticmethod
    def _thread(config: RunnableConfig | None) -> str | None:
        if not config:
            return None
        value = config.get("configurable", {}).get("thread_id")
        return str(value) if value is not None else None

    # -- reads ------------------------------------------------------------

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        thread = self._thread(config)
        if thread is not None:
            self._ensure(thread)
        return super().get_tuple(config)

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        thread = self._thread(config)
        if thread is not None:
            self._ensure(thread)
        return super().list(config, filter=filter, before=before, limit=limit)

    # -- writes -----------------------------------------------------------

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        thread = str(config["configurable"]["thread_id"])
        self._ensure(thread)
        result = super().put(config, checkpoint, metadata, new_versions)
        ns = str(config["configurable"].get("checkpoint_ns", ""))
        cid = str(checkpoint["id"])
        stored = self.storage[thread][ns][cid]
        storage_row = (
            thread,
            ns,
            cid,
            self._seal(
                thread,
                {
                    "checkpoint": _typed_out(stored[0]),
                    "metadata": _typed_out(stored[1]),
                    "parent": stored[2],
                },
                "s",
                ns,
                cid,
            ),
        )
        blob_rows = []
        for channel, version in new_versions.items():
            value = self.blobs[(thread, ns, channel, version)]
            blob_rows.append(
                (
                    thread,
                    ns,
                    channel,
                    str(version),
                    self._seal(thread, _typed_out(value), "b", ns, channel, str(version)),
                )
            )
        self._store.ckpt_put(storage=[storage_row], blobs=blob_rows)
        return result

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread = str(config["configurable"]["thread_id"])
        self._ensure(thread)
        super().put_writes(config, writes, task_id, task_path)
        ns = str(config["configurable"].get("checkpoint_ns", ""))
        cid = str(config["configurable"]["checkpoint_id"])
        rows = []
        for (task, idx), (_, channel, value, path) in self.writes[(thread, ns, cid)].items():
            if task != task_id:
                continue
            rows.append(
                (
                    thread,
                    ns,
                    cid,
                    task,
                    int(idx),
                    self._seal(
                        thread,
                        {"channel": channel, "value": _typed_out(value), "path": path},
                        "w",
                        ns,
                        cid,
                        task,
                        int(idx),
                    ),
                )
            )
        self._store.ckpt_put(writes=rows)

    def delete_thread(self, thread_id: str) -> None:
        super().delete_thread(thread_id)
        self._store.ckpt_delete(thread_id)
        self._loaded.discard(thread_id)
