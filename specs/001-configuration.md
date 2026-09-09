# 001 — Configuration and secrets

- **Status:** active
- **Tests:** `tests/unit/test_config.py`, `tests/unit/test_cli.py`

## Goal

One typed, validated place where every setting and secret enters the process, so that no module
reads `os.environ` directly and a misconfiguration fails loudly at startup rather than as a
confusing error deep inside a network call. Secrets come from a gitignored `.env`; the repo only
ever contains `.env.example`.

## Acceptance criteria

- `AC-1` Settings load from environment variables prefixed `BSBOT_`, with `__` separating nested
  sections (`BSBOT_MOODLE__BASE_URL` → `settings.moodle.base_url`).
- `AC-2` Settings load from a `.env` file, and a real environment variable overrides the file.
- `AC-3` Each subsystem (moodle, gemini, matrix) is independently constructible, so M1 can run with
  only Moodle configured and no Gemini or Matrix credentials present.
- `AC-4` A Moodle base URL is normalised: scheme required, trailing slashes stripped, so that
  `https://m.example.de/` and `https://m.example.de` behave identically.
- `AC-5` A Moodle base URL without a scheme, or with a non-http(s) scheme, is rejected with a
  validation error naming the field.
- `AC-6` Secret values (`password`, `token`, `api_key`) are typed such that they do not appear in
  `repr()`, log output, or tracebacks.
- `AC-7` `matrix.room_ids` accepts a comma-separated string and yields a list of room IDs, with
  surrounding whitespace stripped and empty entries dropped.
- `AC-8` `data_dir` resolves to an absolute path, and derived paths (`blobs/`, `index.db`,
  `matrix_store/`) are exposed as properties rather than reassembled by callers.
- `AC-9` Requesting a subsystem's settings when its required fields are absent raises a clear
  `ConfigError` naming the missing environment variables and the milestone that needs them.
- `AC-10` A configuration value that fails validation (e.g. a malformed URL) is reported by
  every CLI command as a plain, per-field message naming the `BSBOT_`-prefixed environment
  variable — never a pydantic traceback, which buries the one line the user actually needs.
- `AC-11` `bsbot doctor` reports, without contacting any external service, which optional
  subsystems (Moodle, Gemini, Matrix, the web API, PII tokenization) are configured and which
  are not — the diagnostic command reached for first when something is broken.

## Non-goals

- No runtime reconfiguration or hot reload; the process restarts to pick up changes.
- No secret manager integration. `.env` only, for now.

## Notes

`pydantic-settings` gives nested env parsing and validation. `SecretStr` covers `AC-6`.
`AC-9` exists because the milestones deliver value before all credentials exist — the bot must be
runnable for Moodle-only work without Matrix or Gemini keys present.
