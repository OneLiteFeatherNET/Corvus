"""Labels: create/list (staff write, viewer read) and bulk CSV import (data_scientist)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request

from ..auth import READERS, Principal, Role, require
from ..deps import Deps, get_deps, get_page
from ..labels import CsvError, LabelIn, parse_csv
from ..pagination import PageRequest
from ..ports import LabelRecord
from ..problems import ProblemError
from ..pseudonym import parse_pseudonym
from ..schemas import LabelImportOut, LabelOut, Paged

router = APIRouter(tags=["labels"])

_MAX_REPORTED_ERRORS = 50


def _record(label: LabelIn, who: str, deps: Deps) -> LabelRecord:
    return LabelRecord(
        id=deps.new_id(),
        subject_type=label.subject_type,
        subject=label.subject.lower(),
        label=label.label,
        source=label.source,
        confidence=label.confidence,
        valid_from=label.valid_from,
        valid_to=label.valid_to,
        note=label.note,
        created_by=who,
        created_at=deps.clock(),
    )


@router.post("/labels", status_code=201, response_model=LabelOut, summary="Create a label (staff)")
async def create_label(
    body: LabelIn,
    who: Principal = Depends(require(Role.STAFF)),
    deps: Deps = Depends(get_deps),
) -> LabelOut:
    record = _record(body, who.subject, deps)
    await deps.repos.labels.add_many([record])
    return LabelOut.of(record)


@router.get("/labels", response_model=Paged[LabelOut], summary="List labels, newest first")
async def list_labels(
    subject_type: Literal["pseudonym", "session"] | None = Query(default=None),
    subject: str | None = Query(
        default=None, description="Pseudonym (64 hex) or session id (32 hex); needs `subject_type`"
    ),
    _: Principal = Depends(require(*READERS)),
    page: PageRequest = Depends(get_page),
    deps: Deps = Depends(get_deps),
) -> Paged[LabelOut]:
    key: bytes | None = None
    if subject is not None:
        if subject_type is None:
            raise ProblemError(422, "subject requires subject_type", slug="validation")
        try:
            key = (
                parse_pseudonym(subject) if subject_type == "pseudonym" else bytes.fromhex(subject)
            )
        except ValueError as e:
            raise ProblemError(422, "subject is not valid hex", slug="validation") from e
        if subject_type == "session" and len(key) != 16:
            raise ProblemError(422, "session id must be 32 hex characters", slug="validation")
    result = await deps.repos.labels.list_page(page, subject_type, key)
    return Paged[LabelOut](
        items=[LabelOut.of(r) for r in result.items], next_cursor=result.next_cursor
    )


@router.post(
    "/labels/import",
    status_code=201,
    response_model=LabelImportOut,
    summary="Bulk import labels from CSV (data_scientist); all rows or none",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "text/csv": {
                    "schema": {"type": "string"},
                    "example": "subject_type,subject,label,source,confidence,valid_from,valid_to,note",
                }
            },
        }
    },
)
async def import_labels(
    request: Request,
    who: Principal = Depends(require(Role.DATA_SCIENTIST)),
    deps: Deps = Depends(get_deps),
) -> LabelImportOut:
    limit = deps.settings.label_import_max_bytes
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise ProblemError(413, f"CSV body exceeds {limit} bytes", slug="too-large")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise ProblemError(413, f"CSV body exceeds {limit} bytes", slug="too-large")
        chunks.append(chunk)
    try:
        text = b"".join(chunks).decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise ProblemError(422, "CSV must be UTF-8", slug="validation") from e
    try:
        rows = parse_csv(text, deps.settings.label_import_max_rows)
    except CsvError as e:
        raise ProblemError(
            422,
            str(e),
            slug="validation",
            errors=[{"row": r.row, "message": r.message} for r in e.errors[:_MAX_REPORTED_ERRORS]],
            error_count=len(e.errors),
        ) from e
    await deps.repos.labels.add_many([_record(r, who.subject, deps) for r in rows])
    return LabelImportOut(imported=len(rows))
