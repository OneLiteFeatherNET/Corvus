"""The control-plane API as a job sees it: a small port plus a stdlib HTTP adapter.

Jobs authenticate with ``Authorization: Bearer $CORVUS_API_TOKEN`` and identify themselves with
``X-Corvus-Job-Id``. Tests use a fake implementation of :class:`JobApi`.

Assumed shape of the snapshot endpoints (see docs/reference/ml-jobs.md):

* ``GET /api/v1/snapshots/{id}`` returns the snapshot with its ``manifest``
  (``files.events[].key`` are object keys in the lake bucket);
* ``GET /api/v1/snapshots/{id}/files`` returns ``{"items": [...], "raw": [...]}`` (entries with
  ``key``); ``raw`` is absent on older APIs;
* ``GET /api/v1/snapshots/{id}/sessions?exclude_erased=true`` returns a page
  ``{"items": [...], "next_cursor": ...}`` of sessions, each with ``session_id`` (hex),
  ``pseudonym`` (hex), ``started_at``, ``client_protocol``, ``segments``
  (``[{"segment", "game_context"}]``) and
  optionally ``labels`` (``[{"label", "source", "confidence", "valid_from", "valid_to"}]``).
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from typing import Any, Protocol

from corvus_ml.jobs.context import ApiSettings

log = logging.getLogger(__name__)

JOB_STATUSES = ("running", "succeeded", "failed")


class ApiError(RuntimeError):
    """An API call failed. The text never contains request headers or the token."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class ArtifactInvalidated(ApiError):  # noqa: N818 - a domain event rather than a failure
    """``409``: an erasure invalidated the snapshot's raw-derived artefacts after the job began."""

    def __init__(self, message: str) -> None:
        super().__init__(message, 409)


class JobApi(Protocol):
    def update_job(
        self,
        job_id: str,
        status: str,
        message: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        """``PATCH /jobs/{id}``: status ``running|succeeded|failed``, message, result JSON."""

    def get_snapshot(self, snapshot_id: str) -> dict[str, Any]: ...

    def get_snapshot_files(self, snapshot_id: str) -> dict[str, Any]:
        """``GET /snapshots/{id}/files``: ``items`` (events) and, on current APIs, ``raw``."""

    def iter_snapshot_sessions(self, snapshot_id: str) -> Iterator[dict[str, Any]]:
        """Sessions of the snapshot, erased players excluded."""

    def register_artifact(self, job_id: str, kind: str, s3_prefix: str, contains_raw: bool) -> None:
        """``POST /jobs/{job_id}/artifacts`` (201 new, 200 repeated).

        Raises :class:`ArtifactInvalidated` on ``409``.
        """

    def register_model(self, payload: dict[str, Any]) -> dict[str, Any]:
        """``POST /models`` (stage ``registered``)."""


class HttpApiClient:
    """:class:`JobApi` over HTTP using only the standard library."""

    def __init__(
        self,
        settings: ApiSettings,
        job_id: str,
        *,
        timeout: float = 30.0,
        retries: int = 3,
        page_size: int = 200,
        opener: Callable[..., Any] = urllib.request.urlopen,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._base = f"{settings.url}/api/v1"
        self._token = settings.token
        self._job_id = job_id
        self._timeout = timeout
        self._retries = max(1, retries)
        self._page_size = page_size
        self._open = opener
        self._sleep = sleep

    # --- port --------------------------------------------------------------------------------

    def update_job(
        self,
        job_id: str,
        status: str,
        message: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        if status not in JOB_STATUSES:
            raise ValueError(f"status must be one of {JOB_STATUSES}")
        body: dict[str, Any] = {"status": status}
        if message is not None:
            body["message"] = message
        if result is not None:
            body["result"] = result
        self._call("PATCH", f"/jobs/{job_id}", body=body, retry=True)

    def get_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        return self._call("GET", f"/snapshots/{snapshot_id}", retry=True)

    def get_snapshot_files(self, snapshot_id: str) -> dict[str, Any]:
        return self._call("GET", f"/snapshots/{snapshot_id}/files", retry=True)

    def register_artifact(self, job_id: str, kind: str, s3_prefix: str, contains_raw: bool) -> None:
        body = {"kind": kind, "s3_prefix": s3_prefix, "contains_raw": contains_raw}
        try:  # idempotent per (snapshot, prefix), so a retry is safe
            self._call("POST", f"/jobs/{job_id}/artifacts", body=body, retry=True)
        except ApiError as exc:
            if exc.status == 409:
                raise ArtifactInvalidated(str(exc)) from None
            raise

    def iter_snapshot_sessions(self, snapshot_id: str) -> Iterator[dict[str, Any]]:
        cursor: str | None = None
        while True:
            query = {"exclude_erased": "true", "limit": str(self._page_size)}
            if cursor:
                query["cursor"] = cursor
            page = self._call("GET", f"/snapshots/{snapshot_id}/sessions", query=query, retry=True)
            yield from page.get("items", [])
            cursor = page.get("next_cursor")
            if not cursor:
                return

    def register_model(self, payload: dict[str, Any]) -> dict[str, Any]:
        # Not retried: a repeated POST after a lost response would be a 409 duplicate.
        return self._call("POST", "/models", body=payload, retry=False)

    # --- transport ---------------------------------------------------------------------------

    def _call(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
        retry: bool,
    ) -> dict[str, Any]:
        url = self._base + path + (f"?{urllib.parse.urlencode(query)}" if query else "")
        data = None if body is None else json.dumps(body).encode()
        headers = {
            "Authorization": f"Bearer {self._token}",
            "X-Corvus-Job-Id": self._job_id,
            "Accept": "application/json",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        attempts = self._retries if retry else 1
        for attempt in range(1, attempts + 1):
            request = urllib.request.Request(url, data=data, method=method, headers=headers)
            try:
                with self._open(request, timeout=self._timeout) as response:
                    raw = response.read()
                return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                transient = exc.code in (429, 500, 502, 503, 504)
                if not transient or attempt == attempts:
                    raise ApiError(
                        f"{method} {path} failed with HTTP {exc.code}{_problem(exc)}", exc.code
                    ) from None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                if attempt == attempts:
                    raise ApiError(f"{method} {path} failed: {type(exc).__name__}") from None
            except json.JSONDecodeError:
                raise ApiError(f"{method} {path} returned invalid JSON") from None
            log.warning("%s %s failed, retrying (%d/%d)", method, path, attempt, attempts)
            self._sleep(min(2.0 ** (attempt - 1), 10.0))
        raise AssertionError("unreachable")


def _problem(exc: urllib.error.HTTPError) -> str:
    """The ``title`` / ``detail`` of an RFC 9457 problem response, if there is one."""
    try:
        problem = json.loads(exc.read())
        text = " - ".join(str(problem[k]) for k in ("title", "detail") if problem.get(k))
    except (ValueError, OSError, AttributeError, TypeError):
        return ""
    return f": {text[:300]}" if text else ""
