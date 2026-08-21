"""Self-enrolment into linked courses — see ``specs/010-linked-course-discovery.md``.

Berufsschule Moodle sections link to *other* courses on the same instance that
carry material relevant to the linking course — found on the live corpus: a
Lernfeld 10 section linking to a course called "wirtschaftliche Betrachtung".
``core_enrol_get_users_courses`` only returns courses the account is already
enrolled in, so a link like that is otherwise invisible to the crawler, forever.

This closes that gap for exactly one safe case: self-enrolment with no key
required, verified against Moodle's actual source (``enrol/self/externallib.php``)
rather than guessed from documentation — ``enrolpassword`` is a ``VALUE_OPTIONAL``
field on the instance-info response, present only when a key is set. No other
enrolment type (manual, guest, cohort) is ever attempted, and a key requirement is
never worked around — those need a human.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import Any, Protocol
from urllib.parse import parse_qs, urlsplit

import structlog

log = structlog.get_logger(__name__)


class MoodleCaller(Protocol):
    async def call(self, wsfunction: str, **params: Any) -> Any: ...


#: How long to wait for a fresh self-enrolment to appear in
#: core_enrol_get_users_courses. Verified live: real enrolments on 2026-08-21 did
#: not appear on the first re-query but were visible within a few seconds — giving
#: up immediately would silently drop every course this feature enrols into.
DEFAULT_ENROLMENT_WAIT_ATTEMPTS = 5
DEFAULT_ENROLMENT_WAIT_DELAY_S = 2.0


async def await_enrolled_course(
    moodle: MoodleCaller,
    *,
    userid: int,
    courseid: int,
    attempts: int = DEFAULT_ENROLMENT_WAIT_ATTEMPTS,
    delay_s: float = DEFAULT_ENROLMENT_WAIT_DELAY_S,
    sleep: Callable[[float], Awaitable[None] | None] = asyncio.sleep,
) -> dict[str, Any] | None:
    """Poll core_enrol_get_users_courses until the new course appears (AC-11)."""
    for attempt in range(attempts):
        courses = await moodle.call("core_enrol_get_users_courses", userid=userid)
        course = next((c for c in (courses or []) if c.get("id") == courseid), None)
        if course is not None:
            return course
        if attempt + 1 < attempts:
            log.info(
                "enrol.awaiting_propagation",
                courseid=courseid,
                attempt=attempt + 1,
                attempts=attempts,
            )
            result = sleep(delay_s)
            if result is not None:
                await result
    log.warning("enrol.propagation_timeout", courseid=courseid, attempts=attempts)
    return None


def extract_linked_course_id(url: str, *, moodle_host: str) -> int | None:
    """Recognise a same-host ``course/view.php?id=N`` link (AC-1, AC-3)."""
    parts = urlsplit(url)
    if parts.netloc != moodle_host or not parts.path.endswith("/course/view.php"):
        return None
    ids = parse_qs(parts.query).get("id")
    if not ids:
        return None
    try:
        return int(ids[0])
    except ValueError:
        return None


#: Any mod/<type>/view.php link — page, resource, url, forum, ... (spec 011 AC-14).
_MODULE_VIEW_RE = re.compile(r"^/mod/[a-z][a-z0-9_]*/view\.php$")


def extract_linked_module_cmid(url: str, *, moodle_host: str) -> int | None:
    """Recognise a same-host direct module link (a single page/resource, not a
    whole course) and return its course-module id (spec 011 AC-14)."""
    parts = urlsplit(url)
    if parts.netloc != moodle_host or not _MODULE_VIEW_RE.match(parts.path):
        return None
    ids = parse_qs(parts.query).get("id")
    if not ids:
        return None
    try:
        return int(ids[0])
    except ValueError:
        return None


async def resolve_module_course(moodle: MoodleCaller, cmid: int) -> int | None:
    """Resolve a course-module id to its owning course id.

    Verified live against core_course_get_course_module (spec 011 AC-14). A
    deleted or genuinely inaccessible module is a clean skip (AC-16), matching
    every other failure path in this module.
    """
    try:
        result = await moodle.call("core_course_get_course_module", cmid=cmid)
    except Exception as exc:
        log.info("enrol.module_resolve_failed", cmid=cmid, error=str(exc))
        return None
    course_id = (result or {}).get("cm", {}).get("course")
    return int(course_id) if course_id is not None else None


async def try_self_enrol(moodle: MoodleCaller, courseid: int) -> bool:
    """Attempt key-free self-enrolment. Returns whether it succeeded.

    Every outcome is logged (AC-10) — this changes real, visible state on the
    school's Moodle, so it must never happen quietly.
    """
    try:
        methods = await moodle.call("core_enrol_get_course_enrolment_methods", courseid=courseid)
    except Exception as exc:  # AC-8
        log.info("enrol.methods_failed", courseid=courseid, error=str(exc))
        return False

    # AC-4: only "self" enrolment is ever attempted. Manual, guest and cohort
    # enrolment need a human on the other end.
    candidates = [m for m in (methods or []) if m.get("type") == "self" and m.get("status")]
    if not candidates:
        log.info("enrol.no_self_enrolment", courseid=courseid)
        return False

    for method in candidates:
        instance_id = method["id"]
        try:
            info = await moodle.call("enrol_self_get_instance_info", instanceid=instance_id)
        except Exception as exc:  # AC-8
            log.info(
                "enrol.instance_info_failed",
                courseid=courseid,
                instance_id=instance_id,
                error=str(exc),
            )
            continue

        if info.get("enrolpassword"):  # AC-6: never guessed, never worked around
            log.info("enrol.requires_password", courseid=courseid, instance_id=instance_id)
            continue

        try:
            result = await moodle.call(
                "enrol_self_enrol_user", courseid=courseid, instanceid=instance_id, password=""
            )
        except Exception as exc:  # AC-8
            log.warning("enrol.failed", courseid=courseid, instance_id=instance_id, error=str(exc))
            continue

        if result.get("status"):
            log.info("enrol.success", courseid=courseid, instance_id=instance_id)
            return True

        log.info(  # AC-7: a decline (full, closed, banned, ...) is not an error
            "enrol.declined",
            courseid=courseid,
            instance_id=instance_id,
            warnings=result.get("warnings") or [],
        )

    return False
