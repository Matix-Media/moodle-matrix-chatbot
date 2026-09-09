# bsbot — Berufsschule Matrix Bot

A Matrix bot that answers classmates' questions about the Berufsschule, grounded in the
school's Moodle content — course pages, labels, forum text, PDFs, slides and Nextcloud
shares — and always citing its sources.

Built against **moodle.itech-bs14.de** (Moodle 5.0.3+): 25 courses (19 direct enrolments +
6 discovered via same-Moodle-host links and self-enrolled), 5,730 indexed chunks — including
611 pages of scanned IHK exam papers recovered via OCR.

## How it works

```
Moodle REST API ──► crawl (structure only, no downloads)
                      │
                      ├─ inline text (labels, intros) ──────────┐
                      └─ files & pages ─► fetch (cached) ─► extract ─► chunk ─┤
                                                                              ▼
                                              SQLite: FTS5 (BM25) + sqlite-vec (cosine)
                                                                              │
                             question ─► expand ─► ┌─ keyword search ─┐       │
                                                   ├─ vector search ──┤─► RRF ─► rerank
                                                   └──────────────────┘            │
                                                                                   ▼
                                                        Gemini ─► cited German answer
```

### Why hybrid retrieval

Pure vector search is the obvious choice and the wrong one here. The two retrievers fail
in *complementary* ways, which is exactly when fusing them wins:

- **Lexical needles** — `LF05`, `15.03.2026`, a teacher's surname. BM25 nails them;
  embeddings blur them.
- **Casual phrasing against formal documents** — "wann is die prüfung" vs *Termin der
  Abschlussprüfung Teil 1*. Embeddings bridge it; BM25 cannot.

Results are fused with Reciprocal Rank Fusion, so the two scoring scales never have to be
made comparable. German compounds (`Prüfung` inside `Abschlussprüfung`) are deliberately the
vector retriever's job — FTS5 tokenises whole words. That boundary is pinned by a test.

### Course discovery

`bsbot sync` also follows same-Moodle-host course links found inside enrolled courses
(`core_enrol_get_users_courses` only returns courses the account is *already* enrolled in, so
a linked course is otherwise invisible). If the linked course offers key-free self-enrolment,
the bot joins it automatically and crawls it like any other course — verified live: 5 of 7
linked courses found on the real site required no key and were joined this way. A course
requiring an enrolment key, or using manual/guest enrolment, is never touched; the bot never
guesses a password. Every attempt is logged (`enrol.success` / `enrol.requires_password` /
`enrol.declined`), never silent. Disable with `bsbot sync --no-follow-links`.

### Freshness

Files are downloaded **on demand** and cached content-addressed, with a ladder that tries the
cheapest check first:

1. No cache entry → download.
2. Moodle's `timemodified` changed → download. *Free: already known from the structure crawl.*
3. Inside the soft TTL (24 h) → serve cache, **no network at all**.
4. TTL elapsed → conditional `GET`; a `304` costs a few hundred bytes.
5. Bytes differ → new blob, re-extract, re-embed — *only that document*.

Content removed from Moodle is **tombstoned**, so it stops being answerable immediately.

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

Copy `.env.example` to `.env` and fill it in, then:

```bash
bsbot doctor          # what is configured
bsbot whoami          # verify Moodle access
bsbot sync            # crawl course structure (cheap, run often)
bsbot index           # fetch + extract + chunk pending documents
bsbot embed           # embed new chunks
bsbot ask "Was muss ich bei Fehlzeiten tun?"
bsbot serve           # run the Matrix bot
```

### Scanned documents

Roughly 70 PDFs in this corpus are scans of past IHK exam papers with no extractable text.
They are transcribed with Gemini, cached per page image so each is paid for once:

```bash
bsbot index --ocr --retry-empty
```

## Matrix access

matrix.org accounts created through **account.matrix.org** use next-gen auth (MAS/OIDC) and
have **no legacy password**: `m.login.password` is advertised but returns `M_FORBIDDEN`, and
e-mail identifiers are rejected outright (`M_UNKNOWN`). Use the OAuth device grant:

```bash
bsbot matrix-login     # prints a URL + code; approve once in a browser
bsbot serve
```

