# 010 — Linked-course discovery and self-enrolment

- **Status:** active
- **Tests:** `tests/unit/test_enrolment.py`, `tests/unit/test_crawler.py`

## Goal

`core_enrol_get_users_courses` — the function the crawler's course list comes from — only
returns courses the account is already enrolled in. Found on the live corpus: a Lernfeld 10
section in `Klassenkurs IT4bili` links to `moodle.itech-bs14.de/course/view.php?id=934`, a
second course on the same Moodle instance, entirely invisible to the crawler because it's just
a URL string, not an enrolment record.

Close that gap for the one safe case: a same-host course link that offers **self-enrolment
with no key required**. Verified against the live API (Moodle 5.0.3+): course 934 exposes
exactly this — `core_enrol_get_course_enrolment_methods` reports one enabled `type: "self"`
method, and `enrol_self_get_instance_info` for it returns no `enrolpassword` field, meaning no
key is needed.

## Acceptance criteria

### Link discovery
- `AC-1` A `url`-module external link whose host matches the configured Moodle host and whose
  path is `/course/view.php` with an `id` query parameter is recognised as a same-host course
  link, and its course id is extracted.
- `AC-2` A link to a course id already in the crawled set (already enrolled) is not treated as
  a new candidate.
- `AC-3` Links to other hosts, or to the same host but not `course/view.php`, are ignored.

### Self-enrolment (never guesses, never touches other enrolment types)
- `AC-4` `core_enrol_get_course_enrolment_methods` is queried for the candidate course; only
  methods with `type == "self"` and `status == true` are considered. `manual`, `guest`,
  `cohort` and any other type are never attempted — those need staff action.
- `AC-5` No self-enrolment method being enabled means no attempt is made.
- `AC-6` `enrol_self_get_instance_info` is queried for each candidate instance. Its response
  carries an `enrolpassword` field **only when a key is required** (confirmed against the
  actual Moodle source: the field is `VALUE_OPTIONAL`, present only when the instance has a
  password set). A key requirement means that instance is skipped — the password is never
  guessed, brute-forced, or requested from the user.
- `AC-7` `enrol_self_enrol_user` is called with an empty password only for an instance already
  confirmed key-free. A `status: false` result (full, enrolment window closed, banned, ...) is
  logged with Moodle's own warning message and treated as a decline, not an error.
- `AC-8` A Moodle API failure at any step (function disabled on this site, network error) is
  caught, logged, and treated as "could not enrol" — it never aborts the surrounding sync.

### After enrolling
- `AC-9` Once enrolled, the course's `fullname` is obtained by re-querying
  `core_enrol_get_users_courses` — not `core_course_get_courses`, which the live account lacks
  permission for even post-enrolment — and the course is crawled exactly like any other
  enrolled course, entering the same manifest.
- `AC-11` The re-query is retried with a bounded backoff before giving up. Verified live: a
  successful `enrol_self_enrol_user` (`status: true`) does not appear in
  `core_enrol_get_users_courses` immediately — five real enrolments on 2026-08-21 all showed
  this delay, confirmed resolved (course fully accessible) within the sync run's own runtime.
  Giving up after one immediate check would silently drop every course this feature enrols
  into, making the whole feature a no-op in practice.
- `AC-10` Every enrolment attempt — success, decline, or skip — is logged with the reason, so
  the account owner can see exactly which courses the bot joined and why, never silently.

## Non-goals

- No multi-hop following (a newly-joined course's own outbound links are picked up on the
  *next* `bsbot sync`, once it is a normal enrolled course, not within the same run).
- No enrolment via manual, guest, or key-protected self-enrolment — those require a human.
- No crawling of in-body HTML links (only structured `url`-module links, matching the one
  case found on the live corpus).
