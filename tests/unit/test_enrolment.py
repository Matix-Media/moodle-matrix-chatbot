"""Verifies spec 010 — self-enrolment into linked courses.

Every fixture shape here (enrolment method list, instance info, enrol_user result)
was verified against the real Moodle API before being written, not guessed from
documentation — see specs/010-linked-course-discovery.md.
"""

from __future__ import annotations

from typing import Any

import structlog.testing

from bsbot.cron.moodle.enrolment import (
    extract_linked_course_id,
    extract_linked_module_cmid,
    resolve_module_course,
    try_self_enrol,
)

MOODLE_HOST = "moodle.itech-bs14.de"


class FakeMoodle:
    def __init__(self, responses: dict[str, Any] | None = None, fail: set[str] | None = None):
        self._responses = responses or {}
        self._fail = fail or set()
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, wsfunction: str, **params: Any) -> Any:
        self.calls.append((wsfunction, params))
        if wsfunction in self._fail:
            raise RuntimeError(f"{wsfunction} failed")
        return self._responses.get(wsfunction)


class TestLinkExtraction:
    def test_recognises_a_same_host_course_link(self) -> None:
        """AC-1"""
        assert (
            extract_linked_course_id(
                "https://moodle.itech-bs14.de/course/view.php?id=934", moodle_host=MOODLE_HOST
            )
            == 934
        )

    def test_extra_query_parameters_do_not_break_extraction(self) -> None:
        """AC-1"""
        assert (
            extract_linked_course_id(
                "https://moodle.itech-bs14.de/course/view.php?notifyeditingon=1&id=934",
                moodle_host=MOODLE_HOST,
            )
            == 934
        )

    def test_other_host_is_ignored(self) -> None:
        """AC-3"""
        assert (
            extract_linked_course_id(
                "https://cloud.itech-bs14.de/course/view.php?id=934", moodle_host=MOODLE_HOST
            )
            is None
        )

    def test_same_host_but_not_a_course_link_is_ignored(self) -> None:
        """AC-3"""
        assert (
            extract_linked_course_id(
                "https://moodle.itech-bs14.de/mod/page/view.php?id=934", moodle_host=MOODLE_HOST
            )
            is None
        )

    def test_missing_id_is_ignored(self) -> None:
        assert (
            extract_linked_course_id(
                "https://moodle.itech-bs14.de/course/view.php", moodle_host=MOODLE_HOST
            )
            is None
        )

    def test_non_numeric_id_is_ignored(self) -> None:
        assert (
            extract_linked_course_id(
                "https://moodle.itech-bs14.de/course/view.php?id=abc", moodle_host=MOODLE_HOST
            )
            is None
        )


