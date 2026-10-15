"""Scores of players, written by the scoring service (roles: staff, data_scientist)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..auth import Principal, Role, require
from ..deps import Deps, get_deps
from ..problems import ProblemError
from ..pseudonym import parse_pseudonym
from ..schemas import Items, ScoreHistoryOut, ScoreLatestOut

router = APIRouter(tags=["scores"])
_READ = Depends(require(Role.STAFF, Role.DATA_SCIENTIST))
SHADOW_ROLES = frozenset({Role.DATA_SCIENTIST, Role.ADMIN})


def _sees_shadow(principal: Principal) -> bool:
    """Shadow models are not validated: only people who evaluate models may see their scores."""
    return bool(principal.roles & SHADOW_ROLES)


HISTORY_DEFAULT_LIMIT = 100
HISTORY_MAX_LIMIT = 1000


def _pseudonym_or_422(value: str) -> bytes:
    try:
        return parse_pseudonym(value)
    except ValueError as e:
        raise ProblemError(422, str(e), slug="validation") from e


@router.get(
    "/scores/players/{pseudonym}",
    response_model=Items[ScoreLatestOut],
    summary="Latest scores of a player",
    description=(
        "The latest score per game context, domain and model. `shadow` models (never published on "
        "the score stream, not validated) are returned to `data_scientist` and `admin` only. A "
        "player without scores yields an empty list. Scores are informational: Corvus never "
        "sanctions players."
    ),
)
async def latest_scores(
    pseudonym: str, principal: Principal = _READ, deps: Deps = Depends(get_deps)
) -> Items[ScoreLatestOut]:
    rows = await deps.repos.scores.latest(
        _pseudonym_or_422(pseudonym), include_shadow=_sees_shadow(principal)
    )
    return Items[ScoreLatestOut](items=[ScoreLatestOut.of(r) for r in rows])


@router.get(
    "/scores/players/{pseudonym}/history",
    response_model=Items[ScoreHistoryOut],
    summary="Score history of a player",
    description=(
        "Newest first; the history is bounded by the retention of `corvus-housekeeping` "
        "(`CORVUS_SCORE_HISTORY_RETENTION_DAYS`, default 14 days). `shadow` rows are returned to "
        "`data_scientist` and `admin` only."
    ),
)
async def score_history(
    pseudonym: str,
    game_context: str | None = Query(default=None, min_length=1, max_length=200),
    limit: int = Query(default=HISTORY_DEFAULT_LIMIT, ge=1, le=HISTORY_MAX_LIMIT),
    principal: Principal = _READ,
    deps: Deps = Depends(get_deps),
) -> Items[ScoreHistoryOut]:
    rows = await deps.repos.scores.history(
        _pseudonym_or_422(pseudonym), game_context, limit, include_shadow=_sees_shadow(principal)
    )
    return Items[ScoreHistoryOut](items=[ScoreHistoryOut.of(r) for r in rows])
