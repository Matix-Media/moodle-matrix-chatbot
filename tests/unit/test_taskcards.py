"""Verifies spec 011 AC-10..AC-13 — TaskCards boards.

The whole mechanism here was reverse-engineered from a real browser network trace
against itech-bs14.taskcards.app (2026-08-21), not guessed: loading a board URL
triggers createVisitor (an anonymous session — confirmed via a direct
unauthenticated request that this is not a credential bound to any account or the
URL's own ?token=) then a board(id) query. This adapter replicates exactly that
sequence, nothing more privileged than what already happens when anyone clicks
the link.
"""

from __future__ import annotations

from typing import Any

from bsbot.ingest.taskcards import (
    board_id_from_url,
    is_taskcards_url,
    render_board_text,
    share_token_from_url,
)


class TestUrlRecognition:
    def test_itech_board_url(self) -> None:
        """AC-10"""
        assert is_taskcards_url(
            "https://itech-bs14.taskcards.app/#/board/a995081c-a67d-4b99-8cf3-934e1c939521?token=x"
        )

    def test_other_taskcards_domains(self) -> None:
        """AC-10: taskcards.de and hibb.taskcards.app are both real, seen on the
        live corpus."""
        assert is_taskcards_url(
            "https://www.taskcards.de/#/board/f84c1dd0-35b7-42d9-874f-3be0d1ac9f2f?token=x"
        )
        assert is_taskcards_url(
            "https://hibb.taskcards.app/#/board/64fc080e-4c51-4ac2-bda7-d209c4e16c26/view?token=x"
        )

    def test_non_taskcards_url(self) -> None:
        assert not is_taskcards_url("https://example.com/#/board/abc")


class TestBoardIdExtraction:
    def test_bare_board_path(self) -> None:
        """AC-10"""
        assert (
            board_id_from_url(
                "https://itech-bs14.taskcards.app/#/board/a995081c-a67d-4b99-8cf3-934e1c939521?token=x"
            )
            == "a995081c-a67d-4b99-8cf3-934e1c939521"
        )

    def test_board_path_with_view_suffix(self) -> None:
        """AC-10: the /view suffix seen on some real corpus URLs."""
        assert (
            board_id_from_url(
                "https://itech-bs14.taskcards.app/#/board/dc39ac08-fee2-4376-907f-8fb15a0e8ecf/view?token=x"
            )
            == "dc39ac08-fee2-4376-907f-8fb15a0e8ecf"
        )

    def test_no_board_path_returns_none(self) -> None:
        assert board_id_from_url("https://itech-bs14.taskcards.app/#/folder/x") is None


class TestRendering:
    def test_cards_are_grouped_by_list(self) -> None:
        """AC-12: a card's context (which list/topic) must survive into the text."""
        board = {
            "name": "IT4 Bili LF10 Q&A Board",
            "lists": [
                {"id": "l1", "name": "Termine"},
                {"id": "l2", "name": "Sport"},
            ],
            "cards": [
                {
                    "title": "Flow",
                    "description": "Wann und wie viel Flow wird es geben?",
                    "kanbanPosition": {"listId": "l1"},
                },
                {
                    "title": "Wie geht es weiter",
                    "description": "Gibt es überhaupt noch Sport?",
                    "kanbanPosition": {"listId": "l2"},
                },
            ],
        }
        text = render_board_text(board)
        assert "IT4 Bili LF10 Q&A Board" in text
        assert "Termine" in text and "Sport" in text
        assert text.index("Termine") < text.index("Flow") < text.index("Sport")
        assert "Wann und wie viel Flow wird es geben?" in text

    def test_card_without_a_list_is_still_included(self) -> None:
        board: dict[str, Any] = {
            "name": "Board",
            "lists": [],
            "cards": [{"title": "Orphan card", "description": "", "kanbanPosition": None}],
        }
        text = render_board_text(board)
        assert "Orphan card" in text

    def test_empty_board_yields_a_reasonable_text(self) -> None:
        board = {"name": "Leeres Board", "lists": [], "cards": []}
        text = render_board_text(board)
        assert "Leeres Board" in text

    def test_html_in_descriptions_is_stripped(self) -> None:
        """Real card descriptions contain raw HTML (<div>, <b>, <br />)."""
        board = {
            "name": "Board",
            "lists": [{"id": "l1", "name": "L"}],
            "cards": [
                {
                    "title": "T",
                    "description": "<div>Text<br /><b>fett</b></div>",
                    "kanbanPosition": {"listId": "l1"},
                }
            ],
        }
        text = render_board_text(board)
        assert "<div>" not in text and "<b>" not in text
        assert "Text" in text and "fett" in text