class TestSelfEnrolment:
    async def test_no_self_enrolment_method_declines(self) -> None:
        """AC-4 / AC-5"""
        moodle = FakeMoodle(
            {
                "core_enrol_get_course_enrolment_methods": [
                    {"id": 1, "courseid": 934, "type": "manual", "name": "Manual", "status": True},
                ],
            }
        )
        assert await try_self_enrol(moodle, 934) is False  # type: ignore[arg-type]
        assert not any(c[0] == "enrol_self_enrol_user" for c in moodle.calls)

    async def test_disabled_self_enrolment_is_not_attempted(self) -> None:
        """AC-4: status must be true, not just type=='self'."""
        moodle = FakeMoodle(
            {
                "core_enrol_get_course_enrolment_methods": [
                    {"id": 1, "courseid": 934, "type": "self", "name": "Self", "status": False},
                ],
            }
        )
        assert await try_self_enrol(moodle, 934) is False  # type: ignore[arg-type]
        assert not any(c[0] == "enrol_self_enrol_user" for c in moodle.calls)

    async def test_key_required_is_never_attempted(self) -> None:
        """AC-6: the defining safety property — never guess a password."""
        moodle = FakeMoodle(
            {
                "core_enrol_get_course_enrolment_methods": [
                    {"id": 3012, "courseid": 934, "type": "self", "name": "Self", "status": True},
                ],
                "enrol_self_get_instance_info": {
                    "id": 3012,
                    "courseid": 934,
                    "type": "self",
                    "name": "Self",
                    "status": True,
                    "enrolpassword": "geheim",
                },
            }
        )
        assert await try_self_enrol(moodle, 934) is False  # type: ignore[arg-type]
        assert not any(c[0] == "enrol_self_enrol_user" for c in moodle.calls)

    async def test_no_key_required_enrols(self) -> None:
        """AC-6 / AC-7: the actual live-verified shape for course 934."""
        moodle = FakeMoodle(
            {
                "core_enrol_get_course_enrolment_methods": [
                    {"id": 3012, "courseid": 934, "type": "self", "name": "Self", "status": True},
                ],
                "enrol_self_get_instance_info": {
                    "id": 3012,
                    "courseid": 934,
                    "type": "self",
                    "name": "Self",
                    "status": True,
                },
                "enrol_self_enrol_user": {"status": True, "warnings": []},
            }
        )
        assert await try_self_enrol(moodle, 934) is True  # type: ignore[arg-type]
        call = next(c for c in moodle.calls if c[0] == "enrol_self_enrol_user")
        assert call[1]["courseid"] == 934
        assert call[1]["instanceid"] == 3012
        assert call[1]["password"] == ""

    async def test_declined_enrolment_is_not_an_error(self) -> None:
        """AC-7: a full course or closed window is a decline, not a crash."""
        moodle = FakeMoodle(
            {
                "core_enrol_get_course_enrolment_methods": [
                    {"id": 3012, "courseid": 934, "type": "self", "name": "Self", "status": True},
                ],
                "enrol_self_get_instance_info": {
                    "id": 3012,
                    "courseid": 934,
                    "type": "self",
                    "name": "Self",
                    "status": True,
                },
                "enrol_self_enrol_user": {
                    "status": False,
                    "warnings": [
                        {
                            "item": "instance",
                            "itemid": 3012,
                            "warningcode": "1",
                            "message": "Enrolment not permitted",
                        }
                    ],
                },
            }
        )
        assert await try_self_enrol(moodle, 934) is False  # type: ignore[arg-type]

    async def test_api_failure_declines_without_raising(self) -> None:
        """AC-8"""
        moodle = FakeMoodle(fail={"core_enrol_get_course_enrolment_methods"})
        assert await try_self_enrol(moodle, 934) is False  # type: ignore[arg-type]

    async def test_instance_info_failure_tries_the_next_instance(self) -> None:
        """AC-8: one broken instance must not abandon other candidates."""

        class PartiallyBroken(FakeMoodle):
            async def call(self, wsfunction: str, **params: Any) -> Any:
                if wsfunction == "enrol_self_get_instance_info" and params["instanceid"] == 1:
                    raise RuntimeError("broken instance")
                return await super().call(wsfunction, **params)

        moodle = PartiallyBroken(
            {
                "core_enrol_get_course_enrolment_methods": [
                    {"id": 1, "courseid": 934, "type": "self", "name": "A", "status": True},
                    {"id": 2, "courseid": 934, "type": "self", "name": "B", "status": True},
                ],
                "enrol_self_get_instance_info": {
                    "id": 2,
                    "courseid": 934,
                    "type": "self",
                    "name": "B",
                    "status": True,
                },
                "enrol_self_enrol_user": {"status": True, "warnings": []},
            }
        )
        assert await try_self_enrol(moodle, 934) is True  # type: ignore[arg-type]

    async def test_every_outcome_is_logged(self) -> None:
        """AC-10: never silent about a real enrolment action."""
        moodle = FakeMoodle(
            {
                "core_enrol_get_course_enrolment_methods": [
                    {"id": 3012, "courseid": 934, "type": "self", "name": "Self", "status": True},
                ],
                "enrol_self_get_instance_info": {
                    "id": 3012,
                    "courseid": 934,
                    "type": "self",
                    "name": "Self",
                    "status": True,
                },
                "enrol_self_enrol_user": {"status": True, "warnings": []},
            }
        )
        with structlog.testing.capture_logs() as logs:
            await try_self_enrol(moodle, 934)  # type: ignore[arg-type]
        assert any(e.get("event") == "enrol.success" for e in logs)


