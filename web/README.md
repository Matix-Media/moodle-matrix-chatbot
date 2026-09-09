# bsbot web chat

A Nuxt 4 + [Nuxt UI](https://ui.nuxt.com) chat interface for the Moodle bot, for people who aren't in
the Matrix room. Gated by a token (`CHAT_ACCESS_TOKEN`) — see `../specs/014-web-chat.md` for the
feature this talks to and `.env.example` for this app's own config.

Runs as its own Docker service (`web` in `../docker-compose.yml`) alongside a small internal HTTP API
(`api`, part of the bsbot Python package) that actually answers questions.

## Setup

```bash
pnpm install
cp .env.example .env   # fill in NUXT_CHAT_ACCESS_TOKEN etc. for local dev
```

## Development server

```bash
pnpm dev
```

## Production build

```bash
pnpm build
pnpm preview   # locally preview the production build
```
