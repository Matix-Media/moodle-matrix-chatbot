# 014 — Web chat API

- **Status:** active
- **Tests:** `tests/unit/test_web_api.py`

## Goal

Let people outside the Matrix room ask the bot questions from a browser. This spec covers only the
internal HTTP API bsbot exposes for that (a thin wrapper around the existing `AnswerPipeline`) — the
Nuxt chat frontend that calls it is a separate, non-Python component and has no bsbot spec.

## Acceptance criteria

### Authentication
- `AC-1` `POST /api/ask` requires `Authorization: Bearer <token>`; a missing header is rejected with
  `401`.
- `AC-2` A token that does not match `BSBOT_WEB__API_TOKEN` is rejected with `401`, compared with a
  constant-time comparison (`hmac.compare_digest`) so response timing cannot be used to guess the
  token byte-by-byte.
- `AC-3` `GET /healthz` requires no authentication, so container/orchestrator health checks do not
  need the secret.

### Answering
- `AC-4` A valid, authenticated request with `{"question": "..."}` returns `200` with the same
  answer shape the CLI's `ask` command prints: `text`, `citations`, `grounded`, `suggested_questions`.
- `AC-5` An optional `history` field — a list of `[question, answer]` pairs — is passed straight
  through to `pipeline.answer(question, history=...)` unmodified. The API holds no session state of
  its own: every follow-up question's context comes entirely from what the caller resends.
- `AC-6` PII tokenization applies exactly as it does on the Matrix path: when
  `BSBOT_PII__ENABLED=true`, `build_pii_tokenizer` produces the same tokenizer passed into both the
  searcher and the pipeline.

### Rate limiting
- `AC-7` Requests are limited by a single shared bucket (burst-per-minute and daily-quota, mirroring
  `BotPolicy`'s shape in `specs/009-matrix-bot.md`) — appropriate because every caller presents the
  same shared token, so there is no per-visitor identity to key on. Exceeding either limit returns
  `429`.

## Non-goals

- No per-visitor identity or accounts — one shared token, one rate-limit bucket.
- No streaming responses.
- No server-side conversation persistence (see AC-5) — a restart of this service loses nothing
  because it never held anything.
- The Nuxt frontend's own token-gating and forwarding behaviour is out of scope for this spec; it
  lives entirely in `web/` and has no Python code to test here.
