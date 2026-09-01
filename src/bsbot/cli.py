"""Command line entry point.

Every milestone exposes its capability here, so each layer is usable and
verifiable long before the Matrix bot exists.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from pydantic import ValidationError

from bsbot.config import ConfigError, Settings, load_settings
from bsbot.index.search import HybridSearcher
from bsbot.index.store import Store
from bsbot.ingest.crawler import CourseCrawler
from bsbot.ingest.fetcher import Fetcher
from bsbot.ingest.indexer import Indexer
from bsbot.ingest.model import ContentKind
from bsbot.llm.embed import GeminiEmbedder
from bsbot.llm.gemini import CachingOcr, GeminiClient
from bsbot.logging import configure_logging
from bsbot.moodle.client import MoodleClient
from bsbot.moodle.errors import MoodleError
from bsbot.rag.pipeline import AnswerPipeline

app = typer.Typer(
    add_completion=False,
    help="Berufsschule Matrix bot — Moodle-backed question answering.",
    no_args_is_help=True,
)


def _fail(message: str) -> None:
    typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _settings() -> Settings:
    """Load settings, turning a validation error into a readable message.

    Every command funnels through here, because a stack trace is the least useful
    possible response to a typo in a .env file.
    """
    try:
        return load_settings()
    except ValidationError as exc:
        lines = ["Your .env has a value bsbot cannot use:"]
        for error in exc.errors():
            field = ".".join(str(p) for p in error["loc"])
            env_var = "BSBOT_" + field.upper().replace(".", "__")
            lines.append(f"  {env_var}: {error['msg'].removeprefix('Value error, ')}")
        _fail("\n".join(lines))
        raise  # unreachable; _fail exits


@app.command()
def doctor() -> None:
    """Report which subsystems are configured, without contacting anything."""
    settings = _settings()
    typer.echo(f"data dir: {settings.data_dir}")
    for name, require in (
        ("moodle (M1)", settings.require_moodle),
        ("gemini (M5)", settings.require_gemini),
        ("matrix (M7)", settings.require_matrix),
    ):
        try:
            require()
        except ConfigError:
            typer.secho(f"  [ ] {name}: not configured", fg=typer.colors.YELLOW)
        else:
            typer.secho(f"  [x] {name}: configured", fg=typer.colors.GREEN)


@app.command()
def whoami() -> None:
    """Log in to Moodle and print who we are and what the site exposes (M1 smoke test)."""
    settings = _settings()
    configure_logging(settings.log_level)
    try:
        moodle = settings.require_moodle()
    except ConfigError as exc:
        _fail(str(exc))
        return

    async def run() -> None:
        async with MoodleClient(moodle) as client:
            info = await client.site_info()
            typer.secho(f"site     : {info.sitename}", fg=typer.colors.GREEN)
            typer.echo(f"release  : Moodle {info.release}")
            typer.echo(f"user     : {info.username} (id {info.userid})")
            typer.echo(f"language : {info.lang}")
            if info.optimistic:
                typer.secho(
                    "functions: not reported by this site; running in optimistic mode",
                    fg=typer.colors.YELLOW,
                )
            else:
                typer.echo(f"functions: {len(info.functions)} exposed")
                interesting = [
                    "core_enrol_get_users_courses",
                    "core_course_get_contents",
                    "core_course_get_updates_since",
                    "mod_forum_get_forums_by_courses",
                    "mod_page_get_pages_by_courses",
                    "mod_resource_get_resources_by_courses",
                    "mod_assign_get_assignments",
                ]
                for name in interesting:
                    mark = "x" if info.has(name) else " "
                    colour = typer.colors.GREEN if info.has(name) else typer.colors.YELLOW
                    typer.secho(f"  [{mark}] {name}", fg=colour)

    try:
        asyncio.run(run())
    except MoodleError as exc:
        _fail(f"{type(exc).__name__}: {exc}")


@app.command()
def sync(
    follow_links: bool = typer.Option(
        True,
        help="Follow same-Moodle-host course links found in enrolled courses, and "
        "self-enrol when no enrolment key is required. Never touches courses that "
        "need a key or staff-managed enrolment; every attempt is logged.",
    ),
) -> None:
    """Crawl every enrolled course and persist the manifest (M2).

    Structure only — no files are downloaded, so this is cheap enough to run often.
    """
    from urllib.parse import urlsplit

    settings = _settings()
    configure_logging(settings.log_level)
    try:
        moodle = settings.require_moodle()
    except ConfigError as exc:
        _fail(str(exc))
        return

    async def run() -> None:
        async with MoodleClient(moodle) as client:
            result = await CourseCrawler(
                client,
                moodle_host=urlsplit(moodle.base_url).netloc,
                follow_linked_courses=follow_links,
            ).crawl()

        with Store(settings.index_db, embed_dim=settings.gemini.embed_dim) as store:
            before = {d.doc_id: d.timemodified for d in store.active_documents()}
            store.persist_crawl(result.items)
            after = {d.doc_id: d.timemodified for d in store.active_documents()}

            added = after.keys() - before.keys()
            removed = before.keys() - after.keys()
            changed = {k for k in before.keys() & after.keys() if before[k] != after[k]}
            pending = store.documents_needing_extraction(extract_version=1)

        by_kind: dict[str, int] = {}
        for item in result.items:
            by_kind[str(item.kind)] = by_kind.get(str(item.kind), 0) + 1

        typer.secho(
            f"courses: {result.courses_ok} ok, {result.courses_failed} failed",
            fg=typer.colors.GREEN if not result.courses_failed else typer.colors.YELLOW,
        )
        typer.echo(f"items  : {len(result.items)}  ({by_kind})")
        typer.echo(
            f"changes: +{len(added)} added, ~{len(changed)} changed, -{len(removed)} removed"
        )
        typer.echo(f"pending extraction: {len(pending)}")
        inline = sum(len(i.text or "") for i in result.items if i.kind is ContentKind.INLINE)
        typer.echo(f"inline text already available: {inline:,} chars")
        typer.echo(f"index: {settings.index_db}")

    try:
        asyncio.run(run())
    except MoodleError as exc:
        _fail(f"{type(exc).__name__}: {exc}")


@app.command()
def index(
    limit: int = typer.Option(0, help="Only process this many pending documents (0 = all)."),
    ocr: bool = typer.Option(
        False,
        help="Transcribe scanned PDF pages with Gemini. Costs API quota; needed for the "
        "scanned exam papers, which contain no extractable text at all.",
    ),
    retry_empty: bool = typer.Option(
        False,
        help="Re-process documents that previously yielded no text. Combine with --ocr "
        "to pick up scanned PDFs without re-extracting the whole corpus.",
    ),
    aliases_path: Path = typer.Option(
        Path("config/document_aliases.yaml"),
        "--aliases",
        help="Human-maintained doc_id -> search phrase overrides. Missing is fine.",
    ),
    realias: bool = typer.Option(
        False,
        help="Re-process only the documents named in --aliases, after editing that file. "
        "Cheap: does not touch the rest of the corpus.",
    ),
    hype: bool = typer.Option(
        False,
        help="Generate hypothetical student questions for each chunk (HyPE).",
    ),
    summarize: bool = typer.Option(
        False,
        help="Generate hierarchical document summaries for multi-chunk documents.",
    ),
    semantic: bool = typer.Option(
        False,
        help="Use semantic sentence-embedding chunking instead of fixed-size chunking.",
    ),
) -> None:
    """Fetch, extract and chunk everything the manifest reports as pending (M3+M4)."""
    from urllib.parse import urlsplit

    from bsbot.index.aliases import load_aliases

    aliases = load_aliases(aliases_path)

    settings = _settings()
    configure_logging(settings.log_level)
    try:
        moodle = settings.require_moodle()
    except ConfigError as exc:
        _fail(str(exc))
        return

    async def run() -> None:
        async with MoodleClient(moodle) as client:
            token = await client.login()

        with Store(settings.index_db, embed_dim=settings.gemini.embed_dim) as store:
            if retry_empty:
                reset = store.reset_extraction_for_empty_documents()
                typer.echo(f"retrying {reset} document(s) that previously yielded no text")

            if realias:
                if not aliases:
                    typer.secho(
                        f"--realias given but no aliases found at {aliases_path}",
                        fg=typer.colors.YELLOW,
                    )
                else:
                    reset = store.reset_extraction_for_doc_ids(list(aliases.keys()))
                    typer.echo(f"re-processing {reset} aliased document(s)")

            ocr_hook = None
            gemini_client = None
            embedder_fn = None
            if ocr or hype or summarize or semantic:
                try:
                    gemini_cfg = settings.require_gemini()
                    gemini_client = GeminiClient(gemini_cfg)
                    if ocr:
                        ocr_hook = CachingOcr(gemini_client, store)
                    if semantic:
                        gem_embedder = GeminiEmbedder(
                            gemini_client,
                            store=store,
                            model=gemini_cfg.embed_model,
                            dim=gemini_cfg.embed_dim,
                            batch_size=gemini_cfg.embed_batch_size,
                            rpm=gemini_cfg.embed_rpm,
                            items_per_minute=gemini_cfg.embed_items_per_minute,
                        )

                        def embedder_fn(texts: list[str]) -> list[list[float]]:
                            res = gem_embedder.embed_documents(texts, skip_failures=True)
                            return [v for v in res if v is not None]
                except ConfigError as exc:
                    if ocr or hype or summarize:
                        _fail(str(exc))
                        return

            async with Fetcher(
                store,
                moodle_token=token,
                moodle_host=urlsplit(moodle.base_url).netloc,
                max_concurrency=moodle.max_concurrency,
            ) as fetcher:
                stats = await Indexer(
                    store,
                    fetcher,
                    ocr=ocr_hook,
                    llm=gemini_client,
                    utility_model=settings.gemini.utility_model if gemini_client else None,
                    aliases=aliases,
                    moodle_host=urlsplit(moodle.base_url).netloc,
                    semantic=semantic,
                    embedder=embedder_fn,
                    hype=hype,
                    summarize=summarize,
                ).index_pending(limit=limit or None)
            total_chunks = store.connection.execute("select count(*) from chunks").fetchone()[0]


        typer.secho(
            f"indexed {stats.indexed} documents into {stats.chunks} chunks",
            fg=typer.colors.GREEN,
        )
        typer.echo(f"skipped: {stats.skipped}   failed: {stats.failed}")
        typer.echo(f"chunks in index: {total_chunks}")

    try:
        asyncio.run(run())
    except MoodleError as exc:
        _fail(f"{type(exc).__name__}: {exc}")


@app.command()
def embed(
    batch: int = typer.Option(0, help="Override batch size (0 = configured default)."),
) -> None:
    """Embed every chunk that does not yet have a vector (M5)."""
    settings = _settings()
    configure_logging(settings.log_level)
    try:
        gemini = settings.require_gemini()
    except ConfigError as exc:
        _fail(str(exc))
        return

    with Store(settings.index_db, embed_dim=gemini.embed_dim) as store:
        rows = store.connection.execute(
            "SELECT c.chunk_id, c.text FROM chunks c "
            "LEFT JOIN chunks_vec v ON v.chunk_id = c.chunk_id "
            "WHERE v.chunk_id IS NULL ORDER BY c.chunk_id"
        ).fetchall()
        if not rows:
            typer.secho("all chunks already embedded", fg=typer.colors.GREEN)
            return

        embedder = GeminiEmbedder(
            GeminiClient(gemini),
            store=store,
            model=gemini.embed_model,
            dim=gemini.embed_dim,
            batch_size=batch or gemini.embed_batch_size,
            rpm=gemini.embed_rpm,
            items_per_minute=gemini.embed_items_per_minute,
        )
        typer.echo(f"embedding {len(rows)} chunks...")
        vectors = embedder.embed_documents([r["text"] for r in rows], skip_failures=True)
        done = 0
        for row, vector in zip(rows, vectors, strict=True):
            if vector is not None:
                store.set_embedding(row["chunk_id"], vector)
                done += 1
        typer.secho(f"embedded {done}/{len(rows)} chunks", fg=typer.colors.GREEN)


@app.command()
def cron(
    interval_minutes: int = typer.Option(
        0, help="Minutes between cycles (0 = use BSBOT_SYNC_INTERVAL_MINUTES)."
    ),
    once: bool = typer.Option(
        False, help="Run a single sync -> index -> embed cycle and exit, instead of looping."
    ),
    follow_links: bool = typer.Option(True, help="Same as `bsbot sync --follow-links`."),
    aliases_path: Path = typer.Option(
        Path("config/document_aliases.yaml"),
        "--aliases",
        help="Human-maintained doc_id -> search phrase overrides. Missing is fine.",
    ),
) -> None:
    """Repeatedly sync, index and embed — the unattended long-running update loop.

    Meant to run as its own process (its own container in docker-compose), not
    inside `bsbot serve` — a multi-hour crawl or a large embedding batch must
    never delay the bot answering a question in the room.
    """
    from bsbot.sync_loop import run_cycle, run_forever

    settings = _settings()
    configure_logging(settings.log_level)
    try:
        moodle = settings.require_moodle()
        gemini = settings.require_gemini()
    except ConfigError as exc:
        _fail(str(exc))
        return

    interval = interval_minutes or settings.sync_interval_minutes

    async def cycle() -> None:
        await run_cycle(settings, moodle, gemini, aliases_path, follow_links=follow_links)

    async def run() -> None:
        if once:
            await cycle()
        else:
            await run_forever(cycle, interval_minutes=interval)

    asyncio.run(run())


@app.command()
def search(
    query: str = typer.Argument(..., help="A question, in German."),
    limit: int = typer.Option(8, help="How many chunks to show."),
    keyword_only: bool = typer.Option(False, help="Disable the vector retriever."),
) -> None:
    """Run hybrid retrieval and show the matching chunks (M5)."""
    settings = _settings()
    configure_logging("WARNING")

    with Store(settings.index_db, embed_dim=settings.gemini.embed_dim) as store:
        embedder = None
        if not keyword_only:
            try:
                gemini = settings.require_gemini()
                embedder = GeminiEmbedder(
                    GeminiClient(gemini),
                    store=store,
                    model=gemini.embed_model,
                    dim=gemini.embed_dim,
                    batch_size=gemini.embed_batch_size,
                    rpm=gemini.embed_rpm,
                )
            except ConfigError:
                typer.secho("no Gemini key: keyword-only search", fg=typer.colors.YELLOW)

        hits = HybridSearcher(store, embedder=embedder).search(query, limit=limit)

    if not hits:
        typer.secho("no matches", fg=typer.colors.YELLOW)
        return
    for rank, hit in enumerate(hits, start=1):
        sources = "+".join(hit.sources)
        typer.secho(
            f"{rank}. [{hit.score:.4f} {sources}] {hit.header_text[:88]}",
            fg=typer.colors.CYAN,
        )
        body = hit.text.split("\n\n", 1)[-1].replace("\n", " ")
        page = f" (S. {hit.page})" if hit.page else ""
        typer.echo(f"   {body[:190]}{page}")


@app.command()
def ask(
    question: str = typer.Argument(..., help="A question about the Berufsschule, in German."),
    no_expand: bool = typer.Option(False, help="Disable query expansion."),
    no_rerank: bool = typer.Option(False, help="Disable LLM reranking."),
    no_followup: bool = typer.Option(False, help="Disable the second-hop follow-up search."),
    no_decompose: bool = typer.Option(False, help="Disable query decomposition (sub-queries)."),
    no_step_back: bool = typer.Option(False, help="Disable step-back query generation."),
    compress_context: bool = typer.Option(
        False, help="Extract only relevant sentences from chunks before generating answer."
    ),
    no_crag: bool = typer.Option(False, help="Disable CRAG actionable fallback search links."),
) -> None:
    """Answer a question from the indexed Moodle content, with citations (M6)."""
    settings = _settings()
    configure_logging("WARNING")
    try:
        gemini = settings.require_gemini()
    except ConfigError as exc:
        _fail(str(exc))
        return

    with Store(settings.index_db, embed_dim=gemini.embed_dim) as store:
        client = GeminiClient(gemini)
        embedder = GeminiEmbedder(
            client,
            store=store,
            model=gemini.embed_model,
            dim=gemini.embed_dim,
            batch_size=gemini.embed_batch_size,
            rpm=gemini.embed_rpm,
            items_per_minute=gemini.embed_items_per_minute,
        )
        pipeline = AnswerPipeline(
            HybridSearcher(store, embedder=embedder),
            client,
            expand=not no_expand,
            rerank=not no_rerank,
            followup=not no_followup,
            decompose=not no_decompose,
            step_back=not no_step_back,
            compress_context=compress_context,
            crag=not no_crag,
            moodle_base_url=settings.moodle.base_url if settings.moodle.base_url else "https://moodle.itech-bs14.de",
            utility_model=gemini.utility_model,
        )
        answer = pipeline.answer(question)

    colour = typer.colors.GREEN if answer.grounded else typer.colors.YELLOW
    typer.secho(f"\n{answer.text}\n", fg=colour)
    if answer.citations:
        typer.secho("Quellen:", bold=True)
        for citation in answer.citations:
            page = f", S. {citation.page}" if citation.page else ""
            typer.echo(f"  [{citation.index}] {citation.header_text}{page}")
            if citation.url:
                typer.echo(f"      {citation.url}")
    if answer.used_queries and len(answer.used_queries) > 1:
        typer.secho(f"\n(Suchanfragen: {' | '.join(answer.used_queries)})", dim=True)


@app.command()
def serve(
    answer_all: bool = typer.Option(
        False,
        help="Also answer plain questions, not only messages addressed to the bot. "
        "Noisier; start without it.",
    ),
    no_decompose: bool = typer.Option(False, help="Disable query decomposition."),
    no_step_back: bool = typer.Option(False, help="Disable step-back prompting."),
    compress_context: bool = typer.Option(
        False, help="Extract only relevant sentences from chunks before generating answer."
    ),
    no_crag: bool = typer.Option(False, help="Disable CRAG actionable fallback search links."),
) -> None:
    """Run the Matrix bot (M7). Requires Matrix and Gemini configuration."""
    settings = _settings()
    configure_logging(settings.log_level)
    try:
        settings.require_matrix()  # fail fast on a bad config, not deep in the retry loop
        gemini = settings.require_gemini()
    except ConfigError as exc:
        _fail(str(exc))
        return

    from bsbot.matrix.runner import run_bot

    async def run() -> None:
        with Store(settings.index_db, embed_dim=gemini.embed_dim) as store:
            client = GeminiClient(gemini)
            embedder = GeminiEmbedder(
                client,
                store=store,
                model=gemini.embed_model,
                dim=gemini.embed_dim,
                batch_size=gemini.embed_batch_size,
                rpm=gemini.embed_rpm,
                items_per_minute=gemini.embed_items_per_minute,
            )
            pipeline = AnswerPipeline(
                HybridSearcher(store, embedder=embedder),
                client,
                decompose=not no_decompose,
                step_back=not no_step_back,
                compress_context=compress_context,
                crag=not no_crag,
                moodle_base_url=settings.moodle.base_url if settings.moodle.base_url else "https://moodle.itech-bs14.de",
                utility_model=gemini.utility_model,
            )
            await run_bot(
                # Re-resolved on every restart attempt, not just once — see
                # run_bot's docstring for why a frozen config is exactly what
                # let a dead-on-disk refresh token get retried forever.
                lambda: load_settings().require_matrix(),
                pipeline,
                store_dir=settings.matrix_store_dir,
                answer_all=answer_all,
                persist_tokens=lambda values: _write_env(values, settings.token_overrides_file),
            )

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        typer.echo("stopped")


@app.command(name="matrix-login")
def matrix_login() -> None:
    """Log the bot in via the OAuth device grant and save the result (M7).

    Needed for homeservers using next-gen auth (matrix.org accounts created through
    account.matrix.org), which have no legacy password. Crucially this creates a
    device the bot *owns*, so it can upload its own encryption keys — a token
    borrowed from Element cannot decrypt anything, because Element's private keys
    never leave that browser.
    """
    import httpx

    from bsbot.matrix.oauth import (
        DeviceGrantError,
        discover_endpoints,
        generate_device_id,
        matrix_scope,
        poll_for_token,
        register_client,
        start_device_authorization,
    )

    settings = _settings()
    homeserver = settings.matrix.homeserver
    if not homeserver:
        _fail("Set BSBOT_MATRIX__HOMESERVER first (e.g. https://matrix.org).")
        return

    async def run() -> None:
        device_id = generate_device_id()
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as http:
            endpoints = await discover_endpoints(http, homeserver)
            typer.echo(f"auth service: {endpoints['issuer']}")

            client_id = await register_client(
                http,
                endpoints["registration_endpoint"],
                "bsbot (Berufsschule assistant)",
                "https://github.com/bsbot",
            )
            auth = await start_device_authorization(
                http,
                endpoints["device_authorization_endpoint"],
                client_id,
                matrix_scope(device_id),
            )

            typer.secho("\nOpen this URL and approve the login:", bold=True)
            typer.secho(f"  {auth.verification_uri_complete}\n", fg=typer.colors.CYAN)
            typer.echo(f"  (code: {auth.user_code})")
            typer.echo("\nWaiting for approval...")

            tokens = await poll_for_token(
                http,
                endpoints["token_endpoint"],
                client_id,
                auth.device_code,
                interval=auth.interval,
                expires_in=auth.expires_in,
                sleep=asyncio.sleep,
            )

            whoami = await http.get(
                f"{homeserver.rstrip('/')}/_matrix/client/v3/account/whoami",
                headers={"Authorization": f"Bearer {tokens.access_token}"},
            )
            resolved = whoami.json() if whoami.status_code == 200 else {}

        typer.secho("\nLogged in.", fg=typer.colors.GREEN)
        typer.echo(f"  user_id  : {resolved.get('user_id', '?')}")
        typer.echo(f"  device_id: {resolved.get('device_id', device_id)}")

        env_values = {
            "BSBOT_MATRIX__ACCESS_TOKEN": tokens.access_token,
            "BSBOT_MATRIX__DEVICE_ID": str(resolved.get("device_id", device_id)),
            "BSBOT_MATRIX__USER_ID": str(resolved.get("user_id", settings.matrix.user_id or "")),
            "BSBOT_MATRIX__OAUTH_CLIENT_ID": client_id,
            "BSBOT_MATRIX__OAUTH_TOKEN_ENDPOINT": endpoints["token_endpoint"],
        }
        if tokens.refresh_token:
            env_values["BSBOT_MATRIX__REFRESH_TOKEN"] = tokens.refresh_token
        # The same path `serve` writes rotated tokens to (settings_customise_sources
        # gives it priority over real env vars) — not a bare `.env`, which is neither
        # writable (the container runs as a non-root user, with no .env baked into
        # the image) nor persistent (only the data volume survives a restart) when
        # this is run inside a deployed container rather than a local checkout.
        _write_env(env_values, settings.token_overrides_file)

        typer.secho(f"Saved to {settings.token_overrides_file}.", fg=typer.colors.GREEN)
        if tokens.refresh_token:
            typer.echo(
                "A refresh token was stored, so restarts and redeploys will not need "
                "this browser flow again."
            )
        else:
            typer.secho(
                "No refresh token was issued - the bot will need re-authorising when "
                "this access token expires.",
                fg=typer.colors.YELLOW,
            )
        typer.echo("Now run: bsbot serve")

    try:
        asyncio.run(run())
    except DeviceGrantError as exc:
        _fail(str(exc))


def _write_env(values: dict[str, str], path: Path) -> None:
    """Update an env file in place, replacing only the given keys.

    ``path`` has no default on purpose (see git history for why): it used to fall
    back to a bare ``.env`` in the working directory, which is wrong for anything
    other than local dev run from a checkout — inside a container that path is
    neither writable (non-root user, nothing baked into the image) nor persistent
    (only the data volume survives a restart). Every caller passes
    ``settings.token_overrides_file`` explicitly instead: that path lives inside
    the persistent data volume and is loaded with priority over real env vars
    (see ``Settings.settings_customise_sources``), which a plain ``.env`` write
    would not be — a container's env vars always beat a dotenv file, so writing
    rotated tokens to ``.env`` would be silently ignored after a restart.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text().splitlines() if path.exists() else []
    remaining = dict(values)
    out: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in remaining:
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)
    out.extend(f"{k}={v}" for k, v in remaining.items())
    path.write_text("\n".join(out) + "\n")


if __name__ == "__main__":
    app()
