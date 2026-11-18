# Setup guide: Moodle plugin + API server

This guide takes you from nothing to a working course chat: the Python **API server**
(`moodle-llm-chat`), the Moodle **plugin** (`local_llmchat`, optionally `block_llmchat`), and
the connection between them. Allow about 30 minutes if you already have a Moodle site, a
server and a domain name.

* [1. How the two parts talk to each other](#1-how-the-two-parts-talk-to-each-other)
* [2. Requirements](#2-requirements)
* [3. Set up the API server](#3-set-up-the-api-server)
* [4. Put TLS in front of it](#4-put-tls-in-front-of-it)
* [5. Install the Moodle plugin](#5-install-the-moodle-plugin)
* [6. Connect the two](#6-connect-the-two)
* [7. First import and first chat](#7-first-import-and-first-chat)
* [8. Configuration reference](#8-configuration-reference)
* [9. Operating it](#9-operating-it)
* [10. Troubleshooting](#10-troubleshooting)

Just want to try it on a laptop? Skip this guide and use the Docker stack in `dev/`
([README → Trying it locally](../README.md#trying-it-locally)). It wires everything up for you,
with fixed test passwords and plain http — not for production.

---

## 1. How the two parts talk to each other

```
                         ┌─────────────────────────────┐
                         │  Browser (student/teacher)  │
                         └───────┬───────────────▲─────┘
        (3) websocket wss://API/ws/chat          │ Moodle pages + chat UI
            first frame: chat token (JWT)        │
                         ┌───────▼───────┐       │      ┌────────────────────────────┐
                         │  API server   │◀──(1)─┼──────│ Moodle + local_llmchat     │
                         │ moodle-llm-chat│       └──────│  pushes content (Bearer    │
                         │               │───(2)───────▶│  secret), answers callbacks│
                         └───────────────┘              └────────────────────────────┘
```

There are three connections, and each one needs one thing to be true:

| # | Direction | What it is | What must be true |
|---|---|---|---|
| 1 | Moodle server → API | Content push (`/ingest/*`) and token budget reads, `Authorization: Bearer <site secret>` | The **API URL** entered in the plugin is reachable **from the Moodle server**, over `https://`. |
| 2 | API → Moodle server | Live access check before any document text reaches the model: `POST <site URL>/local/llmchat/callback.php`, same bearer secret | The **site URL** registered on the API is reachable **from the API server**. |
| 3 | Browser → API | The chat itself, over a websocket. The plugin issues a short-lived signed chat token (JWT) per user; the browser sends it as the first frame | The **same API URL** is reachable **from users' browsers**, and the browser's `Origin` matches the registered site URL. |

The one shared **site secret** is what authenticates 1 and 2 and (through a derived key) signs the
tokens for 3. You create it on the API side once and paste it into Moodle.

Three values must line up. Most setup problems are one of these being slightly different:

| Value | Set where | Must equal |
|---|---|---|
| **Site URL** | API: `moodle-llm-chat site add … --url` | Moodle's `$CFG->wwwroot`, character for character (no trailing slash needed). It is used as the callback target (`<site URL>/local/llmchat/callback.php`), as the token issuer (`iss`), and — reduced to scheme, host and port — as the allowed websocket/CORS origin. |
| **API URL** | Moodle: plugin setting *API URL* | The public `https://` address of the API (your reverse proxy). |
| **Site secret** | Printed by `site add` | Pasted into the plugin setting *Site secret*. |

> **Moodle in a sub-directory** (e.g. `https://example.org/moodle`) works: register the full
> `wwwroot` including the path. The API compares the browser's `Origin` (which never has a path)
> against the registered URL's scheme, host and port, and builds the callback from the full URL.

---

## 2. Requirements

**API server**

* Python **3.13+** (or Docker — the image is built from `python:3.13-slim`).
* A host that can run a long-lived process with a disk for the data directory.
* An **API key for a chat model** and an **embedding model** (Gemini, OpenAI, or any
  OpenAI-compatible server; Ollama for local models; Anthropic for chat only). Embeddings must
  come from Gemini, OpenAI or Ollama, so with Anthropic chat you need a second provider.
  The chat model must support **tool calling**.
* A **domain name and a TLS certificate** for the API. The plugin refuses plain `http://` API URLs
  (unless Moodle runs with developer debugging) and the API refuses non-TLS requests.

**Moodle**

* Moodle **4.5 up to 5.1** (`version.php`: supported 405–501), PHP 8.2+.
* **Cron running every minute** — the plugin's content sync is made of scheduled tasks.
* Moodle served over `https://` (a sub-directory such as `https://example.org/moodle` is fine).
* Admin rights, and file access to the Moodle code directory.

**Network** (check these before you start; they are the usual cause of failed setups)

* Moodle server → API: outbound HTTPS to the API host.
* API server → Moodle: outbound HTTPS to the Moodle host (the callback).
* Browsers → API: the API host must be reachable from wherever your users are.
* The API port itself (8000) should **not** be public — only the reverse proxy should reach it.
  See [section 4](#4-put-tls-in-front-of-it).

---

## 3. Set up the API server

Pick **one** of the two ways below. Either way you end up with the same service listening on
port 8000, using a data directory and a master key.

### 3.1 The master key

All stored data (course text, files, chat messages, agent state, the database itself) is
encrypted under keys derived from one **master key**. It is never stored next to the data.

```bash
moodle-llm-chat gen-master-key --out secrets/master.key
```

This writes a base64 key (32 random bytes) to a file with mode `0600`; it refuses to overwrite an
existing file. The server refuses to start if the key file is readable by group or others.

> **Back this key up separately from the data directory** (password manager, secrets vault).
> Without it the data cannot be decrypted — there is no recovery. If you lose it, you can only start
> over with an empty data directory and re-import from Moodle.

### 3.2 Option A: Docker Compose (recommended)

The repository root contains a `Dockerfile` and `docker-compose.yml`. The compose file runs the
API, keeps data in a named volume, and mounts the master key as a Docker secret.

**Step 1 — the master key.** The container runs as uid `10001`, and the server insists the key
file is readable by its user and by nobody else (mode `0600`). So the key file has to be **owned
by uid 10001**. Compose refuses to start while the file is missing, so create it before the first
`docker compose` command:

```bash
git clone <this repository> moodle-llm-chat && cd moodle-llm-chat
mkdir -p secrets

docker build -t moodle-llm-chat .
docker run --rm --user root -v "$PWD/secrets:/out" --entrypoint moodle-llm-chat \
  moodle-llm-chat gen-master-key --out /out/master.key
sudo chown 10001:10001 secrets/master.key       # mode stays 0600
```

(Generating the key with a host install of `moodle-llm-chat gen-master-key` works too — just
`chown` it the same way. Without the `chown` the API fails at start-up with
`PermissionError … /run/secrets/mlc_master_key`.)

**Steps 2–5 — configure, register, start:**

```bash
# 2. Model settings
cp .env.example .env
$EDITOR .env          # provider, models, API keys — see section 8

# 3. Check the configuration (contacts nothing)
docker compose run --rm api doctor

# 4. Register your Moodle site (prints the secret ONCE)
docker compose run --rm api site add schule --name "Meine Schule" --url https://moodle.example.org
#   → site schule added. Secret (shown once, store it in the plugin):
#   → mlc_…

# 5. Start
docker compose up -d
curl -s http://127.0.0.1:8000/healthz
#   → {"status":"ok","version":"…","chat":true}
```

`"chat": true` means the chat model is configured. `false` means chat settings are missing — run
`docker compose run --rm api doctor` and `docker compose logs api`.

The compose file publishes the API on `127.0.0.1:8000` only, so it is reachable by a reverse proxy
on the same host and by nobody else. Data lives in the `mlc-data` volume (`/data` in the container).

Later commands against a running stack:

```bash
docker compose exec api moodle-llm-chat site list
docker compose exec api moodle-llm-chat status --site schule
```

### 3.3 Option B: Python on the host

```bash
python3.13 -m venv /opt/moodle-llm-chat/venv
/opt/moodle-llm-chat/venv/bin/pip install /path/to/this/repository
```

Run it as an unprivileged user and keep its files in one place:

```bash
sudo useradd --system --home /var/lib/moodle-llm-chat --create-home mlc
sudo install -d -o mlc -g mlc -m 700 /etc/moodle-llm-chat
sudo -u mlc /opt/moodle-llm-chat/venv/bin/moodle-llm-chat gen-master-key \
  --out /etc/moodle-llm-chat/master.key
```

Create `/etc/moodle-llm-chat/env` (mode `0600`, owned by `mlc`) — the same variables as `.env`
in the repository, plus the paths:

```ini
MLC_DATA_DIR=/var/lib/moodle-llm-chat
MLC_SECURITY__MASTER_KEY_FILE=/etc/moodle-llm-chat/master.key
# The reverse proxy is on this machine, so don't listen on other interfaces.
MLC_SERVER__HOST=127.0.0.1

MLC_LLM__PROVIDER=gemini
MLC_LLM__MODEL=gemini-3.5-flash
MLC_LLM__API_KEY=…
MLC_EMBEDDING__PROVIDER=gemini
MLC_EMBEDDING__MODEL=gemini-embedding-001
MLC_EMBEDDING__DIM=768
MLC_EMBEDDING__API_KEY=…
```

CLI commands (`site add`, `status`, …) need the same variables as the service, and they must run
as `mlc` so the data directory keeps its owner. A tiny wrapper makes that painless:

```bash
sudo tee /usr/local/bin/mlc >/dev/null <<'EOF'
#!/bin/sh
set -a; . /etc/moodle-llm-chat/env; set +a
exec /opt/moodle-llm-chat/venv/bin/moodle-llm-chat "$@"
EOF
sudo chmod 755 /usr/local/bin/mlc
```

From here on, `sudo -u mlc mlc <command>` is `moodle-llm-chat <command>` with the right settings.
Check the configuration, then register the site:

```bash
sudo -u mlc mlc doctor
sudo -u mlc mlc site add schule --name "Meine Schule" --url https://moodle.example.org
```

`/etc/systemd/system/moodle-llm-chat.service`:

```ini
[Unit]
Description=moodle-llm-chat API
After=network-online.target
Wants=network-online.target

[Service]
User=mlc
EnvironmentFile=/etc/moodle-llm-chat/env
ExecStart=/opt/moodle-llm-chat/venv/bin/moodle-llm-chat serve
Restart=on-failure
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/var/lib/moodle-llm-chat

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now moodle-llm-chat
curl -s http://127.0.0.1:8000/healthz
```

### 3.4 What `site add` arguments mean

```
moodle-llm-chat site add <site-id> --name "<display name>" --url <moodle url>
```

| Argument | Meaning |
|---|---|
| `<site-id>` | Short stable id (e.g. `schule`, `bs14`). Goes into the token header (`kid`) and CLI commands like `status --site schule`. Pick it once. |
| `--name` | Display name, informational. |
| `--url` | **Your Moodle's `wwwroot`** — the `$CFG->wwwroot` value in Moodle's `config.php`: `https://…`, including a sub-directory if Moodle has one, without a trailing slash. A mismatch makes every chat token fail with "wrong issuer" and every websocket with "origin not allowed". |

The secret looks like `mlc_…` and is shown **once**. Copy it now; if you lose it, issue a new one
with `site rotate` (section 9).

---

## 4. Put TLS in front of it

The API serves plain HTTP on port 8000 and expects a TLS-terminating reverse proxy on the same
host. Two reasons this is not optional:

* The API checks that requests arrived over TLS (it trusts the proxy's `X-Forwarded-Proto`). Without
  it every ingest call gets `403 TLS required` and the websocket closes with code 4400.
* The Moodle plugin only accepts `https://` API URLs.

Because the API trusts forwarded headers from any peer, **never expose port 8000 directly** —
anyone who could reach it could claim "I came over TLS". Bind it to localhost (as the compose file
and the systemd example above do) or firewall it.

**Caddy** (gets the certificate automatically; websockets just work):

```caddyfile
chat-api.example.org {
    reverse_proxy 127.0.0.1:8000
}
```

**nginx** (the `Upgrade` headers and the long read timeout are what keep the websocket alive):

```nginx
server {
    listen 443 ssl http2;
    server_name chat-api.example.org;
    ssl_certificate     /etc/letsencrypt/live/chat-api.example.org/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/chat-api.example.org/privkey.pem;

    # Course files are uploaded through here; the default 1 MB would reject them.
    client_max_body_size 64m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade           $http_upgrade;
        proxy_set_header Connection        "upgrade";
        proxy_read_timeout 3600s;   # idle chat connections stay open
    }
}
```

Test it from a **different machine** than the server:

```bash
curl -s https://chat-api.example.org/healthz
# {"status":"ok","version":"0.2.0","chat":true}
```

If you can't get a certificate for a quick test, see the note on `MLC_SECURITY__REQUIRE_TLS` in
section 8 — but never leave that on for a real site.

---

## 5. Install the Moodle plugin

The repository holds two plugins:

| Directory | Moodle component | Install to | Needed? |
|---|---|---|---|
| `moodle/local_llmchat` | `local_llmchat` | `<moodle>/local/llmchat` | **Yes** — content sync, access checks, the chat page and chat UI |
| `moodle/block_llmchat` | `block_llmchat` | `<moodle>/blocks/llmchat` | Optional — the chat as a block/drawer next to a course. Requires `local_llmchat`. |

```bash
cd /var/www/moodle        # your Moodle code directory
cp -r /path/to/moodle-llm-chat/moodle/local_llmchat local/llmchat
cp -r /path/to/moodle-llm-chat/moodle/block_llmchat blocks/llmchat     # optional
sudo chown -R www-data:www-data local/llmchat blocks/llmchat           # your web user
```

The directory names matter: it must be `local/llmchat`, not `local_llmchat`.

Then run the upgrade, either in the browser (*Site administration → Notifications*, review and
confirm) or from the command line:

```bash
sudo -u www-data php admin/cli/upgrade.php
```

The plugin is marked alpha (`MATURITY_ALPHA`), so Moodle may flag its maturity on the plugin
check page; that is expected.

Verify under *Site administration → Plugins → Plugins overview* that **Course chat (LLM)** is
listed. Nothing is visible to users yet: the chat entry only appears once the plugin is
configured (next section).

### Who may use it

The plugin defines three capabilities. The defaults are usually what you want:

| Capability | Default roles | Effect |
|---|---|---|
| `local/llmchat:use` | Authenticated user, student, teacher, non-editing teacher, manager | See and use the chat. |
| `local/llmchat:manage` | Manager | Open *Sync status* and *Chat quotas*. |
| `local/llmchat:unlimited` | Manager | Exempt from per-user token limits. |

Change them in *Site administration → Users → Permissions → Define roles* if needed.

---

## 6. Connect the two

1. Go to *Site administration → Plugins → Local plugins → Course chat (LLM) → Settings*.
2. Under **Connection**:
   * **API URL** — the public HTTPS address of the API from section 4, e.g.
     `https://chat-api.example.org` (no trailing path).
   * **Site secret** — the `mlc_…` secret printed by `site add`. It is stored encrypted using
     Moodle's own encryption and never shown again.
3. **Save changes.** The settings form rejects an `http://` URL (unless developer debugging is on).
4. Open *Site administration → Plugins → Local plugins → Course chat (LLM) → Sync status* (also
   reachable from the *Test connection* link in the settings description) and choose
   **Test connection**.

A successful test shows "Connection successful." and records the site id returned by the API. It
verifies connection **1** (Moodle → API, and that the secret is right).

Connections **2** and **3** are exercised the first time a user chats. To check connection 2 before
that, from the API host:

```bash
curl -s -X POST https://moodle.example.org/local/llmchat/callback.php \
  -H 'Authorization: Bearer <the site secret>' \
  -H 'Content-Type: application/json' -d '{"action":"ping"}'
```

A JSON answer with HTTP 200 means the API host can reach Moodle and the secret matches. A `401`
means the secret differs; no answer at all means a network, DNS or certificate problem between the
API host and Moodle.

If you ever change the API URL or the secret, the plugin forgets the cached site id and re-queues
its quota policy push on its own.

---

## 7. First import and first chat

**Make sure Moodle cron is running every minute** (the usual `* * * * * php admin/cli/cron.php`
entry). Without it nothing is ever pushed.

1. On *Sync status*, choose **Run full import now**. It queues a task that the next cron run
   starts. It works through the courses in time slices and resumes where it stopped, so large sites
   take several runs.
2. Watch progress on the same page (courses done, documents pushed, files uploaded, outbox state)
   and on the API side:

   ```bash
   moodle-llm-chat status --site schule     # content counts and processing backlog
   ```

   Pushing is quick; the API then extracts text, optionally runs OCR and creates embeddings in the
   background. **This is where your model provider is billed**, and large course sets take a while.
   To start small, set *Included courses* (course ids, comma-separated) in the plugin settings
   before the first import.
3. Open a course you are enrolled in. The course navigation has a **Chat** entry (also in the user
   menu). Ask something about the course material. Answers cite their sources with links.

From then on, edits in Moodle reach the API automatically: event observers write changes to an
outbox, a task pushes it every minute (failures retry with growing back-off), and a nightly full
sync at about 02:00 repairs anything that was missed.

To add the block as well: *Add a block → Course chat* in a course (enable "Display throughout the
course"), or on the Dashboard for a global chat.

---

## 8. Configuration reference

### API server (environment variables or `.env`)

Every variable starts with `MLC_`; nested settings use a double underscore, e.g.
`MLC_LLM__PROVIDER`. The server reads the environment and a `.env` file in its working directory.
`moodle-llm-chat doctor` prints what is configured and what is missing, without contacting
anything.

**Required**

| Variable | Meaning |
|---|---|
| `MLC_SECURITY__MASTER_KEY_FILE` | Path to the master key file (mode `0600`). Preferred. |
| `MLC_SECURITY__MASTER_KEY` | The key itself, as an alternative to the file. |
| `MLC_LLM__PROVIDER` | `gemini`, `openai`, `ollama` or `anthropic`. |
| `MLC_LLM__MODEL` | Chat model name. Must support tool calling. |
| `MLC_LLM__API_KEY` | Not needed for Ollama or for an OpenAI-compatible server with `BASE_URL`. |
| `MLC_EMBEDDING__PROVIDER` | `gemini`, `openai` or `ollama`. |
| `MLC_EMBEDDING__MODEL` | Embedding model name. |
| `MLC_EMBEDDING__DIM` | Vector size (default 768). **Changing it, or the model, later requires `reembed`.** |
| `MLC_EMBEDDING__API_KEY` | As for the chat model. |

**Common options**

| Variable | Default | Meaning |
|---|---|---|
| `MLC_DATA_DIR` | `./data` | Where the encrypted database and file blobs live. Use an absolute path. |
| `MLC_SERVER__HOST` / `MLC_SERVER__PORT` | `0.0.0.0` / `8000` | Bind address. Use `127.0.0.1` behind a proxy on the same host. |
| `MLC_LLM__BASE_URL` | – | Endpoint for Ollama, or any OpenAI-compatible server (vLLM, LM Studio, OpenRouter). |
| `MLC_LLM__UTILITY_MODEL` | the chat model | Cheaper model for conversation titles and OCR; always called with thinking off. |
| `MLC_LLM__THINKING` | `off` | `off`, `low`, `medium`, `high` — reasoning level for models that support it. |
| `MLC_AGENT__SHOW_THINKING` | `indicator` | `full`, `indicator` or `off` — how much reasoning users see. |
| `MLC_AGENT__TIMEZONE` | `Europe/Berlin` | Used for "today", "this week" and quota resets. **Set this to yours.** |
| `MLC_AGENT__GENERATE_TITLES` | `true` | Name new conversations with the utility model; `false` uses the first message. |
| `MLC_AGENT__DISABLED_TOOLS` | – | JSON list of agent tools to switch off, e.g. `["search_regex"]`. |
| `MLC_OCR__ENABLED` | `false` | OCR for scanned PDF pages (uses the chat provider's vision). After enabling it on a populated index, run `moodle-llm-chat reprocess`. |
| `MLC_SERVER__BURST_PER_MINUTE` | `20` | Messages per user per minute. |
| `MLC_SERVER__MAX_WS_PER_USER` | `3` | Concurrent chat connections per user. |
| `MLC_MOODLE__CALLBACK_TIMEOUT_S` | `5` | How long the API waits for Moodle's live access check. |
| `MLC_LOG_LEVEL` | `INFO` | Logs never contain course or chat content. |

Provider examples (these also appear, commented, in `dev/.env.example`):

```ini
# OpenRouter / any OpenAI-compatible endpoint for chat (model must support tools)
MLC_LLM__PROVIDER=openai
MLC_LLM__BASE_URL=https://openrouter.ai/api/v1
MLC_LLM__MODEL=google/gemma-4-31b-it:free
MLC_LLM__API_KEY=sk-or-…

# Ollama on the same host
MLC_LLM__PROVIDER=ollama
MLC_LLM__MODEL=qwen3:14b
MLC_LLM__BASE_URL=http://localhost:11434
MLC_EMBEDDING__PROVIDER=ollama
MLC_EMBEDDING__MODEL=nomic-embed-text
MLC_EMBEDDING__DIM=768
MLC_EMBEDDING__BASE_URL=http://localhost:11434

# Anthropic for chat (embeddings must come from another provider)
MLC_LLM__PROVIDER=anthropic
MLC_LLM__MODEL=claude-sonnet-5-5
MLC_LLM__API_KEY=sk-ant-…
```

(Inside Docker, a model server on the host is `http://host.docker.internal:<port>`; the compose
file in the repository root does not add that name by default — add
`extra_hosts: ["host.docker.internal:host-gateway"]` to the `api` service if you need it.)

**Local development only**

| Variable | Meaning |
|---|---|
| `MLC_SECURITY__REQUIRE_TLS=false` | Accept plain http/ws. The plugin additionally needs Moodle's developer debugging for an `http://` API URL. Never use this in production. |

### Moodle plugin settings

*Site administration → Plugins → Local plugins → Course chat (LLM) → Settings*

| Setting | Meaning |
|---|---|
| **API URL**, **Site secret** | The connection — section 6. |
| **Sync enabled** | Master switch for pushing content changes. |
| **Included courses** | Comma-separated course ids; empty means all courses. |
| **Maximum file size (MB)** | Larger files are not sent. (The API also caps files at 25 MB.) |
| **File types** | Comma-separated extensions that are sent. |
| **Maximum push attempts** | After this many failures an outbox entry stops retrying and is shown on *Sync status* (**Retry given-up entries** resets them). |
| **Full sync slice (seconds)** | Maximum runtime of one full-sync run; it resumes next time. |
| **Quotas** | Site and per-user token budgets per day/month and a per-answer cap (`0` = unlimited), whether reasoning tokens count, and whether exempt users bypass the site budget. Each change is pushed to the API as the site policy. |

Extra pages in the same menu: **Sync status** (test connection, run or clear-and-reimport, outbox
state) and **Chat quotas** (usage, per-user overrides and exemptions).

The operator on the API host can set **hard ceilings** the plugin's quotas can never exceed:

```bash
moodle-llm-chat site limits schule --tokens-per-day 2000000 --tokens-per-month 30000000
```

---

## 9. Operating it

**Backups.** Back up the data directory (the `mlc-data` volume, or `MLC_DATA_DIR`) and, *separately*,
the master key. Neither is useful alone.

**Updating.** Pull the new version, then `docker compose up -d --build` (or `pip install` again and
restart the service). Update the plugin by replacing `local/llmchat` and running the Moodle upgrade.
Update both sides together — the websocket messages and tool-card formats are a contract between them.

**Changing the embedding model or dimension.** The API will not start while the stored vectors don't
match the configuration. Drop the vectors (content is kept and re-embedded in the background):

```bash
docker compose run --rm --no-deps api reembed --yes     # or: moodle-llm-chat reembed --yes
docker compose up -d
```

**Rotating the site secret.** The old secret keeps working for a grace period so Moodle isn't cut off:

```bash
moodle-llm-chat site rotate schule --grace 3600    # prints the new secret
```

Paste the new one into the plugin settings within the grace period.

**Revoking a site.** `moodle-llm-chat site revoke schule` immediately refuses all its requests and
chat tokens. Use it if a secret leaked, then add the site again.

**Rotating the master key.** Stop the API, then:

```bash
moodle-llm-chat gen-master-key --out secrets/master.new
moodle-llm-chat rotate-keys --new-master-key-file secrets/master.new
```

Point `MLC_SECURITY__MASTER_KEY_FILE` at the new file and start again.

**Everyday commands**

| Command | What it shows or does |
|---|---|
| `moodle-llm-chat doctor` | What is configured (reads settings only). |
| `moodle-llm-chat site list` | Registered sites and whether they are active or revoked. |
| `moodle-llm-chat status --site schule` | Content counts, processing backlog, last sync. |
| `moodle-llm-chat usage --site schule --period day` | Token usage per user (`day` or `month`). |
| `moodle-llm-chat reprocess [--skipped-only]` | Re-extract documents, e.g. after enabling OCR. |

---

## 10. Troubleshooting

### Test connection fails

The message on *Sync status* tells you which of the three layers broke:

| Message | Meaning and fix |
|---|---|
| "The API URL must use https://" | The plugin refuses plain http. Use the proxy's `https://` URL (developer debugging only for local tests). |
| "The chat API cannot be reached." | Moodle's server can't connect: wrong host/port, DNS, firewall, or the proxy isn't running. Test with `curl https://<API URL>/healthz` **from the Moodle server**. |
| "The chat API answered with HTTP 401" | Wrong or outdated secret. Paste it again; if it was lost, `site rotate`. After a rotation or revoke the plugin pauses its outbox until you save a valid secret. |
| "…HTTP 403" (body: `TLS required`) | The API doesn't see the request as TLS. Your proxy must send `X-Forwarded-Proto: https`. |
| "…HTTP 502/503/504" | The proxy is up, the API isn't (or is still starting). Check `docker compose logs api` / `journalctl -u moodle-llm-chat`. |
| "The chat is not configured yet" | API URL or secret is empty. |

### The chat opens but won't connect or answers fail

Check the browser's developer console, then the websocket close code:

| Code | Cause |
|---|---|
| 4400 | Not TLS (`ws://`). Use an `https://` API URL, and make the proxy forward `X-Forwarded-Proto`. |
| 4401 | Token rejected: secret mismatch, expired token (the user's clock or the server's clock is far off), or the site is revoked. Check that Moodle's and the API's clocks are in sync (NTP). |
| 4403 | **Origin not allowed.** The site URL given to `site add --url` doesn't match what users type in the address bar (`http` vs `https`, `www.` vs none, a different host name). Fix by registering the exact `wwwroot` — see below. |
| 4429 | Too many simultaneous chat connections for this user (`MLC_SERVER__MAX_WS_PER_USER`). Message bursts (`MLC_SERVER__BURST_PER_MINUTE`) show a "too many messages" notice in the chat instead. |
| 4503 | The API has no chat model configured. Run `doctor`. |

Browser console messages about the connection being blocked usually mean the websocket upgrade isn't
passing through the proxy (nginx: the `Upgrade`/`Connection` headers) or the API host isn't
reachable from the user's network.

**"Wrong issuer" in API logs, or code 4403, for an already-registered site.** The registered URL is
wrong. There is no command to edit a URL, so revoke the site and register it again with the exact
`wwwroot` (`site revoke schule`, then `site add schule2 …`), paste the new secret into Moodle, and
run the full import again (the new site id starts with an empty index).

### "Moodle could not be reached, so no content was read" (in a tool card)

The live access check (connection 2) failed, so the API refuses to show anything — by design.
From the API host, run the `ping` request from [section 6](#6-connect-the-two). Common causes: the
Moodle host isn't reachable from the API server (firewall, split-horizon DNS), a certificate the API
host doesn't trust, or a web server that **strips the `Authorization` header** before PHP sees it
(common with Apache + PHP-FPM/CGI). With Apache, this usually fixes it:

```apache
RewriteEngine On
RewriteRule .* - [E=HTTP_AUTHORIZATION:%{HTTP:Authorization}]
```

After three failures in a row the API stops asking for 30 seconds (circuit breaker) — wait, then retry.

### The assistant finds nothing / content is missing

* Is cron running? *Site administration → Server → Tasks → Scheduled tasks* shows the last run of
  *Push pending content changes to the chat API* (every minute) and *Full content sync* (nightly).
* *Sync status* shows the outbox: "retrying" or "given up" entries and the last error.
* `moodle-llm-chat status --site schule` shows whether text extraction and embedding are still
  working through a backlog, or have stalled (usually a missing/wrong embedding API key or a quota).
* The agent only sees what *that user* may see. Hidden activities, restricted sections and courses
  they aren't enrolled in are not found — that's the intended behaviour.
* Files larger than the size limit or with an extension not in *File types* are skipped; scanned PDFs
  need `MLC_OCR__ENABLED=true` (then `reprocess`).

### The API won't start

| Error | Fix |
|---|---|
| "no master key configured" | Set `MLC_SECURITY__MASTER_KEY_FILE` (or `MLC_SECURITY__MASTER_KEY`). |
| "master key file … is accessible by other users; chmod it to 0600" | `chmod 600` the file. In Docker, the file must also be owned by the container user (uid 10001). |
| "cannot read master key file" | Wrong path, or the service user can't read it. |
| "must decode to 32 bytes" / "not valid base64" | The file isn't a key generated by `gen-master-key`. |
| Vector/embedding mismatch | You changed `MLC_EMBEDDING__MODEL` or `__DIM`. Run `reembed --yes`. |
| `doctor` says "not configured" | The listed `MLC_…` variables are missing. Remember the `MLC_` prefix and the double underscores. |
| A wrong or unknown master key for an existing database | The database can't be opened — use the key it was created with. |

### Quick end-to-end check

1. `curl https://<API URL>/healthz` from outside → `"chat": true`.
2. *Test connection* in Moodle → "Connection successful."
3. `ping` request from the API host to `callback.php` → HTTP 200.
4. *Run full import now*, then `status --site …` shows content.
5. Open the chat in a course and ask a question about something in it.
