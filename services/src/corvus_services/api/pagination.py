"""Keyset pagination: ``limit`` plus an opaque ``cursor`` over (created_at, id), newest first."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from .problems import ProblemError


@dataclass(frozen=True)
class Cursor:
    created_at: datetime
    id: str

    def encode(self) -> str:
        raw = json.dumps([self.created_at.isoformat(), self.id], separators=(",", ":"))
        return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")

    @classmethod
    def decode(cls, token: str) -> Cursor:
        try:
            padded = token + "=" * (-len(token) % 4)
            created, ident = json.loads(base64.urlsafe_b64decode(padded.encode()))
            return cls(datetime.fromisoformat(created), str(ident))
        except (ValueError, TypeError, json.JSONDecodeError) as e:
            raise ProblemError(400, "invalid cursor", slug="invalid-cursor") from e


@dataclass(frozen=True)
class PageRequest:
    limit: int
    cursor: Cursor | None


@dataclass(frozen=True)
class Page[T]:
    items: list[T]
    next_cursor: str | None


def paginate[T](
    rows: Sequence[T], req: PageRequest, key: Callable[[T], tuple[datetime, str]]
) -> Page[T]:
    """In-memory keyset pagination (used by fakes; the Postgres adapter does it in SQL)."""
    ordered = sorted(rows, key=key, reverse=True)
    if req.cursor is not None:
        bound = (req.cursor.created_at, req.cursor.id)
        ordered = [r for r in ordered if key(r) < bound]
    return page_of(ordered[: req.limit + 1], req.limit, key)


def page_of[T](rows: list[T], limit: int, key: Callable[[T], tuple[datetime, str]]) -> Page[T]:
    """``rows`` holds up to ``limit + 1`` rows; the extra row signals a next page."""
    items = rows[:limit]
    if len(rows) > limit:
        created_at, ident = key(items[-1])
        return Page(items, Cursor(created_at, ident).encode())
    return Page(items, None)