**Do not copy an access token out of Element.** It authenticates fine, but it is bound to
*Element's* device, whose Olm private keys never leave that browser — so the bot can read
nothing, failing with `Olm event doesn't contain ciphertext for our key`. `matrix-login`
instead names its **own** device in the requested scope (MSC2967), so nio uploads keys it
actually controls.

The command stores an access token *and a refresh token*. The bot refreshes on startup and
again while running (matrix.org issues 4-hour tokens), so restarts and redeploys never need
the browser flow again. Only a revoked refresh token requires re-running it.

Password login still works for legacy and self-hosted homeservers.

The bot must be **invited** to its rooms — it only joins room IDs on its allow-list.

## Moodle access

The site has the `moodle_mobile_app` web service enabled, so a normal student account gets a
real REST token from `/login/token.php` — no admin action and no HTML scraping.

Two things to know:

- Moodle authenticates with the **Anmeldename**, not the e-mail address.
- A failed login returns **HTTP 200** with an error body. Anything that only checks the
  status code will treat a bad password as success and fail confusingly later.

Prefer putting a token in `BSBOT_MOODLE__TOKEN` (from *Einstellungen → Sicherheitsschlüssel*)
over storing a password. `bsbot whoami` caches one automatically after the first login.

## Configuration

All settings come from environment variables prefixed `BSBOT_`, with `__` separating nested
sections (`BSBOT_MOODLE__BASE_URL`). See `.env.example`. Secrets are `SecretStr`, so they
never appear in logs or tracebacks. `.env` is gitignored.

## Development

Spec-driven: every feature has a numbered spec in [`specs/`](specs/) with numbered acceptance
criteria, and each spec names the test module that verifies it. Tests cite the criteria they
cover (`AC-7`) in their docstrings. Specs are written before tests, tests before code.

```bash
pytest              # 332 tests, no network required
ruff check . && ruff format --check .
mypy
```

Moodle responses are replayed from recorded fixtures in `tests/fixtures/moodle/`, so the
whole pipeline is testable without touching the school's server.

## Deployment

`api` is the only service that ever opens the SQLite index — `matrix`, `cron`, and `web` are all
HTTP clients of it (specs/015-microservice-split.md). Start it first:

```bash
docker compose up -d api            # internal HTTP API: storage, PII, chunking, embedding, answering
docker compose up -d matrix cron    # the Matrix bot, and the recurring crawl/fetch loop
docker compose run --rm sync        # one-shot: crawl + fetch/extract, api chunks+embeds+stores
```

`matrix` and `cron` are separate containers on purpose — a multi-hour crawl or a large embedding
batch must never delay the bot answering a question in the room. Neither touches the index
directly anymore, so there's no shared-volume concern between them; `cron` repeats
crawl → fetch/extract → (api chunks, embeds, stores) every `BSBOT_SYNC_INTERVAL_MINUTES` (default
180). All containers use `restart: unless-stopped`, and `matrix` additionally restarts its own
Matrix connection internally with backoff after a transient network failure (a laptop's lid
closing, a VPS network blip) — see `matrix.crashed_restarting` in the logs. `matrix`'s own volume
holds only its E2EE session store now, not the RAG index.

For the web chat (spec 014, people outside the Matrix room):

```bash
docker compose up -d web   # the Nuxt chat frontend — needs `api` already running
```

Neither `api` nor `web` publishes a host port — `web` needs a reverse proxy / domain rule pointed
at its internal port 3000 to actually be reachable, see `specs/014-web-chat.md`.

Config reaches the container either way a platform provides it: a real `.env` file (self-hosted
VPS), or variables injected straight into the environment (Dokploy sets `BSBOT_*` directly rather
than writing a `.env` file — `docker-compose.yml` handles both, see the comment at its top). On
Dokploy, point it at this repo, set the values from `.env.example` as environment variables in
its UI, and deploy; no other setup is needed.

`matrix-nio` 0.26 replaced libolm with vodozemac, so end-to-end encryption needs no C library
and the image is a plain `python:3.13-slim`.

## Privacy

Course material and forum text are sent to Google for embedding and answering. In a school
context that may be worth flagging to whoever administers the Moodle. Assignment submissions,
grades and gradebook data are **never** crawled, and the bot only ever indexes what its own
account can already see.
