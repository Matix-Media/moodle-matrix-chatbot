"""Async Moodle web-service client — see ``specs/002-moodle-access.md``.

Two behaviours here are worth calling out because they are easy to get wrong and
fail *silently*:

1. ``login/token.php`` reports bad credentials with **HTTP 200** and an ``error``
   body. A naive ``raise_for_status()`` treats that as success and the failure
   resurfaces much later as a baffling "invalid token".
2. The REST endpoint reports application errors the same way — HTTP 200 with an
   ``exception`` key — so every response body is inspected, never just the status.
"""

from __future__ import annotations

import asyncio
import random
from types import TracebackType
from typing import Any, Self

import httpx
import structlog

from bsbot.config import MoodleConfig
from bsbot.moodle.errors import (
    MoodleAPIError,
    MoodleAuthError,
    MoodleFunctionUnavailable,
    MoodleTransportError,
    MoodleWebServicesDisabled,
)
from bsbot.moodle.params import encode_params
from bsbot.moodle.site_info import SiteInfo

log = structlog.get_logger(__name__)

#: Moodle error codes meaning "your identity is wrong" -> fix the credentials.
#: Deliberately excludes ``accessexception``, which means the token is valid but
#: lacks permission for that function: a different problem with a different fix,
#: and one the crawler should skip past rather than treat as a login failure.
_AUTH_ERRORCODES = frozenset(
    {"invalidlogin", "invalidtoken", "tokenexpired", "expiredtoken", "invalidtokenrenew"}
)

