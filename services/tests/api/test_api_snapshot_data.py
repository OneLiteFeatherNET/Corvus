from __future__ import annotations

from uuid import UUID

import pytest
from api_fakes import FIXED_NOW
from api_support import Env

from corvus_services.api.ports import SnapshotRecord
from corvus_services.api.snapshots import LakeFileRef, build_manifest, manifest_sha256

SNAP = UUID(int=42)
SESSIONS = [bytes([i]) * 16 for i in range(1, 6)]


def _add_snapshot(env: Env) -> None:
    manifest = build_manifest(
        pool_id=UUID(int=1),
        pool_name="p",
        pool_filter={},
        session_ids=SESSIONS,
        files=[LakeFileRef(1, "lake/events/a"), LakeFileRef(2, "lake/events/b")],
        created_at=FIXED_NOW,
    )
    env.parts["snapshots"].rows[SNAP] = SnapshotRecord(
        SNAP, UUID(int=1), "p", manifest, manifest_sha256(manifest), 5, 2, "u", FIXED_NOW
    )


@pytest.fixture
def ds(env: Env, token):
    _add_snapshot(env)
    return env.headers(token(["data_scientist"]))


def test_sessions_are_listed_ascending_with_a_cursor(env: Env, ds):
    first = env.client.get(f"/api/v1/snapshots/{SNAP}/sessions?limit=2", headers=ds).json()
    second = env.client.get(
        f"/api/v1/snapshots/{SNAP}/sessions?limit=2&cursor={first['next_cursor']}", headers=ds
    ).json()

    assert first["items"] == [SESSIONS[0].hex(), SESSIONS[1].hex()]
    assert first["next_cursor"] == SESSIONS[1].hex()
    assert second["items"] == [SESSIONS[2].hex(), SESSIONS[3].hex()]


def test_last_page_has_no_cursor(env: Env, ds):
    resp = env.client.get(f"/api/v1/snapshots/{SNAP}/sessions?limit=5", headers=ds).json()

    assert len(resp["items"]) == 5
    assert resp["next_cursor"] is None


def test_exclude_erased_drops_exactly_the_erased_sessions(env: Env, ds):
    env.parts["snapshots"].erased = {SESSIONS[1], SESSIONS[3]}

    kept = env.client.get(
        f"/api/v1/snapshots/{SNAP}/sessions?exclude_erased=true&limit=5", headers=ds
    ).json()["items"]
    everything = env.client.get(f"/api/v1/snapshots/{SNAP}/sessions?limit=5", headers=ds).json()

    assert kept == [SESSIONS[i].hex() for i in (0, 2, 4)]
    assert len(everything["items"]) == 5  # the default keeps the manifest unchanged


def test_page_size_is_capped_by_the_server_maximum(env: Env, ds):
    resp = env.client.get(f"/api/v1/snapshots/{SNAP}/sessions?limit=1000", headers=ds).json()

    assert len(resp["items"]) == 5  # max_page_size of the test settings


@pytest.mark.parametrize("cursor", ["zz", "abcd", "00" * 17])
def test_a_malformed_cursor_is_rejected(env: Env, ds, cursor):
    resp = env.client.get(f"/api/v1/snapshots/{SNAP}/sessions?cursor={cursor}", headers=ds)

    assert resp.status_code == 400


def test_unknown_snapshot_is_404(env: Env, ds):
    resp = env.client.get(f"/api/v1/snapshots/{UUID(int=9)}/sessions", headers=ds)

    assert resp.status_code == 404


@pytest.mark.parametrize("role", ["viewer", "staff"])
@pytest.mark.parametrize("path", ["sessions", "files"])
def test_other_roles_are_forbidden(env: Env, token, role, path):
    _add_snapshot(env)

    resp = env.client.get(f"/api/v1/snapshots/{SNAP}/{path}", headers=env.headers(token([role])))

    assert resp.status_code == 403


@pytest.mark.parametrize("path", ["sessions", "files"])
def test_anonymous_is_401(env: Env, path):
    assert env.client.get(f"/api/v1/snapshots/{SNAP}/{path}").status_code == 401


def test_admin_may_read(env: Env, token):
    _add_snapshot(env)

    resp = env.client.get(
        f"/api/v1/snapshots/{SNAP}/sessions", headers=env.headers(token(["admin"]))
    )

    assert resp.status_code == 200


def test_files_follow_replacements_and_omit_vanished_files(env: Env, ds):
    # file 1 was rewritten into file 7; file 2 held only erased sessions and was dropped
    env.parts["snapshots"].lake_files = {
        1: ("lake/events/a", 7, True),
        7: ("lake/events/a2", None, False),
        2: ("lake/events/b", None, True),
    }

    resp = env.client.get(f"/api/v1/snapshots/{SNAP}/files", headers=ds)

    assert resp.json()["items"] == [{"manifest_file_id": 1, "id": 7, "key": "lake/events/a2"}]


def test_untouched_files_are_returned_as_they_are(env: Env, ds):
    env.parts["snapshots"].lake_files = {
        1: ("lake/events/a", None, False),
        2: ("lake/events/b", None, False),
    }

    items = env.client.get(f"/api/v1/snapshots/{SNAP}/files", headers=ds).json()["items"]

    assert [(i["manifest_file_id"], i["id"]) for i in items] == [(1, 1), (2, 2)]
