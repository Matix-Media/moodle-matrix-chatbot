"""Stored conversation events, shaped for display.

Every streamed ``token`` and ``thinking`` delta is persisted as its own event so a
reconnecting client can resume mid-turn. For rendering a finished history that is
wasteful: :func:`compact_events` folds each contiguous run into one event.
"""

from __future__ import annotations

from typing import Any

#: Event types whose ``delta`` is concatenated when consecutive within a turn.
STREAMED = frozenset({"token", "thinking"})


def compact_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge consecutive same-type ``token``/``thinking`` events of one turn.

    A merged event carries the concatenated ``delta`` and the ``seq`` (and other fields)
    of the last event of its run, so resuming after it misses nothing. Every other event
    is returned unchanged and in order; the input is not modified.
    """
    out: list[dict[str, Any]] = []
    for event in events:
        prev = out[-1] if out else None
        if (
            prev is not None
            and event.get("type") in STREAMED
            and prev.get("type") == event.get("type")
            and prev.get("turn_id") == event.get("turn_id")
        ):
            out[-1] = {
                **event,
                "delta": str(prev.get("delta") or "") + str(event.get("delta") or ""),
            }
        elif event.get("type") in STREAMED:
            out.append({**event, "delta": str(event.get("delta") or "")})
        else:
            out.append(event)
    return out
