"""Shared bearer-token auth for every endpoint on `api` — public (`/api/ask`,
used by `web`/`matrix`) and internal (`/internal/*`, used by `cron`/`matrix`)
alike. One secret, `BSBOT_WEB__API_TOKEN` — see specs/014-web-chat.md and
specs/019-microservice-split.md for why it's shared rather than per-caller.
"""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, Request


def verify_token(request: Request, authorization: str | None = Header(default=None)) -> None:
    token = ""
    if authorization and authorization.startswith("Bearer "):
        token = authorization[len("Bearer ") :]
    if not hmac.compare_digest(token, request.app.state.api_token):
        raise HTTPException(status_code=401, detail="invalid or missing token")
