"""Test doubles and builders for the job tests: a fake control-plane API and a local bucket."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from corvus_ml.jobs.context import JobContext, Secrets
from corvus_ml.jobs.lifecycle import JobEnv
from corvus_ml.jobs.storage import ObjectStore, local_store

JOB_ID = "11111111-1111-4111-8111-111111111111"
SNAPSHOT_ID = "22222222-2222-4222-8222-222222222222"
TOKEN = "s3cr3t-api-token-value"
S3_SECRET = "minio-secret-key-value"
FIXED_NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


class FakeApi:
    """Records what a job sends; serves a snapshot and its sessions from memory."""

    def __init__(
        self,
        snapshot: dict[str, Any] | None = None,
        sessions: list[dict[str, Any]] | None = None,
        *,
        honour_exclude_erased: bool = True,
    ) -> None:
        self.snapshot = snapshot or {}
        self.sessions = sessions or []
        self.honour_exclude_erased = honour_exclude_erased
        self.updates: list[dict[str, Any]] = []
        self.models: list[dict[str, Any]] = []
        self.session_requests = 0
        self.fail_on: dict[str, Exception] = {}

    def _maybe_fail(self, what: str) -> None:
        if what in self.fail_on:
            raise self.fail_on[what]

    def update_job(self, job_id, status, message=None, result=None) -> None:
        self._maybe_fail(f"update:{status}")
        self.updates.append(
            {"job_id": job_id, "status": status, "message": message, "result": result}
        )

    def get_snapshot(self, snapshot_id) -> dict[str, Any]:
        return self.snapshot

    def iter_snapshot_sessions(self, snapshot_id) -> Iterator[dict[str, Any]]:
        self.session_requests += 1
        for item in self.sessions:
            if self.honour_exclude_erased and item.get("erased"):
                continue
            yield item

    def register_model(self, payload) -> dict[str, Any]:
        self._maybe_fail("register")
        self.models.append(payload)
        return {"id": f"model-{len(self.models)}", "stage": "registered", **payload}

    @property
    def statuses(self) -> list[str]:
        return [u["status"] for u in self.updates]


def make_context(job_type: str = "build_features", **params: Any) -> JobContext:
    return JobContext(JOB_ID, job_type, SNAPSHOT_ID, dict(params))


def make_env(tmp_path: Path, api: FakeApi | None = None) -> JobEnv:
    return JobEnv(
        api=api or FakeApi(),
        store=local_store(tmp_path / "bucket"),
        scratch=tmp_path / "scratch",
        secrets=Secrets(TOKEN, S3_SECRET),
        clock=lambda: FIXED_NOW,
    )


def key_set(store: ObjectStore, prefix: str) -> set[str]:
    return set(store.list_keys(prefix))
