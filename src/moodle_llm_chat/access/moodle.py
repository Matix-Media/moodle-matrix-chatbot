"""Ask Moodle, live, whether a user may see specific course modules.

The chat token's cmid list is a snapshot taken when it was issued. Before any
document body is returned to the agent, this client re-asks the plugin
(``local/llmchat/callback.php``, authenticated with the site secret), so a module
hidden or restricted a minute ago is not read. Decisions are cached briefly per
(site, user, cmid). Failure is closed: if Moodle cannot be asked, nothing is read.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Collection
from typing import Any

import httpx
import structlog

from moodle_llm_chat.index.models import Site

log = structlog.get_logger(__name__)

CALLBACK_PATH = "/local/llmchat/callback.php"


class MoodleUnavailable(RuntimeError):
    """Moodle could not confirm access; treat everything as not accessible."""


class MoodleAccessClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        secrets: Callable[[str], list[str]],
        cache_ttl_s: float = 60.0,
        timeout_s: float = 5.0,
        breaker_failures: int = 3,
        breaker_reset_s: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = http
        self._secrets = secrets
        self._ttl = cache_ttl_s
        self._timeout = timeout_s
        self._breaker_failures = breaker_failures
        self._breaker_reset = breaker_reset_s
        self._clock = clock
        self._cache: dict[tuple[str, int, int], tuple[bool, float]] = {}
        self._failures: dict[str, int] = {}
        self._open_until: dict[str, float] = {}

    async def check(self, site: Site, user_id: int, cmids: Collection[int]) -> set[int]:
        """The subset of ``cmids`` the user may see right now."""
        wanted = set(cmids)
        if not wanted:
            return set()
        now = self._clock()
        allowed: set[int] = set()
        unknown: list[int] = []
        for cmid in sorted(wanted):
            hit = self._cache.get((site.site_id, user_id, cmid))
            if hit is not None and hit[1] > now:
                if hit[0]:
                    allowed.add(cmid)
            else:
                unknown.append(cmid)
        if unknown:
            body = await self._call(
                site, {"action": "check_access", "userid": user_id, "cmids": unknown}
            )
            granted = body.get("allowed")
            if not isinstance(granted, list):
                raise MoodleUnavailable("malformed access response")
            granted_set = {int(c) for c in granted if isinstance(c, int | str)} & set(unknown)
            expires = self._clock() + self._ttl
            for cmid in unknown:
                ok = cmid in granted_set
                self._cache[(site.site_id, user_id, cmid)] = (ok, expires)
                if ok:
                    allowed.add(cmid)
        return allowed

    async def user_info(self, site: Site, user_id: int) -> dict[str, Any]:
        """Extra, non-sensitive profile data (group names per course)."""
        body = await self._call(site, {"action": "user_info", "userid": user_id})
        groups = body.get("groups") or {}
        return {"groups": {int(k): [str(g) for g in v] for k, v in groups.items()}}

    def forget_user(self, site_id: str, user_id: int) -> None:
        for key in [k for k in self._cache if k[0] == site_id and k[1] == user_id]:
            del self._cache[key]

    async def _call(self, site: Site, payload: dict[str, Any]) -> dict[str, Any]:
        now = self._clock()
        if self._open_until.get(site.site_id, 0.0) > now:
            raise MoodleUnavailable("circuit open: Moodle recently unreachable")
        url = f"{site.url}{CALLBACK_PATH}"
        try:
            body = await self._post(url, site.site_id, payload)
        except MoodleUnavailable:
            failures = self._failures.get(site.site_id, 0) + 1
            self._failures[site.site_id] = failures
            if failures >= self._breaker_failures:
                self._open_until[site.site_id] = self._clock() + self._breaker_reset
                self._failures[site.site_id] = 0
                log.warning("moodle.circuit_open", site=site.site_id)
            raise
        self._failures[site.site_id] = 0
        return body

    async def _post(self, url: str, site_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        secrets = self._secrets(site_id)
        for index, secret in enumerate(secrets):
            try:
                response = await self._http.post(
                    url,
                    json=payload,
                    headers={"Authorization": f"Bearer {secret}"},
                    timeout=self._timeout,
                )
            except httpx.HTTPError as exc:
                raise MoodleUnavailable(f"{type(exc).__name__}") from exc
            if response.status_code == 401 and index + 1 < len(secrets):
                continue  # the plugin may still hold the previous secret
            if response.status_code != 200:
                raise MoodleUnavailable(f"HTTP {response.status_code}")
            try:
                body = response.json()
            except ValueError as exc:
                raise MoodleUnavailable("non-JSON response") from exc
            if not isinstance(body, dict):
                raise MoodleUnavailable("unexpected response")
            return body
        raise MoodleUnavailable("no site secret")
