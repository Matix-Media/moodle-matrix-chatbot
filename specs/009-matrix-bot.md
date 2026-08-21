# 009 — Matrix bot

- **Status:** active
- **Tests:** `tests/unit/test_matrix_bot.py`

## Goal

Sit in the class's encrypted Matrix room, notice questions aimed at the bot, and reply in a
thread with a cited answer. The room is shared by real classmates, so the bot must be quiet,
predictable, and incapable of replaying history or spamming.

## Acceptance criteria

### Scope and triggering
- `AC-1` The bot acts only in configured room IDs; messages elsewhere are ignored entirely.
- `AC-2` The bot never responds to its own messages (an obvious but fatal loop).
- `AC-3` Messages older than process start are ignored, so a restart does not replay and
  re-answer the room's backlog.
- `AC-4` It responds when addressed: `!bs <question>`, a mention of its display name or user
  ID, a reply to one of its own messages, or a **structured mention** (MSC3952
  `m.mentions.user_ids`) — the form real clients produce when a user picks the bot from
  the `@`-autocomplete, which can land anywhere in the message, not just at the start.
- `AC-5` In `answer_all` mode it also responds to plain questions, but still ignores its own
  messages and non-questions.
- `AC-6` The trigger prefix is stripped before the question reaches the pipeline.

### Replying
- `AC-7` Replies are threaded (`m.thread`) off the triggering message, keeping the main
  timeline readable.
- `AC-8` Answers render as Markdown with citation links back to Moodle, in `formatted_body`
  with an HTML-free `body` fallback.
- `AC-9` A typing indicator is shown while the pipeline runs, and cleared afterwards even if
  the pipeline raises.
- `AC-10` A pipeline exception produces an apologetic message rather than silence or a crash.

### Authentication
- `AC-15` Either a password **or** a pre-issued access token authenticates the bot; a token
  is preferred because matrix.org increasingly issues accounts through next-gen auth
  (MAS/OIDC), where `m.login.password` is rejected even though the flow is advertised.
- `AC-16` With a token, no login request is made at all.
- `AC-17` A token used for an encrypted room needs its matching `device_id`. If one is not
  configured, it is **discovered** from `/account/whoami` at startup rather than demanded
  from the user, who would otherwise have to hunt for a "Session ID" in client settings.
  Only if discovery fails is startup refused, with an explanatory message.
- `AC-18` After a password login the device id and token are persisted and reused, so
  restarts do not create a new device every time (which would strand message keys).

### Encryption
- `AC-11` The client is configured with a persistent store so device keys survive restarts —
  without this the bot cannot decrypt after its first run.
- `AC-12` Undecryptable messages are logged and skipped, not crashed on.
- `AC-19` Messages the bot's own device sends are **not** cross-signed as verified by the
  account (that needs interactive verification or SSSS recovery, both impractical for an
  unattended process), so clients show a "not verified by its owner" indicator on the
  bot's replies. This is expected, not a bug: the content is still end-to-end encrypted
  and readable. A human can clear the indicator by verifying the bot's device once from
  another session logged into the same account, but the bot must work correctly without
  that ever happening.

### Resilience
- `AC-20` A transient network interruption (a laptop sleeping, a Wi-Fi drop, a homeserver
  hiccup) must **not** crash the process. `sync_forever`'s retry budget for timeouts and
  rate-limit responses is unlimited — a bot that dies on the first dropped connection is
  useless as a long-running service.
- `AC-25` `sync_forever` is given a non-zero `loop_sleep_time`. Found live: nio's own sync
  loop only paces itself via the server's long-poll wait on a *successful* response — a
  response that fails nio's own schema validation (observed: a `/sync` body missing
  `next_batch`) returns immediately, and with no `loop_sleep_time` set the loop retries with
  **zero delay**, producing hundreds of requests per second against the homeserver until the
  process is killed by hand. `AC-20`'s unlimited retry budget governs a completely different
  code path (transport-level timeouts/429s) and does not protect against this one.
- `AC-26` A `SyncError` whose Matrix error code indicates the access token is no longer
  recognised (`M_UNKNOWN_TOKEN`, `M_MISSING_TOKEN`) proactively triggers a token refresh
  rather than retrying the same rejected token indefinitely. Every sync error is logged
  through bsbot's own structured logging, not left as only nio's generic warning line.
- `AC-21` The Matrix library's own internal logging (`nio.*`) is quiet by default; only
  bsbot's own structured log lines appear at the configured level, so real signal is not
  buried under per-event trace lines like "Room X handling event of type Y".

### Politeness
- `AC-13` Per-user rate limiting: a user exceeding the limit gets one notice, not a reply per
  message.
- `AC-14` Only `m.text` messages are considered; images, files and reactions are ignored.

## Non-goals

- No conversational memory across messages; each question stands alone.
- No moderation or admin commands.
- No automated cross-signing / SSSS self-verification of the bot's own device (see AC-19).
