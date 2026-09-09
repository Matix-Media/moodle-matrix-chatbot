"""TaskCards boards — see ``specs/011-external-adapters.md``.

The whole access mechanism was reverse-engineered from a real browser network
trace against itech-bs14.taskcards.app (2026-08-21), not guessed from docs:
loading a board URL triggers a ``createVisitor`` mutation (an anonymous session —
confirmed via a direct unauthenticated request that this is *not* a credential
bound to any account, the board owner, or the URL's own ``?token=``) and then a
``board(id)`` query, which returns full content for both a public board and one
flagged ``private: true``. This module replicates exactly that sequence — the
same access path already available to anyone who clicks the link a teacher pasted
into Moodle, nothing more privileged.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import structlog

log = structlog.get_logger(__name__)

_TASKCARDS_HOSTS_SUFFIX = "taskcards.app"
_TASKCARDS_HOSTS = frozenset({"taskcards.de", "www.taskcards.de"})

_BOARD_PATH_RE = re.compile(r"/board/([0-9a-fA-F-]{36})")

_CREATE_VISITOR = "mutation { createVisitor { id } }"

#: Only the fields the text rendering actually uses — the real schema (see the
#: spec) is far larger, but asking for less is both faster and simpler to keep
#: in sync with a service we don't control.
_BOARD_QUERY = """
query ($id: String!) {
  board(id: $id) {
    name
    lists { id name }
    cards { title description kanbanPosition { listId } }
  }
}
"""


def is_taskcards_url(url: str) -> bool:
    return board_id_from_url(url) is not None if _is_taskcards_host(url) else False


def _is_taskcards_host(url: str) -> bool:
    host = urlsplit(url).netloc
    return host.endswith(_TASKCARDS_HOSTS_SUFFIX) or host in _TASKCARDS_HOSTS


def board_id_from_url(url: str) -> str | None:
    """Pull the board UUID out of a TaskCards URL (AC-10)."""
    if not _is_taskcards_host(url):
        return None
    match = _BOARD_PATH_RE.search(urlsplit(url).path or urlsplit(url).fragment)
    # The board id lives in the URL *fragment* (a hash-routed SPA: #/board/<id>),
    # which urlsplit keeps separate from .path — check both.
    if match is None:
        match = _BOARD_PATH_RE.search(urlsplit(url).fragment)
    return match.group(1) if match else None


def share_token_from_url(url: str) -> str | None:
    """Pull the share ``?token=`` out of a TaskCards URL.

    Distinct from the anonymous ``x-token`` session (see the module docstring):
    this one is the actual per-board share credential. Found live: a board
    flagged ``private: true`` returns nothing from the ``board(id)`` query unless
    this token is first redeemed via a REST call (see ``fetch_board``) — every
    private board in the corpus failed until this step was added.
    """
    parts = urlsplit(url)
    # The token lives inside the fragment's own query string for this hash-routed
    # SPA (#/board/<id>?token=...), not the URL's real query component.
    _, _, fragment_query = parts.fragment.partition("?")
    values = parse_qs(fragment_query or parts.query).get("token")
    return values[0] if values else None


async def fetch_board(
    http: httpx.AsyncClient, host: str, board_id: str, *, share_token: str | None = None
) -> dict[str, Any] | None:
    """Run the real createVisitor -> [redeem share token] -> board(id) sequence.

    The middle step is the one that is easy to miss and was missed on the first
    attempt here: a board flagged ``private: true`` returns nothing from the
    ``board(id)`` query unless the URL's own ``?token=`` is first redeemed via
    ``POST /api/boards/<id>/permissions/<token>/accesses`` — found live by
    capturing a real browser's network trace after every private board in the
    corpus failed under the createVisitor-then-board(id)-only sequence. A public
    board (``private: false``) needs no redemption and works either way, so this
    step is attempted whenever the URL carries a token, unconditionally.

    Returns ``None`` on any failure (AC-13) — a deleted or genuinely
    inaccessible board is a clean skip, not a crash. A failed *redemption* is
    not fatal by itself: the subsequent board query is still attempted and is
    what ultimately decides success, in case the board turns out to need no
    token after all.
    """
    endpoint = f"https://{host}/graphql"
    try:
        visitor_response = await http.post(
            endpoint, json={"operationName": None, "variables": {}, "query": _CREATE_VISITOR}
        )
        visitor_response.raise_for_status()
        visitor_data = visitor_response.json()
        token = visitor_data["data"]["createVisitor"]["id"]

        if share_token:
            try:
                redeem_response = await http.post(
                    f"https://{host}/api/boards/{board_id}/permissions/{share_token}/accesses",
                    headers={"x-token": token},
                    json={"password": ""},
                )
                redeem_response.raise_for_status()
            except httpx.HTTPError as exc:
                log.info("taskcards.redeem_failed", board_id=board_id, error=str(exc))

        board_response = await http.post(
            endpoint,
            json={"operationName": None, "variables": {"id": board_id}, "query": _BOARD_QUERY},
            headers={"x-token": token},
        )
        board_response.raise_for_status()
        board_data = board_response.json()
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        log.info("taskcards.fetch_failed", board_id=board_id, error=str(exc))
        return None

    if board_data.get("errors") or not board_data.get("data", {}).get("board"):
        log.info("taskcards.board_unavailable", board_id=board_id, errors=board_data.get("errors"))
        return None
    board: dict[str, Any] = board_data["data"]["board"]
    return board


_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", text)).strip()


def render_board_text(board: dict[str, Any]) -> str:
    """Render lists and cards as text, grouped by list (AC-12).

    A card's list membership is its context (which topic/Lernfeld it belongs to)
    — losing that would strand a card's meaning the same way a bare paragraph
    loses its section heading.
    """
    lists = {lst["id"]: lst["name"] for lst in board.get("lists") or []}
    by_list: dict[str, list[dict[str, Any]]] = {name: [] for name in lists.values()}
    unlisted: list[dict[str, Any]] = []

    for card in board.get("cards") or []:
        position = card.get("kanbanPosition") or {}
        list_id = position.get("listId")
        list_name = lists.get(list_id)
        (by_list[list_name] if list_name else unlisted).append(card)

    parts = [board.get("name") or "TaskCards-Board"]
    for list_name, cards in by_list.items():
        if not cards:
            continue
        parts.append(f"\n{list_name}:")
        for card in cards:
            title = _strip_html(card.get("title") or "")
            description = _strip_html(card.get("description") or "")
            line = f"- {title}"
            if description:
                line += f": {description}"
            parts.append(line)
    for card in unlisted:
        title = _strip_html(card.get("title") or "")
        description = _strip_html(card.get("description") or "")
        parts.append(f"- {title}" + (f": {description}" if description else ""))

    return "\n".join(parts)
