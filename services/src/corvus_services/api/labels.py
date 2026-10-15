"""Labels: validation and CSV import."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .pseudonym import parse_pseudonym

LabelValue = Literal["legit", "cheat"]
LabelSource = Literal["honeypot", "staff_verdict", "ban_import", "weak_rule"]
SubjectType = Literal["pseudonym", "session"]

CSV_COLUMNS = (
    "subject_type",
    "subject",
    "label",
    "source",
    "confidence",
    "valid_from",
    "valid_to",
    "note",
)
_REQUIRED = ("subject_type", "subject", "label", "source", "confidence")


class LabelIn(BaseModel):
    """``subject`` is the pseudonym (64 hex chars) or the session id (32 hex chars)."""

    model_config = ConfigDict(extra="forbid")

    subject_type: SubjectType
    subject: str
    label: LabelValue
    source: LabelSource
    confidence: float = Field(ge=0.0, le=1.0)
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    note: str = Field(default="", max_length=2000)

    @field_validator("valid_from", "valid_to")
    @classmethod
    def _aware(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("timestamps must carry a UTC offset")
        return v

    @model_validator(mode="after")
    def _consistent(self) -> LabelIn:
        if self.valid_from and self.valid_to and self.valid_from >= self.valid_to:
            raise ValueError("valid_from must be before valid_to")
        try:
            if self.subject_type == "pseudonym":
                parse_pseudonym(self.subject)
            else:
                if len(self.subject) != 32:
                    raise ValueError("session id must be 32 hex characters")
                bytes.fromhex(self.subject)
        except ValueError as e:
            raise ValueError(str(e)) from None
        return self

    def subject_bytes(self) -> bytes:
        return bytes.fromhex(self.subject)


@dataclass(frozen=True)
class RowError:
    row: int
    message: str


class CsvError(Exception):
    def __init__(self, message: str, errors: list[RowError] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or []


def parse_csv(text: str, max_rows: int) -> list[LabelIn]:
    """Parses and validates all rows; any invalid row rejects the whole file (nothing is imported)."""
    reader = csv.DictReader(io.StringIO(text, newline=""))
    header = reader.fieldnames or []
    missing = [c for c in _REQUIRED if c not in header]
    unknown = [c for c in header if c not in CSV_COLUMNS]
    if missing or unknown:
        raise CsvError(
            "bad header"
            + (f", missing: {', '.join(missing)}" if missing else "")
            + (f", unknown: {', '.join(unknown)}" if unknown else "")
        )
    rows: list[LabelIn] = []
    errors: list[RowError] = []
    for index, raw in enumerate(reader, start=2):  # line 1 is the header
        if len(rows) + len(errors) >= max_rows:
            raise CsvError(f"too many rows (limit {max_rows})")
        data = {k: v for k, v in raw.items() if k is not None and v not in (None, "")}
        try:
            rows.append(LabelIn.model_validate(data))
        except ValidationError as e:
            first = e.errors()[0]
            loc = ".".join(str(p) for p in first["loc"]) or "row"
            # No input echo: notes and subjects can be sensitive.
            errors.append(RowError(index, f"{loc}: {first['msg']}"))
    if errors:
        raise CsvError("invalid rows", errors)
    if not rows:
        raise CsvError("file contains no rows")
    return rows
