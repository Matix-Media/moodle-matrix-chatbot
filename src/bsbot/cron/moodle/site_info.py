"""Parsed ``core_webservice_get_site_info`` payload and capability lookup."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class SiteInfo(BaseModel):
    """Identity of the authenticated user plus the site's exposed function list."""

    model_config = {"frozen": True}

    sitename: str = ""
    username: str = ""
    userid: int = 0
    release: str = ""
    version: str = ""
    lang: str = ""
    functions: frozenset[str] = frozenset()
    #: True when the site did not report a function list. We then assume every
    #: function is available and discover unavailability per call (spec 002 AC-14),
    #: because failing hard here would break crawling on perfectly usable sites.
    optimistic: bool = False

    @classmethod
    def from_payload(cls, payload: Any) -> SiteInfo:
        if not isinstance(payload, dict):
            raise TypeError(f"site info payload must be an object, got {type(payload).__name__}")

        raw_functions = payload.get("functions")
        names: set[str] = set()
        if isinstance(raw_functions, list) and raw_functions:
            optimistic = False
            for entry in raw_functions:
                if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                    names.add(entry["name"])
                elif isinstance(entry, str):
                    names.add(entry)
        else:
            optimistic = True

        return cls(
            sitename=str(payload.get("sitename", "")),
            username=str(payload.get("username", "")),
            userid=int(payload.get("userid", 0) or 0),
            release=str(payload.get("release", "")),
            version=str(payload.get("version", "")),
            lang=str(payload.get("lang", "")),
            functions=frozenset(names),
            optimistic=optimistic,
        )

    def has(self, wsfunction: str) -> bool:
        """Whether this site exposes ``wsfunction``."""
        if self.optimistic:
            return True
        return wsfunction in self.functions
