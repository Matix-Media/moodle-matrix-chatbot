"""The access scope of one chat turn, built from a verified chat token.

The Moodle plugin computes, as the user, which courses and course modules they can
currently see (``get_fast_modinfo(...)->uservisible``) and signs that into the token.
Every store query a tool makes is filtered by this scope; before any document body
is returned, the API additionally re-checks with Moodle (see ``access.moodle``).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class Capability(StrEnum):
    #: Read course content through the retrieval and navigation tools.
    READ = "read"
    #: Be asked questions back by the agent (interactive tools).
    INTERACT = "interact"
    #: Reserved for future tools that change something in Moodle.
    WRITE = "write"


class QuotaClaim(BaseModel):
    """The user's effective token limits, computed by the plugin."""

    day: int | None = None
    month: int | None = None
    exempt: bool = False


class UserProfile(BaseModel):
    """Who is asking — only what an answer needs, never contact data or ids."""

    user_id: int
    fullname: str
    lang: str = "de"
    #: course_id → role archetypes (``student``, ``editingteacher``, …)
    roles: dict[int, list[str]] = Field(default_factory=dict)
    #: course_id → group names, filled lazily from the plugin
    groups: dict[int, list[str]] = Field(default_factory=dict)
    #: The course page the chat was opened from, if any.
    course_context: int | None = None


class AccessScope(BaseModel):
    model_config = {"frozen": True}

    site_id: str
    user_id: int
    course_ids: frozenset[int]
    cmids: frozenset[int]
    caps: frozenset[Capability]

    def can(self, cap: Capability) -> bool:
        return cap in self.caps
