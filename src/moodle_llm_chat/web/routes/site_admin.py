"""Site-authenticated admin routes for the plugin: quota policy push and usage report."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Request

from moodle_llm_chat.quota.service import QuotaPolicy, QuotaService
from moodle_llm_chat.web.auth import SiteDep

router = APIRouter(prefix="/ingest", tags=["site"])


def _quota(request: Request) -> QuotaService:
    quota: QuotaService = request.app.state.quota
    return quota


@router.put("/policy")
async def put_policy(policy: QuotaPolicy, request: Request, site: SiteDep) -> dict[str, Any]:
    """The plugin pushes its quota settings whenever they are saved (and on full syncs)."""
    _quota(request).set_policy(site.site_id, policy)
    return {"policy": policy.model_dump(), "ceilings": _quota(request).ceilings(site.site_id)}


@router.get("/usage")
async def usage(
    request: Request,
    site: SiteDep,
    period: Literal["day", "month"] = "day",
    by: Literal["user", "site"] = "user",
) -> dict[str, Any]:
    rows = _quota(request).report(site.site_id, period=period, by=by)
    return {"period": period, "by": by, "rows": rows}