class TestAwaitEnrolment:
    """AC-11: enrolment doesn't appear in core_enrol_get_users_courses immediately.

    Verified against the live API — five real self-enrolments on 2026-08-21 all
    showed this delay. Giving up after one check would make the whole feature a
    silent no-op: every enrolment would succeed and then be dropped.
    """

    async def test_retries_until_the_course_appears(self) -> None:
        from bsbot.cron.moodle.enrolment import await_enrolled_course

        calls = 0

        class FlakyMoodle:
            async def call(self, wsfunction: str, **params: Any) -> Any:
                nonlocal calls
                calls += 1
                if calls < 3:
                    return [{"id": 100, "fullname": "Other"}]
                return [{"id": 100, "fullname": "Other"}, {"id": 934, "fullname": "Neu"}]

        slept: list[float] = []
        course = await await_enrolled_course(
            FlakyMoodle(),
            userid=1,
            courseid=934,
            attempts=5,
            delay_s=1.0,
            sleep=slept.append,  # type: ignore[arg-type]
        )
        assert course == {"id": 934, "fullname": "Neu"}
        assert len(slept) == 2

    async def test_gives_up_after_the_attempt_budget(self) -> None:
        from bsbot.cron.moodle.enrolment import await_enrolled_course

        class NeverMoodle:
            async def call(self, wsfunction: str, **params: Any) -> Any:
                return [{"id": 100, "fullname": "Other"}]

        course = await await_enrolled_course(
            NeverMoodle(),
            userid=1,
            courseid=934,
            attempts=3,
            delay_s=0.0,
            sleep=lambda _: None,  # type: ignore[arg-type]
        )
        assert course is None

    async def test_first_attempt_success_needs_no_sleep(self) -> None:
        from bsbot.cron.moodle.enrolment import await_enrolled_course

        class ImmediateMoodle:
            async def call(self, wsfunction: str, **params: Any) -> Any:
                return [{"id": 934, "fullname": "Sofort da"}]

        slept: list[float] = []
        course = await await_enrolled_course(
            ImmediateMoodle(),
            userid=1,
            courseid=934,
            attempts=5,
            delay_s=1.0,
            sleep=slept.append,  # type: ignore[arg-type]
        )
        assert course == {"id": 934, "fullname": "Sofort da"}
        assert slept == []


class TestLinkedModuleExtraction:
    """Spec 011 AC-14: a direct mod/*/view.php link, not a whole-course link."""

    def test_recognises_a_page_module_link(self) -> None:
        assert (
            extract_linked_module_cmid(
                "https://moodle.itech-bs14.de/mod/page/view.php?id=64055&inpopup=1",
                moodle_host=MOODLE_HOST,
            )
            == 64055
        )

    def test_recognises_a_resource_module_link(self) -> None:
        assert (
            extract_linked_module_cmid(
                "https://moodle.itech-bs14.de/mod/resource/view.php?id=123",
                moodle_host=MOODLE_HOST,
            )
            == 123
        )

    def test_other_host_is_ignored(self) -> None:
        assert (
            extract_linked_module_cmid(
                "https://elsewhere.de/mod/page/view.php?id=1", moodle_host=MOODLE_HOST
            )
            is None
        )

    def test_course_link_is_not_a_module_link(self) -> None:
        """Must not double-match what extract_linked_course_id already handles."""
        assert (
            extract_linked_module_cmid(
                "https://moodle.itech-bs14.de/course/view.php?id=934", moodle_host=MOODLE_HOST
            )
            is None
        )

    def test_missing_id_is_ignored(self) -> None:
        assert (
            extract_linked_module_cmid(
                "https://moodle.itech-bs14.de/mod/page/view.php", moodle_host=MOODLE_HOST
            )
            is None
        )


class TestResolveModuleCourse:
    async def test_resolves_the_owning_course(self) -> None:
        """AC-14: verified live against core_course_get_course_module."""
        moodle = FakeMoodle(
            {
                "core_course_get_course_module": {
                    "cm": {"id": 64055, "course": 878, "modname": "page", "instance": 7190},
                    "warnings": [],
                },
            }
        )
        assert await resolve_module_course(moodle, 64055) == 878  # type: ignore[arg-type]

    async def test_failure_is_a_clean_skip(self) -> None:
        """AC-16: a deleted or inaccessible module must not crash the crawl."""
        moodle = FakeMoodle(fail={"core_course_get_course_module"})
        assert await resolve_module_course(moodle, 999) is None  # type: ignore[arg-type]
