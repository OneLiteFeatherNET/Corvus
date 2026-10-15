"""Model registry (data_scientist write, viewer read)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from ..auth import READERS, Principal, Role, require
from ..deps import Deps, get_deps, get_page
from ..pagination import PageRequest
from ..ports import DuplicateError, ModelFilter, ModelRecord, StaleError
from ..problems import ProblemError
from ..registry import (
    ModelNotFoundError,
    Stage,
    TransitionError,
    transition_model,
)
from ..schemas import ModelOut, Paged, TransitionOut

router = APIRouter(tags=["models"])
_DS = Depends(require(Role.DATA_SCIENTIST))
_DS_OR_JOB = Depends(require(Role.DATA_SCIENTIST, allow_jobs=True))
_READ = Depends(require(*READERS))
_READ_OR_JOB = Depends(require(*READERS, allow_jobs=True))


class ModelIn(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    name: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=100)
    candidate_type: str = Field(min_length=1, max_length=100)
    game_context: str = Field(min_length=1, max_length=200)
    domain: str = Field(min_length=1, max_length=200)
    snapshot_id: UUID
    metrics: dict[str, Any] = Field(default_factory=dict)
    onnx_key: str = Field(min_length=1, max_length=1024, description="Object key of the ONNX file")


class StageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: Stage
    reason: str = Field(default="", max_length=500)


@router.post("/models", status_code=201, response_model=ModelOut, summary="Register a model")
async def register_model(
    body: ModelIn, who: Principal = _DS_OR_JOB, deps: Deps = Depends(get_deps)
) -> ModelOut:
    if await deps.repos.snapshots.get(body.snapshot_id) is None:
        raise ProblemError(422, "snapshot does not exist", slug="validation")
    model = ModelRecord(
        id=deps.new_id(),
        name=body.name,
        version=body.version,
        candidate_type=body.candidate_type,
        game_context=body.game_context,
        domain=body.domain,
        snapshot_id=body.snapshot_id,
        metrics=body.metrics,
        onnx_key=body.onnx_key,
        stage=Stage.REGISTERED.value,
        created_by=who.subject,
        created_at=deps.clock(),
    )
    try:
        await deps.repos.models.create(model)
    except DuplicateError as e:
        raise ProblemError(409, "this model name and version already exist", slug="conflict") from e
    return ModelOut.of(model)


@router.get("/models", response_model=Paged[ModelOut], summary="List models, newest first")
async def list_models(
    name: str | None = Query(default=None, max_length=200),
    version: str | None = Query(default=None, max_length=100),
    game_context: str | None = Query(default=None, max_length=200),
    domain: str | None = Query(default=None, max_length=200),
    stage: Stage | None = Query(default=None),
    _: Principal = _READ_OR_JOB,
    page: PageRequest = Depends(get_page),
    deps: Deps = Depends(get_deps),
) -> Paged[ModelOut]:
    flt = ModelFilter(name, version, game_context, domain, stage.value if stage else None)
    result = await deps.repos.models.list_page(page, flt)
    return Paged[ModelOut](
        items=[ModelOut.of(m) for m in result.items], next_cursor=result.next_cursor
    )


@router.get(
    "/models/{model_id}",
    response_model=ModelOut,
    responses={404: {"description": "Model not found"}},
    summary="Get a model",
)
async def get_model(
    model_id: UUID, _: Principal = _READ_OR_JOB, deps: Deps = Depends(get_deps)
) -> ModelOut:
    model = await deps.repos.models.get(model_id)
    if model is None:
        raise ProblemError(404, "model not found", slug="not-found")
    return ModelOut.of(model)


@router.get(
    "/models/{model_id}/transitions",
    response_model=list[TransitionOut],
    responses={404: {"description": "Model not found"}},
    summary="Audit trail of stage transitions",
)
async def model_transitions(
    model_id: UUID, _: Principal = _READ, deps: Deps = Depends(get_deps)
) -> list[TransitionOut]:
    if await deps.repos.models.get(model_id) is None:
        raise ProblemError(404, "model not found", slug="not-found")
    return [TransitionOut.of(t) for t in await deps.repos.models.transitions(model_id)]


@router.get(
    "/models/{name}/{version}",
    response_model=ModelOut,
    responses={404: {"description": "Model not found"}},
    summary="Get a model by name and version",
)
async def get_model_by_version(
    name: str, version: str, _: Principal = _READ_OR_JOB, deps: Deps = Depends(get_deps)
) -> ModelOut:
    model = await deps.repos.models.get_by_name_version(name, version)
    if model is None:
        raise ProblemError(404, "model not found", slug="not-found")
    return ModelOut.of(model)


@router.post(
    "/models/{model_id}/stage",
    response_model=ModelOut,
    responses={
        404: {"description": "Model not found"},
        409: {"description": "Transition not allowed or quality gate failed (see `reasons`)"},
    },
    summary="Move a model to another stage",
    description=(
        "Allowed: registered -> shadow -> canary -> enforce, and any stage -> archived. Promotion to "
        "`enforce` requires `p99_latency_ms` below the configured limit and `recall_at_fpr_0_1pct` at "
        "least that of the model currently enforced on the same snapshot and game context; that model is "
        "archived in the same transaction. Every transition is audited."
    ),
)
async def set_stage(
    model_id: UUID, body: StageIn, who: Principal = _DS, deps: Deps = Depends(get_deps)
) -> ModelOut:
    try:
        model = await transition_model(
            deps.repos.models,
            model_id,
            body.stage,
            actor=who.subject,
            reason=body.reason,
            now=deps.clock(),
            max_p99_latency_ms=deps.settings.gate_max_p99_latency_ms,
            new_id=deps.new_id,
        )
    except ModelNotFoundError as e:
        raise ProblemError(404, "model not found", slug="not-found") from e
    except TransitionError as e:
        deps.metrics.transitions.labels(body.stage.value, "rejected").inc()
        raise ProblemError(409, str(e), slug="transition-rejected", reasons=e.reasons) from e
    except StaleError as e:
        raise ProblemError(409, "the model changed concurrently, retry", slug="conflict") from e
    deps.metrics.transitions.labels(body.stage.value, "applied").inc()
    return ModelOut.of(model)
