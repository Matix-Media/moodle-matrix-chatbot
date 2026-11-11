"""Plugin → API authentication: one per-site bearer secret over HTTPS.

Deliberately simple and conventional (the same model as webhook secrets): TLS gives
confidentiality, integrity and replay protection; a high-entropy secret, looked up
by keyed hash and compared in constant time, identifies the site.

Every dependency here is ``async def``: FastAPI runs plain ``def`` dependencies in a
threadpool, and the store's SQLite connection may only be used from the event loop
thread that opened it.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from moodle_llm_chat.index.models import Site
from moodle_llm_chat.index.store import Store

UNAUTHORIZED = HTTPException(status.HTTP_401_UNAUTHORIZED, detail="unauthorized")


async def get_store(request: Request) -> Store:
    store: Store = request.app.state.store
    return store


async def require_tls(request: Request) -> None:
    """Refuse plain HTTP unless explicitly disabled (local development only).

    Behind a TLS-terminating proxy, run uvicorn with ``--proxy-headers`` so the
    forwarded scheme is seen here.
    """
    if request.app.state.settings.security.require_tls and request.url.scheme != "https":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="TLS required")


def bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


async def site_auth(
    request: Request,
    store: Annotated[Store, Depends(get_store)],
    _tls: Annotated[None, Depends(require_tls)],
) -> Site:
    token = bearer_token(request)
    if token is None:
        raise UNAUTHORIZED
    site = store.authenticate_site(token)
    if site is None:
        raise UNAUTHORIZED
    store.touch_site(site.site_id)
    return site


SiteDep = Annotated[Site, Depends(site_auth)]
StoreDep = Annotated[Store, Depends(get_store)]
