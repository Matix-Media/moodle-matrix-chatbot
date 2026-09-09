# 016 — Matrix OAuth device-grant login and process resilience

- **Status:** active
- **Tests:** `tests/unit/test_matrix_oauth.py`, `tests/unit/test_matrix_runner.py`,
  `tests/unit/test_write_env.py`

## Goal

Spec 009 covers how the running bot *uses* a Matrix token and reacts to it being rejected. It
does not cover how the bot **obtains** that token in the first place, or the outer layer that
keeps an unattended, long-running process alive across restarts. Both exist because of the same
root cause: matrix.org accounts created via account.matrix.org have no legacy password
(`m.login.password` is advertised but rejected), and a token copied from Element is bound to
Element's own device, whose Olm keys never leave that browser — the bot can hold the token but
decrypts nothing. `bsbot matrix-login` runs an OAuth 2.0 device-code grant (MSC2967) so the bot
registers itself as an OAuth client, picks its **own** device id, and a human approves it once
in a browser; the resulting device has no keys yet, so nio uploads its own and encryption works
from the first message. Everything below either makes that login possible or keeps the bot
running unattended afterwards.

## Acceptance criteria

### OAuth device-grant login (`bsbot matrix-login`)
- `AC-1` The bot generates its own 10-character alphanumeric device id rather than letting the
  homeserver assign one, and requests that specific device by id in its OAuth scope (MSC2967) —
  so the resulting device, and its encryption keys, are unambiguously the bot's own.
- `AC-2` Auth endpoints are discovered dynamically: `/.well-known/matrix/client`'s
  `org.matrix.msc2965.authentication` issuer, then that issuer's
  `/.well-known/openid-configuration`. A homeserver that advertises no such issuer, or whose
  issuer doesn't support the device grant, fails with a message saying so — "use a password
  instead" — rather than a generic HTTP error.
- `AC-3` The bot dynamically registers itself as a public OAuth client with no redirect URI and
  no client secret (`token_endpoint_auth_method: none`) — the shape MAS expects for a
  CLI-style native client, not a web app.
- `AC-4` Device authorization returns a user code and a verification URL, which `matrix-login`
  prints for a human to open and approve — decoupling that one manual step from the token
  exchange itself, which continues to poll unattended.
- `AC-5` Polling honours `authorization_pending` (keep waiting), `slow_down` (increase the poll
  interval rather than ignore the request), `access_denied` and `expired_token` (stop with a
  clear error); any other error is a hard failure, so a real problem never spins forever
  disguised as "still pending."
- `AC-6` A registration or device-authorization failure surfaces the server's own
  `error`/`error_description` text, not just an HTTP status code — MAS's rejection reasons (e.g.
  `client_uri must be https`) are specific enough to act on.
- `AC-7` On success, the access token, refresh token, device id, OAuth client id and token
  endpoint are written to the settings' token-overrides file (AC-10) so a subsequent `bsbot
  serve` picks them up with no further action.

### Token persistence (`_write_env`)
- `AC-8` Writing tokens updates only the given keys, in place, in the target file — every other
  line, and any existing value for a key not being updated, is preserved.
- `AC-9` The target file's parent directories are created if missing.
- `AC-10` The write target is always an explicit path (the settings' token-overrides file); there
  is no fallback to a bare `.env`. Inside a container that path is neither writable (a non-root
  user, nothing baked into the image) nor persistent (only the data volume survives a restart) —
  every caller passes the explicit path so this can't regress back to the broken default.

### Continuous token renewal
- `AC-11` While connected, the bot renews its access token in the background at a fixed fraction
  (75%) of the token's reported lifetime — not only once at startup — because matrix.org issues
  short-lived tokens (observed: 4 hours) and startup-only renewal would cap the bot's uptime at
  that lifetime.
- `AC-12` A renewal failure retries sooner (5 minutes) rather than waiting a full cycle or giving
  up on renewal entirely.
- `AC-13` MAS rotates the refresh token on every use. The value sent by the *next* renewal is the
  one the *previous* renewal within this process returned, never the value the process started
  with — reusing the original value deterministically fails the second renewal (observed live:
  every deployment's first scheduled 3-hourly renewal, on every run).

### Outer process resilience
- `AC-14` A supervising restart loop wraps the entire connect-and-sync lifecycle. Any exception
  surfacing from it — including one nio itself does not catch, such as an `httpx.ReadError` from
  a connection torn down by a sleeping laptop or a Wi-Fi drop — restarts the bot instead of
  killing the process.
- `AC-15` Restart backoff starts small and doubles on each immediate failure, capped at a
  maximum, so a persistent problem (bad config, a homeserver that is actually down) is not
  hammered with instant retries.
- `AC-16` A run that stays healthy past a threshold uptime resets backoff to its initial value on
  its *next* failure, so an incident hours later does not inherit an unrelated earlier incident's
  already-elevated backoff.
- `AC-17` `asyncio.CancelledError` (a deliberate shutdown) propagates immediately — it is not
  logged as a crash and is never retried.
- `AC-18` The Matrix configuration is re-read fresh on every restart attempt, not only once at
  process start. Otherwise a token rotated on disk by something else (a manual `matrix-login`,
  another process) while this process is stuck retrying a stale, already-rejected refresh token
  is never noticed — it would retry the same dead value forever instead of picking up the valid
  one sitting next to it on disk (observed live).

## Non-goals

- No interactive/redirect-based OAuth flow (authorization code + redirect URI) — there is no
  browser embedded in a headless bot; the device grant is the only flow implemented.
- No token revocation or logout command.
- Reactive refresh triggered by the homeserver rejecting a token mid-sync (`M_UNKNOWN_TOKEN`),
  and the `sync_forever` pacing fix that prevents a hot retry loop on a malformed `/sync`
  response, are spec 009 AC-25/AC-26 — this spec's outer restart loop (AC-14..18) is a separate,
  lower layer that catches what AC-26's in-loop handling cannot (an exception nio never turns
  into a `SyncError` at all).

## Notes

`bsbot matrix-login` is the CLI entry point for the flow above
(`src/bsbot/cli/matrix.py:matrix_login`); `src/bsbot/matrix/oauth.py` implements the OAuth protocol
pieces, `src/bsbot/matrix/runner.py` implements the renewal loop and the outer restart
supervisor (`run_bot`/`_run_with_restart`/`MatrixRunner._renew_forever`). See spec 009 for how
the resulting token is used once the bot is running, and spec 001 for where the token-overrides
file lives in `Settings`.
