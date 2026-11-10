# local_llmchat — course chat for Moodle

Moodle 4.5+/5.x (PHP 8.2+) local plugin. It is both the **content source** of the
`moodle_llm_chat` API (it pushes course content and visibility) and the **chat UI** (a
websocket client inside Moodle).

## Install

1. Copy this directory to `<moodle>/local/llmchat` and run the upgrade.
2. On the API host: `moodle-llm-chat site add "<name>"`, which prints a secret once.
3. In *Site administration → Plugins → Local plugins → Course chat (LLM) → Settings*, enter
   the API URL (it must be `https://`; `http://` only works with developer debugging) and the
   secret. The secret is stored encrypted with `\core\encryption`.
4. Open *Sync status* and choose **Test connection**, then **Run full import now**.

## How it fits together

| Direction | Mechanism |
|---|---|
| Moodle → API | `Authorization: Bearer <secret>` on `/ingest/*` (`classes/api_client.php`). Event observers only write to `local_llmchat_outbox`; `drain_outbox_task` pushes every minute, with backoff of 1 min doubling up to 6 h. The nightly `full_sync_task` is manifest-based and resumes at the next course. |
| API → Moodle | `callback.php` (`ping`, `check_access {userid, cmids}`, `user_info {userid}` → `{groups}`, `resolve_user`), protected by the same bearer secret. |
| Browser → API | `local_llmchat_issue_token` (AJAX, sesskey) issues an HS256 JWT signed with `HKDF-SHA256(secret, info="chat-token")`. Header `kid` = the API's site id. It lives 10 min (API limit 15) and carries `cmids`, `roles`, `quota` and, when opened from a course, `course`. `amd/src/chat.js` sends `auth{token}` as its first websocket frame. |

`tests/fixtures/jwt_vectors.json` is a copy of the API's shared vectors (HKDF/HS256 with `kid`); the PHP encoder reproduces them byte for byte.

## Tests

```bash
vendor/bin/phpunit --testsuite local_llmchat_testsuite   # inside a Moodle checkout with phpunit initialised
moodle-plugin-ci phplint && moodle-plugin-ci phpcs && moodle-plugin-ci phpunit
```

The tool-card parser (`amd/src/tooldetails.js`, pure functions) has its own tests:

```bash
node --test tests/js/tooldetails.test.mjs
```

The API is never contacted: tests inject `tests/fixtures/fake_transport.php` through
`api_client::set_test_transport()`.

`amd/build/` must be regenerated with `grunt amd` (from the Moodle root) after editing `amd/src/`.
