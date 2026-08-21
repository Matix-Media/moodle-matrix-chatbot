"""OAuth 2.0 device grant for next-gen Matrix auth (MAS/OIDC).

Why the bot needs this at all:

* matrix.org accounts created through account.matrix.org have **no legacy password**;
  ``m.login.password`` is advertised but returns ``M_FORBIDDEN``, and e-mail
  identifiers are rejected outright.
* An access token copied from Element is bound to *Element's* device, whose Olm
  private keys never leave that browser. The bot can hold the token but cannot
  decrypt anything encrypted to that device — observed as
  ``Olm event doesn't contain ciphertext for our key``.

The device grant solves both: the bot registers itself as an OAuth client, picks its
**own** device id (MSC2967 lets the client name the device in the requested scope),
and the user approves it once in a browser. The resulting device has no keys yet, so
nio uploads its own and encryption works.
"""

from __future__ import annotations

import contextlib
import secrets
import string
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

log = structlog.get_logger(__name__)

DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
_DEVICE_ID_ALPHABET = string.ascii_uppercase + string.digits


class DeviceGrantError(RuntimeError):
    """The device authorization flow could not be completed."""


@dataclass
class TokenSet:
    """An OAuth token pair.

    MAS access tokens are short-lived (minutes), so the refresh token is the part
    that makes an unattended deployment possible. MAS also *rotates* refresh tokens,
    so the new one must be persisted on every refresh.
    """

    access_token: str
    refresh_token: str | None = None
    expires_in: int = 0


@dataclass
class DeviceAuthorization:
    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int
    interval: int


def generate_device_id(length: int = 10) -> str:
    """A fresh Matrix device id. We choose it, so the device is ours alone."""
    return "".join(secrets.choice(_DEVICE_ID_ALPHABET) for _ in range(length))


def matrix_scope(device_id: str) -> str:
    """MSC2967 scope: full client API plus this specific device."""
    return (
        "urn:matrix:org.matrix.msc2967.client:api:* "
        f"urn:matrix:org.matrix.msc2967.client:device:{device_id}"
    )


def build_client_metadata(client_name: str, client_uri: str) -> dict[str, Any]:
    """Metadata for dynamic client registration.

    A public native client using only the device grant: there is no redirect URI and
    no client secret, which is exactly the shape MAS expects for CLI-style logins.
    """
    return {
        "client_name": client_name,
        "client_uri": client_uri,
        "application_type": "native",
        "token_endpoint_auth_method": "none",
        "grant_types": [DEVICE_GRANT, "refresh_token"],
        "response_types": [],
    }


def _error_text(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}: {response.text[:200]}"
    parts = [str(body.get("error", response.status_code))]
    if body.get("error_description"):
        parts.append(str(body["error_description"]))
    return ": ".join(parts)


async def register_client(
    http: httpx.AsyncClient, registration_endpoint: str, client_name: str, client_uri: str
) -> str:
    """Dynamically register this bot as an OAuth client and return its client_id."""
    response = await http.post(
        registration_endpoint,
        json=build_client_metadata(client_name, client_uri),
        headers={"Content-Type": "application/json"},
    )
    if response.status_code not in (200, 201):
        raise DeviceGrantError(f"client registration failed - {_error_text(response)}")
    client_id = response.json().get("client_id")
    if not client_id:
        raise DeviceGrantError("client registration returned no client_id")
    return str(client_id)


async def start_device_authorization(
    http: httpx.AsyncClient, device_endpoint: str, client_id: str, scope: str
) -> DeviceAuthorization:
    """Begin the flow and return the code the user must approve."""
    response = await http.post(device_endpoint, data={"client_id": client_id, "scope": scope})
    if response.status_code != 200:
        raise DeviceGrantError(f"device authorization failed - {_error_text(response)}")
    body = response.json()
    verification_uri = body.get("verification_uri", "")
    return DeviceAuthorization(
        device_code=body["device_code"],
        user_code=body.get("user_code", ""),
        verification_uri=verification_uri,
        verification_uri_complete=body.get("verification_uri_complete") or verification_uri,
        expires_in=int(body.get("expires_in", 900)),
        interval=int(body.get("interval", 5)),
    )


