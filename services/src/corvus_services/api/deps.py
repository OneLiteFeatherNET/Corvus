"""Application dependencies: one container on ``app.state.deps``, injectable in tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from fastapi import Depends, Query, Request
from prometheus_client import CollectorRegistry

from ..clock import Clock, system_clock
from .auth import Authenticator
from .jobs import JobRunner
from .metrics import ApiMetrics
from .pagination import Cursor, PageRequest
from .ports import Repos
from .pseudonym import Pseudonymizer
from .settings import ApiSettings


@dataclass
class Deps:
    settings: ApiSettings
    repos: Repos
    authenticator: Authenticator
    runner: JobRunner
    pseudonymizer: Pseudonymizer
    plugin_tokens: list[str] = field(default_factory=list)
    clock: Clock = system_clock
    new_id: Callable[[], UUID] = uuid4
    metrics: ApiMetrics = field(default_factory=lambda: ApiMetrics(CollectorRegistry()))


def get_deps(request: Request) -> Deps:
    deps: Deps = request.app.state.deps
    return deps


def get_page(
    deps: Deps = Depends(get_deps),
    limit: int | None = Query(default=None, ge=1, description="Page size (server maximum applies)"),
    cursor: str | None = Query(default=None, description="Opaque cursor from `next_cursor`"),
) -> PageRequest:
    s = deps.settings
    size = min(limit if limit is not None else s.default_page_size, s.max_page_size)
    return PageRequest(size, Cursor.decode(cursor) if cursor else None)
