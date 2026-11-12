# Project instructions

`moodle_llm_chat` is an agentic course assistant for Moodle: a FastAPI service the
`local_llmchat` Moodle plugin pushes content to and whose websocket chat the plugin
embeds. Tests are the specification — there is no `specs/` directory any more. Write
the failing test first, then the code.

## Before every push: reproduce CI locally

CI (`.github/workflows/ci.yml`) runs exactly this, in this order:

```bash
pip install -e ".[dev]"
ruff check .
ruff format --check .
mypy
pytest -m "not integration and not e2e" --cov=moodle_llm_chat --cov-report=term-missing
pytest -m integration
```

Run all of these check commands yourself before pushing — not a subset, and not just
on the files you touched. This repo's CI has failed repeatedly on the same
handful of avoidable issues; this section exists so they get caught locally
instead of in a CI run.

## Common causes of CI failure here

1. **`ruff check` passing does not mean `ruff format --check` passes.** Lint
   (unused imports, bugs, complexity) and format (whitespace, line wrapping,
   quote style) are two independent ruff invocations in CI — a clean `ruff
   check .` says nothing about formatting. Always run both:

   ```bash
   ruff check .
   ruff format .        # rewrites files in place
   ruff format --check . # or: report only, don't rewrite
   ```

2. **Merging/rebasing onto `master` can pull in formatting drift you didn't
   write.** If a file elsewhere in the tree already violates the pinned ruff
   version's formatting, it can ride into your branch through a merge and fail
   CI even though you never touched that file. Don't scope the format check to
   just your changed files — run `ruff format --check .` over the *whole tree*
   after any merge from `master`, and fix whatever it flags regardless of who
   wrote it. (This has happened: a merge brought in one line in
   a file that failed `ruff format --check` despite passing
   `ruff check`, and it was not caught until CI ran.)

3. **`mypy` only type-checks `src/moodle_llm_chat`** (`packages = ["moodle_llm_chat"]` in
   `pyproject.toml`), invoked bare in CI — running `mypy` locally with no
   arguments reproduces this exactly. Tests under `tests/` are not
   type-checked. Every function in `src/moodle_llm_chat/**` needs full type annotations
   (`disallow_untyped_defs = true` — see `[tool.mypy]`); a missing return type
   or an untyped parameter on an otherwise-correct new function fails CI.

4. **Line length is 100** (`[tool.ruff] line-length = 100`), not the more
   common 79/88/120. The most common single-file lint failure (`E501`) comes
   from a long f-string, error message, or `typer.Option` help string. Run
   `ruff format .` before manually reflowing anything — it fixes most of these
   automatically.

5. **Local dev tooling is the `[dev]` extra, not the base install.** `pip
   install -e .` alone gets none of pytest/ruff/mypy/respx/xlwt/etc. Use the
   same command CI does:

   ```bash
   pip install -e ".[dev]"
   ```

   Missing `respx` shows up as an `ImportError` collecting the adapter tests
   (`test_taskcards.py` and friends); missing `xlwt` shows up
   as an `ImportError` in `test_extract.py::TestXls`. Neither is a real code
   problem — it's a missing dev dependency, install the extra above.

6. **Two pytest tiers, two CI jobs.** `tests/unit` is hermetic and fast;
   `tests/integration` runs the whole app in-process (marked `integration`
   automatically by `tests/integration/conftest.py`). Shared doubles live in
   `tests/support/` — import them from there, never from another test module or a
   `conftest.py`. A test that waits on the websocket must fail on an `error` frame
   (`support.harness.ws_until` does) or a failed turn hangs the run.

7. **Provider SDKs do not all use `httpx`.** `respx` mocks `httpx` (Ollama, our own
   clients); the OpenAI and Anthropic SDKs use `httpx2` — inject an
   `httpx2.MockTransport` client instead (see `test_provider_wire.py`). An unmocked
   call shows up as a connect timeout, not a respx error.

8. **The Moodle plugin** (`moodle/local_llmchat/`) is checked by the `moodle-plugin`
   CI job (moodle-plugin-ci: phplint, phpcs, phpdoc, validate, savepoints, mustache,
   grunt, phpunit). `tests/fixtures/jwt_vectors.json` must stay byte-identical to the
   plugin's copy — change both together.

9. **Browser (e2e) tests** live in `tests/e2e` and run only inside their compose stack
   (a normal `pytest` does not collect them):

   ```bash
   docker compose -f tests/e2e/docker-compose.yml --profile test build
   docker compose -f tests/e2e/docker-compose.yml up -d --wait moodle
   docker compose -f tests/e2e/docker-compose.yml run --rm runner      # ~30 s
   docker compose -f tests/e2e/docker-compose.yml down -v
   ```

   Plugin and tests are *copied into* their images, so rebuild after changing either.
   A full run takes about 30 seconds; anything much longer is a hang, not slowness.
   Three Playwright/Moodle traps already cost hours: (a) log in only after the login
   page is **network-idle** (typing within ~100 ms loses a session race, ~7% of
   logins); (b) after clicking a form-POST button, wait for the navigation
   (`expect_navigation`) **before** `page.close()` — close has no timeout and hangs
   forever mid-navigation; (c) the chat UI opens a fresh text segment after a tool
   card or question, so assert on the whole message body, not the first text
   region. The runner enforces `--timeout=120` and CI `timeout-minutes: 30`.

10. **The interaction wire format is a contract with the plugin** (`question` /
    `options` / `multi_select`, answers `{text, selected}`, `{confirmed}`, `{id}`),
    pinned by `tests/unit/test_interaction_wire.py`. Change both sides together. The same
    goes for tool-card **display blocks** (`agent/tools/display.py` ↔
    `amd/src/tooldetails.js::describeResult` + `templates/tool_result.mustache`), pinned by
    `tests/unit/test_tool_display.py`. A new tool composes existing block kinds; a new kind
    needs a branch in `describeBlock` and the template (unknown kinds render generically).

## Quick pre-push checklist

```bash
pip install -e ".[dev]"       # once, or after dependency changes
ruff check .
ruff format --check .         # a separate check from the line above — see #1
mypy
pytest -m "not integration and not e2e" --cov=moodle_llm_chat --cov-report=term-missing
pytest -m integration
```

All must be clean before pushing.
