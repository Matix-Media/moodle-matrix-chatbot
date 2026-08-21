# 002 — Moodle authentication and capability probe

- **Status:** active
- **Tests:** `tests/unit/test_moodle_client.py`, `tests/contract/test_moodle_contract.py`

## Goal

Turn a plain Moodle username and password into a usable REST client, and discover up front which
web service functions this particular Moodle exposes. Moodle sites differ widely in which functions
are enabled; the crawler must degrade gracefully rather than crash on the first missing one.

## Acceptance criteria

### Token acquisition
- `AC-1` A username and password are exchanged for a token via
  `GET /login/token.php?username=…&password=…&service=moodle_mobile_app`.
- `AC-2` A configured token is used directly, and no login request is made.
- `AC-3` Moodle signals login failure with **HTTP 200** and an `{"error": …}` body. This is detected
  and raised as `MoodleAuthError`, never treated as success.
- `AC-4` The error variant `{"errorcode": "enablewsdescription"}` (web services disabled site-wide)
  raises a distinct `MoodleWebServicesDisabled` carrying remediation text, because it is the one
  failure the user must fix in Moodle rather than in our config.
- `AC-5` Credentials never appear in exception messages, logs, or the `repr` of any object.

### Request layer
- `AC-6` Web service calls POST to `/webservice/rest/server.php` with `wstoken`, `wsfunction` and
  `moodlewsrestformat=json`; parameters go in the body, not the query string, so tokens are not
  captured in server access logs.
- `AC-7` Moodle's PHP-array parameter encoding is produced correctly for lists and nested structures
  (`courseids[0]=2&courseids[1]=5`, `options[0][name]=…&options[0][value]=…`).
- `AC-8` An `{"exception": …, "errorcode": …}` response body raises `MoodleAPIError` exposing
  `errorcode`, regardless of the HTTP status.
- `AC-9` Transient failures (HTTP 5xx, timeouts, connection errors) are retried with exponential
  backoff up to a configured limit; 4xx and Moodle-level exceptions are **not** retried.
- `AC-10` Concurrent requests are bounded so a sync never opens an unbounded number of connections
  against the school's server.

### Capability probe
- `AC-11` `core_webservice_get_site_info` is called once and its `functions` list cached, exposing
  `has(function_name) -> bool`.
- `AC-12` Calling a function the site does not expose raises `MoodleFunctionUnavailable` *without*
  issuing a network request.
- `AC-13` The probe reports the authenticated `userid`, `sitename` and Moodle `release`, which the
  `bsbot whoami` command prints as the M1 smoke test.
- `AC-14` If `core_webservice_get_site_info` omits `functions` (some configurations restrict it),
  the client degrades to optimistic mode: every function is assumed available and unavailability is
  discovered per call, rather than the probe failing hard.

## Non-goals

- No crawling, no file download, no content parsing — those are specs 003 and 004.
- No HTML scraping adapter. The port exists so one can be added; it is not implemented while the
  web service works.

## Notes

Verified against Moodle's documented behaviour: `login/token.php` returns HTTP 200 with an error
body on bad credentials, which is why `AC-3` is called out explicitly — a naive
`raise_for_status()` implementation would silently treat a failed login as success and then fail
much later with a confusing "invalid token".
