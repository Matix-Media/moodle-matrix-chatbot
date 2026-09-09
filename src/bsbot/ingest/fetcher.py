"""On-demand fetching with a cache freshness ladder — ``specs/005-fetch-cache.md``.

The corpus is 544 MB across 422 documents. Re-downloading that on every sync would
be both slow and rude to the school's server, but never re-checking would let the
bot answer from outdated material. The ladder resolves that: each rung is tried only
when the cheaper one above is inconclusive.

    1. no cache entry            -> download
    2. Moodle timemodified moved -> download        (free: known from the crawl)
    3. inside the soft TTL       -> serve cache     (no network at all)
    4. TTL elapsed               -> conditional GET (a 304 costs a few hundred bytes)
    5. bytes differ              -> new blob + re-extract

Depends only on a small ``FetchCache`` protocol, not a concrete ``Store`` — so the
same fetch/cache logic runs unchanged whether the cache is a local ``Store`` (the
`bsbot index` dev tool) or an HTTP-backed one talking to the `api` service (`cron`,
which never opens the SQLite file itself — see specs/019-microservice-split.md).
"""

from __future__ import annotations

import asyncio
import enum
import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from types import TracebackType
from typing import Protocol, Self
from urllib.parse import urlsplit

import httpx
import structlog

from bsbot.index.store import FetchRecord

log = structlog.get_logger(__name__)

DEFAULT_TTL_S = 24 * 60 * 60
DEFAULT_FAILURE_COOLDOWN_S = 6 * 60 * 60
DEFAULT_MAX_BYTES = 25 * 1024 * 1024
_CHUNK = 64 * 1024


class FetchCache(Protocol):
    """Everything `Fetcher` needs from a blob/fetch-bookkeeping cache."""

    def fetch_record(self, url: str) -> FetchRecord | None: ...
    def blob_exists(self, sha256: str) -> bool: ...
    def read_blob(self, sha256: str) -> bytes: ...
    def put_blob(self, data: bytes) -> str: ...

    def record_fetch(
        self,
        url: str,
        *,
        sha256: str | None,
        etag: str | None = None,
        last_modified: str | None = None,
        moodle_timemodified: int | None = None,
        fetched_at: int | None = None,
        checked_at: int | None = None,
    ) -> None: ...

    def record_fetch_failure(
        self, url: str, *, error: str, checked_at: int | None = None
    ) -> None: ...

    def touch_fetch(self, url: str, *, checked_at: int) -> None: ...


class FetchOutcome(enum.StrEnum):
    DOWNLOADED = "downloaded"  # first time we have seen this URL
    CACHED = "cached"  # inside TTL, no request made
    NOT_MODIFIED = "not_modified"  # server said 304
    UNCHANGED = "unchanged"  # re-downloaded, identical bytes
    CHANGED = "changed"  # new bytes -> needs re-extraction
    FAILED = "failed"
    SKIPPED = "skipped"  # known-bad URL inside its cooldown

    @property
    def needs_extraction(self) -> bool:
        return self in (FetchOutcome.DOWNLOADED, FetchOutcome.CHANGED)


@dataclass
class FetchResult:
    url: str
    outcome: FetchOutcome
    sha256: str | None = None
    error: str | None = None
    filename: str | None = None
    #: Present whenever `sha256` is (i.e. every outcome a caller would want to
    #: extract from) — read from cache for CACHED/NOT_MODIFIED, or the bytes
    #: just downloaded otherwise. Callers with no local blob cache of their own
    #: (cron) need this to avoid a second round trip just to read the bytes back.
    data: bytes | None = None


