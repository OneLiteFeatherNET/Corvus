"""Tiny asyncio HTTP server: ``/metrics`` (Prometheus) and ``/healthz`` (readiness)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest

log = logging.getLogger("corvus.health")

Check = Callable[[], Awaitable[None]]


async def run_checks(checks: dict[str, Check], timeout: float = 3.0) -> dict[str, str]:
    """Run all checks; returns ``{name: "ok" | error}``."""

    async def one(name: str, check: Check) -> tuple[str, str]:
        try:
            await asyncio.wait_for(check(), timeout)
        except Exception as e:
            return name, f"{type(e).__name__}: {e}"
        return name, "ok"

    results = await asyncio.gather(*(one(n, c) for n, c in checks.items()))
    return dict(results)


class HealthServer:
    def __init__(self, registry: CollectorRegistry, checks: dict[str, Check], port: int) -> None:
        self._registry = registry
        self._checks = checks
        self._port = port
        self._server: asyncio.Server | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "0.0.0.0", self._port)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def respond(self, path: str) -> tuple[int, str, bytes]:
        if path == "/metrics":
            return 200, CONTENT_TYPE_LATEST, generate_latest(self._registry)
        if path == "/healthz":
            results = await run_checks(self._checks)
            ok = all(v == "ok" for v in results.values())
            body = "\n".join(f"{k}: {v}" for k, v in results.items()) + "\n"
            return (200 if ok else 503), "text/plain", body.encode()
        return 404, "text/plain", b"not found\n"

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await asyncio.wait_for(reader.readline(), 5)
            parts = line.decode("latin-1").split()
            path = parts[1].split("?")[0] if len(parts) >= 2 else "/"
            status, ctype, body = await self.respond(path)
            head = (
                f"HTTP/1.1 {status} X\r\nContent-Type: {ctype}\r\n"
                f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
            )
            writer.write(head.encode() + body)
            await writer.drain()
        except Exception:
            log.debug("health request failed", exc_info=True)
        finally:
            writer.close()
