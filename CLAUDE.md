# Project instructions

## Before every push: reproduce CI locally

CI (`.github/workflows/ci.yml`) runs exactly this, in this order:

```bash
pip install -e ".[dev]"
ruff check .
ruff format --check .
mypy
pytest --cov=bsbot --cov-report=term-missing
```

Run all four check commands yourself before pushing — not a subset, and not just
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
   `src/bsbot/matrix/bot.py` that failed `ruff format --check` despite passing
   `ruff check`, and it was not caught until CI ran.)

3. **`mypy` only type-checks `src/bsbot`** (`packages = ["bsbot"]` in
   `pyproject.toml`), invoked bare in CI — running `mypy` locally with no
   arguments reproduces this exactly. Tests under `tests/` are not
   type-checked. Every function in `src/bsbot/**` needs full type annotations
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

   Missing `respx` shows up as an `ImportError` collecting `test_fetcher.py`,
   `test_matrix_oauth.py`, or `test_moodle_client.py`; missing `xlwt` shows up
   as an `ImportError` in `test_extract.py::TestXls`. Neither is a real code
   problem — it's a missing dev dependency, install the extra above.

## Quick pre-push checklist

```bash
pip install -e ".[dev]"       # once, or after dependency changes
ruff check .
ruff format --check .         # a separate check from the line above — see #1
mypy
pytest --cov=bsbot --cov-report=term-missing
```

All four must be clean before pushing.