class Fetcher:
    def __init__(
        self,
        cache: FetchCache,
        *,
        moodle_token: str | None = None,
        moodle_host: str | None = None,
        ttl_s: int = DEFAULT_TTL_S,
        failure_cooldown_s: int = DEFAULT_FAILURE_COOLDOWN_S,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_concurrency: int = 4,
        clock: Callable[[], int] = lambda: int(time.time()),
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._cache = cache
        self._token = moodle_token
        self._moodle_host = moodle_host
        self._ttl = ttl_s
        self._cooldown = failure_cooldown_s
        self._max_bytes = max_bytes
        self._clock = clock
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._owns_http = http is None
        self._http = http or httpx.AsyncClient(
            timeout=60.0,
            follow_redirects=True,
            headers={"User-Agent": "bsbot/0.1 (+Berufsschule Matrix assistant)"},
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def fetch(self, url: str, *, moodle_timemodified: int | None = None) -> FetchResult:
        now = self._clock()
        record = self._cache.fetch_record(url)

        # Rung 0: a URL that recently failed stays failed for a while (AC-9).
        if (
            record is not None
            and record.error
            and record.checked_at is not None
            and now - record.checked_at < self._cooldown
        ):
            return FetchResult(url, FetchOutcome.SKIPPED, error=record.error)

        cached = (
            record is not None
            and record.sha256 is not None
            and self._cache.blob_exists(record.sha256)
        )
        if cached:
            assert record is not None and record.sha256 is not None
            moved = (
                moodle_timemodified is not None
                and record.moodle_timemodified is not None
                and moodle_timemodified != record.moodle_timemodified
            )
            fresh = record.checked_at is not None and now - record.checked_at < self._ttl
            # Rung 2 beats rung 3: Moodle's own timestamp is authoritative and free.
            if not moved and fresh:
                return FetchResult(
                    url,
                    FetchOutcome.CACHED,
                    sha256=record.sha256,
                    data=self._cache.read_blob(record.sha256),
                )
            return await self._revalidate(url, record, moodle_timemodified, now, force=moved)

        return await self._download(url, record, moodle_timemodified, now, conditional=False)

    async def _revalidate(
        self,
        url: str,
        record: FetchRecord | None,
        moodle_timemodified: int | None,
        now: int,
        *,
        force: bool,
    ) -> FetchResult:
        blob_exists = (
            record is not None
            and record.sha256 is not None
            and self._cache.blob_exists(record.sha256)
        )
        # When Moodle says the file moved or blob is missing, validators would only mislead us.
        return await self._download(
            url, record, moodle_timemodified, now, conditional=(not force and blob_exists)
        )

    async def _download(
        self,
        url: str,
        record: FetchRecord | None,
        moodle_timemodified: int | None,
        now: int,
        *,
        conditional: bool,
    ) -> FetchResult:
        headers: dict[str, str] = {}
        if conditional and record is not None:
            if record.etag:
                headers["If-None-Match"] = record.etag
            if record.last_modified:
                headers["If-Modified-Since"] = record.last_modified

        request_url = self._authenticated(url)
        try:
            async with (
                self._semaphore,  # AC-11
                self._http.stream("GET", request_url, headers=headers) as response,
            ):
                if response.status_code == 304:  # AC-6
                    self._cache.touch_fetch(url, checked_at=now)
                    sha256 = record.sha256 if record else None
                    return FetchResult(
                        url,
                        FetchOutcome.NOT_MODIFIED,
                        sha256=sha256,
                        data=self._cache.read_blob(sha256) if sha256 else None,
                    )
                if response.status_code >= 400:
                    raise httpx.HTTPStatusError(
                        f"HTTP {response.status_code}",
                        request=response.request,
                        response=response,
                    )

                body = bytearray()
                async for piece in response.aiter_bytes(_CHUNK):
                    body.extend(piece)
                    # AC-10: stop reading rather than buffering a 70 MB file.
                    if len(body) > self._max_bytes:
                        raise _TooLarge(f"file too large (> {self._max_bytes} bytes), abandoned")
                data = bytes(body)
                etag = response.headers.get("ETag")
                last_modified = response.headers.get("Last-Modified")
                filename = _filename_from(response.headers.get("Content-Disposition"))
        except _TooLarge as exc:
            return self._fail(url, str(exc), now)
        except (httpx.HTTPError, httpx.StreamError) as exc:
            return self._fail(url, f"{type(exc).__name__}: {exc}", now)

        digest = hashlib.sha256(data).hexdigest()
        previous = record.sha256 if record else None
        # put_blob writes via a temp file and renames, so an interrupted run can
        # never leave a truncated blob behind (AC-12).
        self._cache.put_blob(data)
        self._cache.record_fetch(
            url,
            sha256=digest,
            etag=etag,
            last_modified=last_modified,
            moodle_timemodified=moodle_timemodified,
            fetched_at=now,
            checked_at=now,
        )

        if previous is None:
            outcome = FetchOutcome.DOWNLOADED
        elif previous == digest:
            outcome = FetchOutcome.UNCHANGED
        else:
            outcome = FetchOutcome.CHANGED
        return FetchResult(url, outcome, sha256=digest, filename=filename, data=data)

    def _fail(self, url: str, error: str, now: int) -> FetchResult:
        self._cache.record_fetch_failure(url, error=error, checked_at=now)
        log.warning("fetch.failed", url=_redact(url), error=error)
        return FetchResult(url, FetchOutcome.FAILED, error=error)

    def _authenticated(self, url: str) -> str:
        """Append the Moodle token, and only ever to Moodle (AC-1)."""
        if not self._token or "/pluginfile.php" not in url:
            return url
        host = urlsplit(url).netloc
        if self._moodle_host and host != self._moodle_host:
            return url
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}token={self._token}"


class _TooLarge(Exception):
    pass


def _filename_from(disposition: str | None) -> str | None:
    """Pull the real filename out of Content-Disposition (AC-16)."""
    if not disposition:
        return None
    for part in disposition.split(";"):
        part = part.strip()
        for prefix in ("filename*=UTF-8''", "filename="):
            if part.lower().startswith(prefix.lower()):
                value = part[len(prefix) :].strip().strip('"')
                if value:
                    from urllib.parse import unquote

                    return unquote(value)
    return None


def _redact(url: str) -> str:
    """Never let a token reach the logs."""
    if "token=" not in url:
        return url
    head, _, _ = url.partition("token=")
    return f"{head}token=<redacted>"
