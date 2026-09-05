"""Connects the bot to a homeserver — see ``specs/009-matrix-bot.md``.

Two details decide whether an encrypted bot works at all:

* **Session reuse.** Logging in afresh on every start creates a new device each time.
  Old devices pile up, other clients see a swarm of unverified devices, and history
  encrypted to previous devices becomes unreadable. So the device id and access token
  are persisted after the first login and reused.
* **Device trust.** In an encrypted room the bot cannot read anything from devices it
  refuses to trust. Interactive verification is impractical for a bot, so devices in
  the configured rooms are trusted automatically. That is a deliberate trade-off:
  it accepts the room's devices at face value in exchange for the bot working at all.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import structlog
from nio import (
    AsyncClient,
    AsyncClientConfig,
    InviteMemberEvent,
    LoginResponse,
    MatrixRoom,
    MegolmEvent,
    RoomMessageText,
    SyncError,
    SyncResponse,
)

from bsbot.config import MatrixConfig
from bsbot.matrix.bot import BerufsschuleBot, BotPolicy, PipelineLike

log = structlog.get_logger(__name__)

#: Paces sync_forever between iterations. This is the actual fix for a real
#: production incident: nio's own sync loop only self-paces via a *successful*
#: response's server-side long-poll wait. A response that fails nio's schema
#: validation (nio/responses.py's ``verify()`` decorator converts it to a
#: SyncError rather than raising) returns immediately — with no loop_sleep_time
#: set, that produced hundreds of zero-delay requests per second against
#: matrix.org from a single bad /sync response, observed live on 2026-08-21.
LOOP_SLEEP_TIME_MS = 1000

#: Matrix error codes that unambiguously mean "this token is no longer valid" —
#: not M_FORBIDDEN, which can mean many unrelated things (wrong room, banned,
#: ...) and would make a proactive refresh a guess rather than a diagnosis.
_TOKEN_REJECTED_CODES = frozenset({"M_UNKNOWN_TOKEN", "M_MISSING_TOKEN"})

#: A laptop's lid closing (or any real network blip — a Wi-Fi drop, a homeserver
#: restart) tears the connection down mid-read. nio does not catch the resulting
#: httpx exception (observed live on 2026-08-21: an uncaught ReadError killed the
#: whole process), so nothing above it in the stack gets a chance to reconnect —
#: without an outer restart loop, a routine sleep/wake cycle is fatal.
RESTART_BACKOFF_INITIAL_S = 5.0
RESTART_BACKOFF_MAX_S = 300.0
#: A run lasting at least this long counts as "recovered" — the next failure
#: backs off from scratch rather than compounding on an old streak from hours ago.
RESTART_HEALTHY_UPTIME_S = 120.0


def is_token_rejected_error(status_code: str | None) -> bool:
    return status_code in _TOKEN_REJECTED_CODES


def looks_like_email(identifier: str) -> bool:
    """Whether this is an e-mail rather than an MXID or a bare localpart.

    An MXID also contains ``@`` — but at the start — so the check is deliberately
    stricter than "contains an @".
    """
    if identifier.startswith("@"):
        return False
    local, _, domain = identifier.partition("@")
    return bool(local and "." in domain)


def build_login_auth(identifier: str, password: str, *, device_name: str) -> dict[str, Any]:
    """Build an ``m.login.password`` auth dict, by MXID or by e-mail.

    nio's ``login()`` always sends ``m.id.user``; logging in with an e-mail needs the
    third-party identifier form, which is why this is assembled by hand and passed to
    ``login_raw``.
    """
    if looks_like_email(identifier):
        ident: dict[str, Any] = {
            "type": "m.id.thirdparty",
            "medium": "email",
            "address": identifier,
        }
    else:
        ident = {"type": "m.id.user", "user": identifier}
    return {
        "type": "m.login.password",
        "identifier": ident,
        "password": password,
        "initial_device_display_name": device_name,
    }


class MatrixRunner:
    def __init__(
        self,
        config: MatrixConfig,
        pipeline: PipelineLike,
        *,
        store_dir: Path,
        answer_all: bool = False,
        trust_room_devices: bool = True,
        persist_tokens: Callable[[dict[str, str]], None] | None = None,
        store: Any | None = None,
        embedder: Any | None = None,
        pii_tokenizer: Any | None = None,
    ) -> None:
        self._config = config
        self._pipeline = pipeline
        self._store_dir = store_dir
        self._answer_all = answer_all
        self._trust = trust_room_devices
        self._persist_tokens = persist_tokens
        self._store = store
        self._embedder = embedder
        self._pii_tokenizer = pii_tokenizer
        self._token_lifetime = 0
        # Mutable, unlike self._config: MAS rotates the refresh token on every
        # use, and this is what every subsequent refresh within this process's
        # lifetime actually sends — reading straight from the frozen config
        # instead would keep resending the token from the very first refresh,
        # which MAS has already invalidated by the second one. Found live: a
        # deployment failed its very first scheduled 3-hourly renewal, exactly
        # once, on every run, because renewal only ever reused the startup value.
        self._refresh_token_value = (
            config.refresh_token.get_secret_value() if config.refresh_token else None
        )
        self._session_file = store_dir / "session.json"
        self._client: AsyncClient | None = None
        self._bot: BerufsschuleBot | None = None

    async def run(self) -> None:
        self._store_dir.mkdir(parents=True, exist_ok=True)
        client = AsyncClient(
            self._config.homeserver,
            self._config.user_id,
            store_path=str(self._store_dir),
            config=AsyncClientConfig(
                store_sync_tokens=True,
                encryption_enabled=True,
                # Unlimited: this is a long-running service, and a laptop sleeping
                # or a Wi-Fi drop produces an ordinary sync timeout. The previous
                # values of 0 meant "give up after the very first one" — that is
                # what crashed the process the first time a connection blipped.
                max_limit_exceeded=None,
                max_timeouts=None,
            ),
        )
        self._client = client

        try:
            await self._authenticate(client)
            # Loading the store is what makes E2EE survive a restart.
            if client.should_upload_keys:
                await client.keys_upload()

            policy = BotPolicy(
                room_ids=set(self._config.room_ids),
                user_id=client.user_id,
                display_name=self._config.device_name,
                answer_all=self._answer_all,
            )
            self._bot = BerufsschuleBot(
                client,
                self._pipeline,
                policy,
                store=self._store,
                embedder=self._embedder,
                pii_tokenizer=self._pii_tokenizer,
            )

            client.add_event_callback(self._on_message, RoomMessageText)
            client.add_event_callback(self._on_invite, InviteMemberEvent)
            client.add_event_callback(self._on_undecryptable, MegolmEvent)
            client.add_response_callback(self._on_sync, SyncResponse)

            client.add_response_callback(self._on_sync_error, SyncError)

            log.info(
                "matrix.starting",
                user=client.user_id,
                device=client.device_id,
                rooms=len(self._config.room_ids),
                encryption=client.olm is not None,
            )
            # Renew the access token while running; matrix.org issues 4-hour tokens,
            # so refreshing only at startup would cap the bot's uptime at four hours.
            renewer = asyncio.create_task(self._renew_forever(client))
            try:
                await client.sync_forever(
                    timeout=30_000, full_state=True, loop_sleep_time=LOOP_SLEEP_TIME_MS
                )
            finally:
                renewer.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await renewer
        finally:
            await client.close()

    async def _renew_forever(self, client: AsyncClient) -> None:
        """Background loop that keeps the access token fresh."""
        from bsbot.matrix.oauth import seconds_until_refresh

        if not (self._config.refresh_token and self._config.oauth_client_id):
            return
        delay = seconds_until_refresh(self._token_lifetime)
        while True:
            await asyncio.sleep(delay)
            token = await self._refresh_token()
            if token:
                client.access_token = token
                delay = seconds_until_refresh(self._token_lifetime)
            else:
                # Retry sooner than a full cycle, but do not spin.
                delay = 300
                log.warning("matrix.token.renew_failed_retrying", seconds=delay)

    # ------------------------------------------------------------------ #

    async def _authenticate(self, client: AsyncClient) -> None:
        """Prefer a configured token, then a stored session, then a password login."""
        # AC-16: a configured token means no login request at all. This is the route
        # that works on homeservers using next-gen auth, where m.login.password is
        # advertised but rejected.
        if self._config.access_token is not None:
            token = self._config.access_token.get_secret_value()
            # MAS access tokens live only minutes, so refresh up front rather than
            # starting with one that may already be dead. This is what makes a
            # redeploy unattended.
            refreshed = await self._refresh_token()
            client.access_token = refreshed or token
            client.user_id = self._config.user_id
            device_id = self._config.device_id or await self._discover_device_id(client)
            client.device_id = device_id
            client.load_store()
            log.info("matrix.token.configured", device=device_id, refreshed=bool(refreshed))
            return

        session = self._load_session()
        if session:
            client.access_token = session["access_token"]
            client.user_id = session["user_id"]
            client.device_id = session["device_id"]
            client.load_store()
            log.info("matrix.session.restored", device=client.device_id)
            return

        if self._config.password is None:
            raise RuntimeError("No Matrix access token and no password configured.")
        identifier = self._config.login_identifier or self._config.user_id
        response = await client.login_raw(
            build_login_auth(
                identifier,
                self._config.password.get_secret_value(),
                device_name=self._config.device_name,
            )
        )
        if not isinstance(response, LoginResponse):
            raise RuntimeError(f"Matrix login failed: {response}")
        self._save_session(
            {
                "access_token": response.access_token,
                "user_id": response.user_id,
                "device_id": response.device_id,
            }
        )
        log.info("matrix.session.created", device=response.device_id)

    async def _refresh_token(self) -> str | None:
        """Exchange the current refresh token for a fresh access token.

        Returns ``None`` when no refresh credentials are configured, in which case
        the configured access token is used as-is. MAS rotates refresh tokens on
        every use — the new pair is written back to disk immediately (losing it
        would force the browser flow again) *and* kept in memory
        (``self._refresh_token_value``), since the next refresh within this same
        process needs the just-rotated value, not the one from the very first
        refresh at startup.
        """
        cfg = self._config
        refresh_token = self._refresh_token_value
        if not (refresh_token and cfg.oauth_client_id and cfg.oauth_token_endpoint):
            return None

        from bsbot.matrix.oauth import DeviceGrantError, refresh_access_token

        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as http:
                tokens = await refresh_access_token(
                    http,
                    cfg.oauth_token_endpoint,
                    cfg.oauth_client_id,
                    refresh_token,
                )
        except DeviceGrantError as exc:
            log.warning("matrix.token.refresh_failed", error=str(exc))
            return None

        if tokens.refresh_token:
            self._refresh_token_value = tokens.refresh_token
        if self._persist_tokens is not None:
            updates = {"BSBOT_MATRIX__ACCESS_TOKEN": tokens.access_token}
            if tokens.refresh_token:
                updates["BSBOT_MATRIX__REFRESH_TOKEN"] = tokens.refresh_token
            self._persist_tokens(updates)
        self._token_lifetime = tokens.expires_in
        log.info("matrix.token.refreshed", expires_in=tokens.expires_in)
        return tokens.access_token

    async def _discover_device_id(self, client: AsyncClient) -> str:
        """Ask the homeserver which device this token belongs to (AC-17).

        Message keys are bound to a device, so an encrypted bot with the wrong (or
        no) device id runs happily and decrypts nothing.
        """
        response = await client.whoami()
        device_id = getattr(response, "device_id", None)
        if not device_id:
            raise RuntimeError(
                "Could not determine the device id for this access token "
                f"({getattr(response, 'message', response)}). Set "
                "BSBOT_MATRIX__DEVICE_ID explicitly — in Element it is shown as the "
                "Session ID under Settings > Help & About > Advanced."
            )
        return str(device_id)

    def _load_session(self) -> dict[str, str] | None:
        if not self._session_file.exists():
            return None
        try:
            data = json.loads(self._session_file.read_text())
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        if all(isinstance(data.get(k), str) for k in ("access_token", "user_id", "device_id")):
            return {k: str(v) for k, v in data.items()}
        return None

    def _save_session(self, data: dict[str, str]) -> None:
        self._session_file.write_text(json.dumps(data, indent=2))
        self._session_file.chmod(0o600)  # it holds an access token

    # ------------------------------------------------------------------ #

    async def _on_message(self, room: MatrixRoom, event: RoomMessageText) -> None:
        assert self._bot is not None
        try:
            await self._bot.handle_message(room, event)
        except Exception as exc:  # a handler crash must not kill the sync loop
            log.warning("matrix.handler_failed", error=f"{type(exc).__name__}: {exc}")

    async def _on_invite(self, room: MatrixRoom, event: InviteMemberEvent) -> None:
        """Join only rooms we were configured for."""
        client = self._client
        if client is None:
            return
        if event.membership != "invite" or event.state_key != client.user_id:
            return
        if room.room_id not in self._config.room_ids:
            log.info("matrix.invite.ignored", room=room.room_id)
            return
        await client.join(room.room_id)
        log.info("matrix.invite.accepted", room=room.room_id)

    async def _on_undecryptable(self, room: MatrixRoom, event: MegolmEvent) -> None:
        """AC-12: a message we lack keys for is normal, not fatal.

        It happens for anything sent before the bot joined, or while it was offline.
        """
        log.info("matrix.undecryptable", room=room.room_id, sender=event.sender)

    async def _on_sync(self, response: SyncResponse) -> None:
        if self._trust:
            self._trust_room_devices()

    async def _on_sync_error(self, response: SyncError) -> None:
        """Log every sync failure through our own logging, and self-heal a
        rejected token rather than retrying it forever (AC-26).

        Raises when the refresh itself fails, rather than returning quietly.
        nio's ``sync_forever`` re-raises any exception a response callback
        raises and stops (a bare ``except: raise`` in
        ``nio/client/async_client.py``, confirmed by reading the installed
        source directly), which hands control to ``_run_with_restart``'s
        backoff. Without this, a token rejection whose refresh can never
        succeed — a revoked or expired refresh token — turned into a refresh
        attempt on every single sync iteration forever: observed live, roughly
        once a second, hammering the identity server indefinitely with a
        request that could never succeed.
        """
        log.warning(
            "matrix.sync_error",
            status_code=response.status_code,
            message=response.message,
        )
        if not is_token_rejected_error(response.status_code):
            return
        log.warning("matrix.token.rejected_refreshing")
        token = await self._refresh_token()
        if token and self._client is not None:
            self._client.access_token = token
            return
        raise RuntimeError(
            "Matrix token was rejected and could not be refreshed. The refresh "
            "token may be revoked or expired — run 'bsbot matrix-login' to re-authorise."
        )

    def _trust_room_devices(self) -> None:
        """Trust devices in our rooms so we can actually decrypt (see module docstring)."""
        client = self._client
        if client is None or client.olm is None:
            return
        for room_id in self._config.room_ids:
            room = client.rooms.get(room_id)
            if room is None:
                continue
            for user_id in room.users:
                for device in client.device_store.active_user_devices(user_id):
                    if device.deleted or client.olm.is_device_verified(device):
                        continue
                    client.verify_device(device)


async def _run_with_restart(
    run_once: Callable[[], Awaitable[None]],
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Restart ``run_once`` after any failure, with backoff.

    A persistent problem (bad config, a homeserver that is actually down) should
    not be hammered with instant retries — but a transient one (see the module
    docstring: a laptop waking from sleep, a Wi-Fi blip) should recover within
    seconds, not stay dead until someone notices and restarts it by hand.
    """
    backoff = RESTART_BACKOFF_INITIAL_S
    while True:
        started = clock()
        try:
            await run_once()
            return
        except asyncio.CancelledError:
            log.info("matrix.stopped")
            raise
        except Exception as exc:
            if clock() - started >= RESTART_HEALTHY_UPTIME_S:
                backoff = RESTART_BACKOFF_INITIAL_S
            log.warning(
                "matrix.crashed_restarting",
                error=f"{type(exc).__name__}: {exc}",
                retry_in_seconds=backoff,
            )
            await sleep(backoff)
            backoff = min(backoff * 2, RESTART_BACKOFF_MAX_S)


