"""FastAPI application factory and the ``corvus-api`` entry point."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request, Response

from ..clock import system_clock
from .auth import build_authenticator
from .deps import Deps
from .jobs import build_runner
from .pg import open_repos
from .problems import install_problem_handlers
from .pseudonym import Pseudonymizer
from .routes import jobs, labels, models, players, pools, system
from .settings import ApiSettings

log = logging.getLogger("corvus.api")

API_PREFIX = "/api/v1"

_DESCRIPTION = """\
Control plane of Corvus: pool definitions, dataset snapshots, labels, players and erasure,
blocklist, model registry and training jobs. Corvus never sanctions players itself.

Errors are RFC 9457 problem details (`application/problem+json`). Authenticate with an OIDC bearer
token; roles are `admin`, `data_scientist`, `staff` and `viewer` (`admin` may do everything).
"""


async def _default_deps(settings: ApiSettings) -> Deps:
    clock = system_clock
    authenticator = build_authenticator(settings, clock)
    if not authenticator.configured:
        raise RuntimeError(
            "no authentication configured: set CORVUS_API_OIDC_ISSUER, CORVUS_API_OIDC_AUDIENCE and "
            "CORVUS_API_OIDC_JWKS_URL (or, for local development only, CORVUS_API_DEV_TOKEN)"
        )
    return Deps(
        settings=settings,
        repos=await open_repos(settings.pg_dsn, settings.pg_pool_max),
        authenticator=authenticator,
        runner=build_runner(settings),
        pseudonymizer=Pseudonymizer(settings.pseudonym_key_bytes(), settings.pseudonym_key_id),
        plugin_tokens=settings.plugin_token_list(),
        clock=clock,
    )


def create_app(settings: ApiSettings | None = None, deps: Deps | None = None) -> FastAPI:
    """``deps`` is injected by tests; production builds them from the settings on startup."""
    cfg = settings or (deps.settings if deps else ApiSettings())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = deps is None
        app.state.deps = deps if deps is not None else await _default_deps(cfg)
        if not app.state.deps.pseudonymizer.available:
            log.warning(
                "CORVUS_PSEUDONYM_KEY is not set: player lookup and UUID erasure are disabled"
            )
        try:
            yield
        finally:
            if owned:
                for close in app.state.deps.repos.closers:
                    await close()

    app = FastAPI(
        title="Corvus control plane",
        version="1",
        description=_DESCRIPTION,
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    if deps is not None:
        app.state.deps = deps
    install_problem_handlers(app)

    @app.middleware("http")
    async def observe(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            route = request.scope.get("route")
            template = getattr(route, "path", None) or "unmatched"
            if route is not None and request.url.path.startswith(API_PREFIX + "/"):
                template = API_PREFIX + template  # included routers report the path without prefix
            m = request.app.state.deps.metrics
            m.requests.labels(template, request.method, str(status)).inc()
            m.latency.labels(template, request.method).observe(time.perf_counter() - started)

    for router in (pools.router, labels.router, players.router, players.plugin_router,
                   models.router, jobs.router):  # fmt: skip
        app.include_router(router, prefix=API_PREFIX)
    app.include_router(system.router)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = ApiSettings()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_config=None)


if __name__ == "__main__":
    main()