#: Retrying these cannot help — they are deterministic.
_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class MoodleClient:
    """Talks to one Moodle site's REST API."""

    def __init__(self, config: MoodleConfig, http: httpx.AsyncClient | None = None) -> None:
        self._config = config
        self._owns_http = http is None
        self._http = http or httpx.AsyncClient(
            timeout=config.timeout_s,
            follow_redirects=True,
            headers={"User-Agent": "bsbot/0.1 (+Berufsschule Matrix assistant)"},
        )
        self._semaphore = asyncio.Semaphore(config.max_concurrency)
        self._token: str | None = (
            config.token.get_secret_value() if config.token is not None else None
        )
        self._login_lock = asyncio.Lock()
        self._site_info: SiteInfo | None = None
        self._site_info_lock = asyncio.Lock()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    # ------------------------------------------------------------------ #
    # Authentication
    # ------------------------------------------------------------------ #

    async def login(self) -> str:
        """Return a web service token, logging in only if we don't already have one.

        Guarded by a lock and cached: repeated failed logins can lock the account,
        so we must never turn one bad password into a burst of attempts.
        """
        if self._token:
            return self._token

        async with self._login_lock:
            if self._token:  # another task won the race
                return self._token

            if not (self._config.username and self._config.password):
                raise MoodleAuthError(
                    "No token configured and no username/password available to obtain one."
                )

            payload = {
                "username": self._config.username,
                "password": self._config.password.get_secret_value(),
                "service": self._config.service,
            }
            response = await self._request(self._config.token_endpoint, payload)
            body = self._decode_json(response)

            if "error" in body or "errorcode" in body:
                self._raise_token_error(body)

            token = body.get("token")
            if not isinstance(token, str) or not token:
                raise MoodleAuthError("Moodle returned no token and no error; response unusable.")

            log.info("moodle.login.success", service=self._config.service)
            self._token = token
            return token

    def _raise_token_error(self, body: dict[str, Any]) -> None:
        """Translate a token.php error body. Never includes the submitted credentials."""
        errorcode = body.get("errorcode")
        message = body.get("error") or body.get("message") or "Moodle rejected the login."

        if errorcode == "enablewsdescription":
            raise MoodleWebServicesDisabled(
                f"{message}\nWeb services are disabled site-wide. A Moodle administrator must "
                "enable them under Site administration > Advanced features.",
                errorcode,
            )
        if errorcode == "invalidlogin":
            raise MoodleAuthError(
                f"{message}\nMoodle rejected these credentials. Note that token.php authenticates "
                "with the Moodle *username*, which is often not the e-mail address, unless the "
                "site allows login via e-mail.",
                errorcode,
            )
        raise MoodleAuthError(f"{message} (errorcode: {errorcode})", errorcode)

    # ------------------------------------------------------------------ #
    # Web service calls
    # ------------------------------------------------------------------ #

    async def call(self, wsfunction: str, **params: Any) -> Any:
        """Invoke a web service function and return its decoded payload."""
        token = await self.login()
        payload = {
            "wstoken": token,
            "wsfunction": wsfunction,
            "moodlewsrestformat": "json",
            **encode_params(params),
        }
        response = await self._request(self._config.rest_endpoint, payload)
        body = self._decode_json(response)
        self._raise_for_ws_exception(body, wsfunction)
        return body

    async def call_if_available(self, wsfunction: str, **params: Any) -> Any:
        """Like :meth:`call`, but check the capability probe first.

        Saves a pointless round trip when we already know the site does not expose
        the function (spec 002 AC-12).
        """
        info = await self.site_info()
        if not info.has(wsfunction):
            raise MoodleFunctionUnavailable(
                f"This Moodle site does not expose {wsfunction!r}; skipping."
            )
        return await self.call(wsfunction, **params)

    @staticmethod
    def _raise_for_ws_exception(body: Any, wsfunction: str) -> None:
        if not isinstance(body, dict) or "exception" not in body:
            return
        errorcode = body.get("errorcode")
        message = body.get("message") or "Moodle returned an exception."
        detail = f"{wsfunction}: {message} (errorcode: {errorcode})"
        if errorcode in _AUTH_ERRORCODES:
            raise MoodleAuthError(detail, errorcode)
        raise MoodleAPIError(detail, errorcode, body.get("exception"))

    # ------------------------------------------------------------------ #
    # Capability probe
    # ------------------------------------------------------------------ #

    async def site_info(self) -> SiteInfo:
        """Fetch and cache ``core_webservice_get_site_info`` (spec 002 AC-11)."""
        if self._site_info is not None:
            return self._site_info
        async with self._site_info_lock:
            if self._site_info is None:
                raw = await self.call("core_webservice_get_site_info")
                self._site_info = SiteInfo.from_payload(raw)
                log.info(
                    "moodle.site_info",
                    sitename=self._site_info.sitename,
                    release=self._site_info.release,
                    functions=len(self._site_info.functions),
                    optimistic=self._site_info.optimistic,
                )
        return self._site_info

    # ------------------------------------------------------------------ #
    # Transport
    # ------------------------------------------------------------------ #

    async def _request(self, url: str, payload: dict[str, str]) -> httpx.Response:
        """POST with bounded concurrency and bounded retries.

        Parameters go in the body (never the query string) so tokens are not
        captured in the school's HTTP access logs — spec 002 AC-6.
        """
        attempts = self._config.max_retries + 1
        last_error: Exception | None = None

        for attempt in range(attempts):
            try:
                async with self._semaphore:  # AC-10
                    response = await self._http.post(url, data=payload)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                if attempt + 1 >= attempts:
                    break
                await self._backoff(attempt)
                continue

            if response.status_code in _RETRYABLE_STATUS:
                last_error = MoodleTransportError(
                    f"Moodle returned HTTP {response.status_code} for {url}"
                )
                if attempt + 1 >= attempts:
                    break
                await self._backoff(attempt)
                continue

            if response.status_code >= 400:
                # Deterministic client error: retrying only wastes the server's time.
                raise MoodleTransportError(f"Moodle returned HTTP {response.status_code} for {url}")

            return response

        raise MoodleTransportError(
            f"Giving up on {url} after {attempts} attempt(s): {last_error}"
        ) from last_error

    async def _backoff(self, attempt: int) -> None:
        """Exponential backoff with jitter, so parallel workers don't retry in lockstep."""
        base = self._config.retry_backoff_s
        if base <= 0:
            return
        delay = base * (2**attempt)
        await asyncio.sleep(delay * (0.5 + random.random() / 2))

    @staticmethod
    def _decode_json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            snippet = response.text[:200].replace("\n", " ")
            raise MoodleTransportError(
                f"Expected JSON from Moodle but got {response.headers.get('content-type')!r}: "
                f"{snippet!r}"
            ) from exc