async def run_bot(
    config_factory: Callable[[], MatrixConfig],
    pipeline: PipelineLike,
    *,
    store_dir: Path,
    answer_all: bool = False,
    persist_tokens: Callable[[dict[str, str]], None] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    store: Any | None = None,
    embedder: Any | None = None,
    pii_tokenizer: Any | None = None,
) -> None:
    """Run the bot, restarting with backoff on any failure (see _run_with_restart).

    ``config_factory`` is called again on every restart attempt, not just once —
    otherwise a token rotated on disk by something else (another process, a
    manual ``matrix-login``) while this one was stuck retrying with a stale,
    already-rejected refresh token would never be noticed: the process would
    keep retrying the exact value it started with, forever, instead of picking
    up the valid one sitting right next to it. Observed live: exactly this,
    diagnosed from a refresh token that died precisely on schedule (3h into a
    4h token) with no activity from this process in between, meaning something
    else had already consumed it.
    """

    async def run_once() -> None:
        runner = MatrixRunner(
            config_factory(),
            pipeline,
            store_dir=store_dir,
            answer_all=answer_all,
            persist_tokens=persist_tokens,
            store=store,
            embedder=embedder,
            pii_tokenizer=pii_tokenizer,
        )
        await runner.run()

    await _run_with_restart(run_once, sleep=sleep)


__all__ = ["MatrixRunner", "run_bot"]
