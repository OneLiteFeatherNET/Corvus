"""Identity and dashboard summary for the management UI."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import READERS, Principal, authenticated, require
from ..deps import Deps, get_deps
from ..schemas import MeOut, SummaryOut

router = APIRouter(tags=["me"])

RECENT = 10
"""How many recent jobs and erasure requests the summary lists."""


@router.get(
    "/me",
    response_model=MeOut,
    summary="The caller: subject, display name and roles",
    description="Any valid OIDC token or the dev token; machine job tokens are refused.",
)
async def me(who: Principal = Depends(authenticated)) -> MeOut:
    return MeOut(
        subject=who.subject,
        display_name=who.display_name,
        roles=sorted(r.value for r in who.roles),
    )


@router.get(
    "/summary",
    response_model=SummaryOut,
    summary="Counts and recent activity for the dashboard (viewer)",
    description=(
        "Pools, snapshots, jobs by status, models by stage, labels by label and pending erasures, "
        "plus the 10 newest jobs and erasure requests (pseudonym prefix only)."
    ),
)
async def summary(
    _: Principal = Depends(require(*READERS)), deps: Deps = Depends(get_deps)
) -> SummaryOut:
    return SummaryOut.of(await deps.repos.summary.summary(RECENT))
