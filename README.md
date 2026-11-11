# moodle-llm-chat

An agentic course assistant that lives inside Moodle. Students and teachers ask
questions in a chat on the Moodle site; an agent searches, navigates and reads the
course material **that this user is allowed to see**, asks back when a question is
ambiguous, and answers with links to the exact activity, file and page.

```
┌──────────── Moodle ────────────┐   HTTPS (site secret)   ┌──────── moodle-llm-chat API ────────┐
│ local_llmchat plugin           │ ──────────────────────▶ │ /ingest/*  content, files, policy   │
│  • pushes courses, activities, │                         │ job pipeline: fetch → extract →     │
│    files (events + full sync)  │ ◀────────────────────── │   OCR → embed (encrypted store)     │
│  • answers live access checks  │   callback.php          │                                     │
│  • signs short-lived chat      │                         │ /ws/chat   LangGraph agent + tools  │
│    tokens (who + what visible) │   browser websocket     │ /api/conversations  history         │
│  • renders the chat UI         │ ──────────────────────▶ │ quotas, usage metering              │
└────────────────────────────────┘                         └─────────────────────────────────────┘
```

* `moodle/local_llmchat/` — the Moodle plugin (Moodle 4.5+ / 5.x, PHP 8.2+).
* `src/moodle_llm_chat/` — the API service (Python 3.13+).

## What the agent can do

| Tool | Purpose |
|---|---|
| `search_content` | Semantic + keyword search (hybrid, reciprocal-rank fused) |
| `search_text` | Exact keyword / phrase search (FTS5, umlaut-folding) |
| `search_regex` | Regular expressions over full document text (guarded against ReDoS) |
| `list_dir`, `stat_file`, `read_file` | Courses as folders, documents as files; partial and per-page reads |
| `list_documents` | Newest / oldest documents, with `since` / `until` filters |
| `current_datetime`, `current_user` | Today's date and week; who is asking, roles, groups, course context |
| `ask_user` | Pause and ask a clarifying question (options or free text) |

Every search tool takes a path and time filters. New tools are one class
(`agent/tools/base.py`); new kinds of question to the user are one interaction kind
(`agent/interactions.py`) and need no UI change for a basic rendering.

## Security model

* **Access.** The plugin computes, *as the user*, which courses and modules they can
  see and signs that into a 15-minute HS256 chat token. Every store query is filtered
  by it, and before any document text reaches the model the API asks Moodle again
  live (`callback.php`). If Moodle cannot be reached, nothing is shown.
* **Plugin ↔ API.** One random secret per site, HTTPS only, sent as a bearer token
  both ways. Chat tokens use a key derived from it (HKDF), never the secret itself.
  Secrets rotate with a grace period and can be revoked.
* **At rest.** The whole database is SQLCipher-encrypted; additionally every course
  text, file, chat message and agent checkpoint is sealed (AES-256-GCM) under a
  per-course, per-file or per-user key, wrapped by keys derived from one master key.
  Logs never contain content.
* **Quotas.** Token budgets per user and per site (day / month, per turn), configured
  in the plugin with per-user overrides and exemptions; the operator can set hard
  ceilings the plugin cannot raise.

## Running the API

```bash
pip install .
moodle-llm-chat gen-master-key --out secrets/master.key     # mode 0600; back it up separately
export MLC_SECURITY__MASTER_KEY_FILE=secrets/master.key
cp .env.example .env                                         # choose providers and models
moodle-llm-chat doctor                                       # shows what is configured
moodle-llm-chat site add schule --name "Meine Schule" --url https://moodle.example.de
moodle-llm-chat serve                                        # behind a TLS-terminating proxy
```

Paste the printed site secret and the API URL into the plugin settings
(*Site administration → Plugins → Local plugins → Course chat (LLM)*), press **Test
connection**, then **Run full import now**. `docker compose up -d` runs the same with the
master key as a Docker secret.

Providers (`MLC_LLM__PROVIDER`, `MLC_EMBEDDING__PROVIDER`): `gemini`, `openai`
(also any OpenAI-compatible server via `MLC_LLM__BASE_URL`, e.g. vLLM or LM Studio),
`ollama`, `anthropic` (chat only). `MLC_LLM__THINKING=off|low|medium|high` enables
reasoning on models that support it; `MLC_AGENT__SHOW_THINKING` decides whether users
see it.

### Operator commands

| Command | |
|---|---|
| `site add / list / rotate / revoke / limits` | Manage connected Moodle sites and their hard token ceilings |
| `status --site X`, `usage --site X` | Content, processing backlog, token usage |
| `reprocess [--skipped-only]` | Re-extract documents (e.g. after enabling OCR) |
| `reembed --yes` | Drop vectors after changing the embedding model |
| `rotate-keys --new-master-key-file F` | New master key; data keys are re-wrapped, the DB is re-keyed |

## Development

### Trying it locally

`dev/` runs a real Moodle 4.5 with the plugin, the API and a real model of your choice
in Docker, for clicking through by hand:

```bash
cp dev/.env.example dev/.env        # provider, model, API key (Gemini, OpenAI, Ollama, …)
docker compose -f dev/docker-compose.yml up -d --build --wait
```

Open <http://localhost:8080> and log in as `student`, `student2` or `teacher`
(password `Test1234!`), or `admin` (`Admin1234!`). The plugin is already connected to
the API at <http://localhost:8000>, and two seeded courses are imported by cron within
a minute. Add your own courses as admin; they sync the same way.

* Model change: edit `dev/.env`, then `docker compose -f dev/docker-compose.yml up -d api`.
  If the embedding model or dimension changed, the API won't start until you run
  `… run --rm --no-deps --entrypoint moodle-llm-chat api reembed --yes` (content is kept
  and re-embedded).
* The plugin is bind-mounted: PHP, templates and a rebuilt `amd/build` apply on reload;
  a `version.php` bump needs `… restart moodle`. API changes need `… up -d --build api`.
  Data is kept. (Docker must be able to see this checkout.)
* A model on your machine (Ollama, LM Studio) is reachable as `host.docker.internal`.
* `… logs -f api` shows the agent; `… down -v` wipes all data.

### Tests

Tests are the specification — write the failing test first.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
ruff check . && ruff format --check . && mypy
pytest -m "not integration and not e2e"     # unit tier, seconds
pytest -m integration                       # every real component in-process
```

The integration tier runs the real app — encrypted store, pipeline, agent, websocket,
checkpointer, quotas — with only the chat model scripted, embeddings hashed and the
Moodle callback mocked at the HTTP level (`tests/support/harness.py`). It includes
wire-format checks of the real OpenAI and Ollama clients streaming tool calls.

A third tier, `tests/e2e`, drives a real browser against a real Moodle 4.5 with the
plugin installed, the API, and a scripted OpenAI-compatible model, all in Docker
(`docker compose -f tests/e2e/docker-compose.yml --profile test build`, then `up -d
--wait moodle` and `run --rm runner`; about 30 seconds once built). It covers import,
cited answers, clarifying questions, history, reconnects and per-user rights, and is
what caught the plugin/API contract mismatches the other tiers could not.

The plugin has its own PHPUnit suite and README in `moodle/local_llmchat/`; CI runs
it with `moodle-plugin-ci` against Moodle 4.5 and 5.0. `tests/fixtures/jwt_vectors.json`
is shared byte-for-byte with the plugin so both sides agree on chat tokens.