class TestShareTokenExtraction:
    """Real finding: a board flagged private requires redeeming the URL's own
    ?token= via a REST call before the GraphQL board() query succeeds — verified
    live (itech-bs14.taskcards.app, 2026-08-21) by capturing the real browser's
    network trace after the createVisitor/board(id)-only sequence failed for
    every private board in the corpus.
    """

    def test_extracts_the_share_token(self) -> None:
        assert (
            share_token_from_url(
                "https://itech-bs14.taskcards.app/#/board/e61bd349-c5bc-48b0-95b3-1d615177d8d0"
                "?token=6b0a73b3-5974-4bc7-9c4d-4eca15d98630"
            )
            == "6b0a73b3-5974-4bc7-9c4d-4eca15d98630"
        )

    def test_no_token_returns_none(self) -> None:
        assert share_token_from_url("https://itech-bs14.taskcards.app/#/board/abc") is None


class TestFetchBoardSequence:
    """Pins the exact three-step sequence found live, via respx rather than the
    real network."""

    async def test_redeems_the_share_token_before_querying_the_board(self) -> None:
        import httpx
        import respx

        from bsbot.ingest.taskcards import fetch_board

        host = "example.taskcards.app"
        board_id = "abc-123"
        share_token = "tok-456"

        with respx.mock:
            visitor_route = respx.post(f"https://{host}/graphql").mock(
                side_effect=[
                    httpx.Response(200, json={"data": {"createVisitor": {"id": "visitor-1"}}}),
                    httpx.Response(
                        200,
                        json={"data": {"board": {"name": "B", "lists": [], "cards": []}}},
                    ),
                ]
            )
            redeem_route = respx.post(
                f"https://{host}/api/boards/{board_id}/permissions/{share_token}/accesses"
            ).mock(return_value=httpx.Response(201))

            async with httpx.AsyncClient() as http:
                board = await fetch_board(http, host, board_id, share_token=share_token)

            assert board is not None
            assert redeem_route.called
            assert redeem_route.calls.last.request.headers["x-token"] == "visitor-1"
            assert visitor_route.call_count == 2

    async def test_no_share_token_skips_redemption(self) -> None:
        """A public board's URL carries no token; the redeem call must not fire."""
        import httpx
        import respx

        from bsbot.ingest.taskcards import fetch_board

        host = "example.taskcards.app"
        with respx.mock:
            respx.post(f"https://{host}/graphql").mock(
                side_effect=[
                    httpx.Response(200, json={"data": {"createVisitor": {"id": "v1"}}}),
                    httpx.Response(
                        200, json={"data": {"board": {"name": "B", "lists": [], "cards": []}}}
                    ),
                ]
            )
            redeem_route = respx.post(url__regex=r".*/accesses$")

            async with httpx.AsyncClient() as http:
                board = await fetch_board(http, host, "abc", share_token=None)

            assert board is not None
            assert not redeem_route.called

    async def test_redemption_failure_does_not_abort_the_board_fetch(self) -> None:
        """A failed redemption isn't fatal by itself — the board query still runs
        and decides success, in case the board needed no token after all."""
        import httpx
        import respx

        from bsbot.ingest.taskcards import fetch_board

        host = "example.taskcards.app"
        with respx.mock:
            respx.post(f"https://{host}/graphql").mock(
                side_effect=[
                    httpx.Response(200, json={"data": {"createVisitor": {"id": "v1"}}}),
                    httpx.Response(
                        200, json={"data": {"board": {"name": "B", "lists": [], "cards": []}}}
                    ),
                ]
            )
            respx.post(url__regex=r".*/accesses$").mock(return_value=httpx.Response(500))

            async with httpx.AsyncClient() as http:
                board = await fetch_board(http, host, "abc", share_token="dead-token")

            assert board is not None
