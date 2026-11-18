"""Small in-process guards: a per-key burst limiter and CORS for registered sites."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Hashable
from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

_DEFAULT_PORTS = {"http": 80, "https": 443}


def site_origin(url: str) -> str:
    """The browser ``Origin`` of a URL: scheme, host and non-default port, no path.

    A site registered as ``https://campus.example/moodle`` (Moodle in a sub-directory)
    is seen by the browser as ``https://campus.example`` — the header never carries a path.
    Anything that is not an absolute URL (e.g. the literal ``null`` origin) is returned
    trimmed and lower-cased, so it never equals a real origin.
    """
    parsed = urlparse(url.strip())
    host = parsed.hostname
    if not parsed.scheme or not host:
        return url.strip().rstrip("/").lower()
    scheme = parsed.scheme.lower()
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    try:
        port = parsed.port
    except ValueError:
        port = None
    suffix = f":{port}" if port is not None and port != _DEFAULT_PORTS.get(scheme) else ""
    return f"{scheme}://{host}{suffix}"


class BurstLimiter:
    """At most ``per_minute`` events per key in any sliding 60 s window."""

    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.per_minute = per_minute
        self._clock = clock
        self._events: dict[Hashable, deque[float]] = {}

    def allow(self, key: Hashable) -> bool:
        now = self._clock()
        window = self._events.setdefault(key, deque())
        while window and now - window[0] >= 60.0:
            window.popleft()
        if len(window) >= self.per_minute:
            return False
        window.append(now)
        return True


class SiteCORS(BaseHTTPMiddleware):
    """CORS for ``/api/*``: only the origins of registered, unrevoked Moodle sites.

    ``allowed_origins`` returns origins as :func:`site_origin` spells them.
    """

    def __init__(self, app: ASGIApp, allowed_origins: Callable[[], set[str]]) -> None:
        super().__init__(app)
        self._allowed = allowed_origins

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        origin = request.headers.get("origin", "").rstrip("/")
        is_api = request.url.path.startswith("/api/")
        allowed = bool(origin) and is_api and site_origin(origin) in self._allowed()
        if (
            request.method == "OPTIONS"
            and is_api
            and request.headers.get("access-control-request-method")
        ):
            response = Response(status_code=204 if allowed else 403)
        else:
            response = await call_next(request)
        if allowed:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
            response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
            response.headers["Access-Control-Allow-Methods"] = "GET, PATCH, DELETE, OPTIONS"
            response.headers["Access-Control-Max-Age"] = "600"
        return response