async def poll_for_token(
    http: httpx.AsyncClient,
    token_endpoint: str,
    client_id: str,
    device_code: str,
    *,
    interval: int,
    expires_in: int,
    sleep: Callable[[float], Any] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> TokenSet:
    """Poll until the user approves, and return the tokens.

    ``authorization_pending`` is the normal state while the user is still clicking;
    ``slow_down`` is the server asking us to back off and must actually be honoured.
    """
    deadline = clock() + expires_in
    delay = float(interval)

    while True:
        response = await http.post(
            token_endpoint,
            data={
                "grant_type": DEVICE_GRANT,
                "device_code": device_code,
                "client_id": client_id,
            },
        )
        if response.status_code == 200:
            return _token_set(response.json())

        error = ""
        with contextlib.suppress(ValueError):
            error = str(response.json().get("error", ""))

        if error == "authorization_pending":
            pass
        elif error == "slow_down":
            delay += 5
        elif error == "access_denied":
            raise DeviceGrantError("Login was denied in the browser.")
        elif error == "expired_token":
            raise DeviceGrantError("The login request expired before it was approved.")
        else:
            raise DeviceGrantError(f"device grant failed - {_error_text(response)}")

        if clock() >= deadline:
            raise DeviceGrantError("The login request expired before it was approved.")
        await_result = sleep(delay)
        if hasattr(await_result, "__await__"):  # pragma: no cover - async sleep support
            await await_result


async def discover_endpoints(http: httpx.AsyncClient, homeserver: str) -> dict[str, str]:
    """Find the account/auth service for a homeserver and its OAuth endpoints."""
    well_known = await http.get(f"{homeserver.rstrip('/')}/.well-known/matrix/client")
    if well_known.status_code != 200:
        raise DeviceGrantError(f"{homeserver} did not serve /.well-known/matrix/client")
    auth = well_known.json().get("org.matrix.msc2965.authentication")
    if not auth or not auth.get("issuer"):
        raise DeviceGrantError(
            f"{homeserver} does not advertise next-gen auth, so the device grant is "
            "not available. Use a password instead."
        )
    issuer = str(auth["issuer"]).rstrip("/")
    discovery = await http.get(f"{issuer}/.well-known/openid-configuration")
    if discovery.status_code != 200:
        raise DeviceGrantError(f"could not read OpenID configuration from {issuer}")
    config = discovery.json()
    if DEVICE_GRANT not in config.get("grant_types_supported", []):
        raise DeviceGrantError(f"{issuer} does not support the OAuth device grant")
    return {
        "issuer": issuer,
        "registration_endpoint": config["registration_endpoint"],
        "device_authorization_endpoint": config["device_authorization_endpoint"],
        "token_endpoint": config["token_endpoint"],
    }


def _token_set(body: dict[str, Any]) -> TokenSet:
    access = body.get("access_token")
    if not access:
        raise DeviceGrantError("token response contained no access_token")
    return TokenSet(
        access_token=str(access),
        refresh_token=str(body["refresh_token"]) if body.get("refresh_token") else None,
        expires_in=int(body.get("expires_in", 0) or 0),
    )


async def refresh_access_token(
    http: httpx.AsyncClient, token_endpoint: str, client_id: str, refresh_token: str
) -> TokenSet:
    """Exchange a refresh token for a fresh access token.

    This is what keeps the bot running across restarts and past token expiry without
    a human reopening the browser flow.
    """
    response = await http.post(
        token_endpoint,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        },
    )
    if response.status_code != 200:
        raise DeviceGrantError(
            f"could not refresh the Matrix access token ({_error_text(response)}). "
            "The refresh token has probably been revoked or expired - "
            "run 'bsbot matrix-login' once to re-authorise."
        )
    return _token_set(response.json())


#: Renew at this fraction of the token's lifetime. Early enough to absorb a failed
#: attempt and a retry, late enough not to hammer the token endpoint.
REFRESH_FRACTION = 0.75
#: Used when the server does not report an expiry.
DEFAULT_REFRESH_PERIOD_S = 1800


def seconds_until_refresh(expires_in: int) -> float:
    """How long to wait before renewing an access token."""
    if expires_in <= 0:
        return float(DEFAULT_REFRESH_PERIOD_S)
    return max(30.0, expires_in * REFRESH_FRACTION)
