from __future__ import annotations

import base64
import logging
import uuid
from datetime import UTC, datetime

import pytest
from api_fakes import FIXED_NOW, FakeErasureStore, sequential_ids
from api_support import PLUGIN_TOKEN, Env

from corvus_services.api.erasure import erase_player
from corvus_services.api.ports import ScoreHistoryRecord, ScoreLatestRecord
from corvus_services.api.pseudonym import Pseudonymizer, PseudonymKeyMissingError, parse_pseudonym
from corvus_services.api.settings import ApiSettings

# Documented in docs/reference/pseudonym-test-vector.md; the plugin must reproduce these values.
VECTOR_KEY = b"0123456789abcdef"
VECTOR_UUID = uuid.UUID("11111111-2222-3333-4444-555555555555")
VECTOR_PSEUDONYM = "40642e55746eeaffde2e9de80d506b79cd3dbbc299a84da87a3812759417da84"
VECTOR2_KEY = bytes(range(32))
VECTOR2_UUID = uuid.UUID("00000000-0000-0000-0000-000000000000")
VECTOR2_PSEUDONYM = "ff921230177d146ee8401eb82173128410002ba6f997e8e837e804407c8cdd38"

PLAYER = uuid.UUID("11111111-2222-3333-4444-555555555555")
PSEUDONYM = bytes.fromhex(VECTOR_PSEUDONYM)


# --- pseudonym algorithm -------------------------------------------------------------------------


def test_pseudonym_matches_the_documented_vector():
    assert Pseudonymizer(VECTOR_KEY, "k1").pseudonym(VECTOR_UUID).hex() == VECTOR_PSEUDONYM


def test_pseudonym_matches_the_second_vector_with_a_32_byte_key():
    assert Pseudonymizer(VECTOR2_KEY, "k1").pseudonym(VECTOR2_UUID).hex() == VECTOR2_PSEUDONYM


def test_pseudonym_hashes_the_big_endian_uuid_bytes_not_the_text():
    import hashlib
    import hmac

    expected = hmac.new(VECTOR_KEY, VECTOR_UUID.hex.encode(), hashlib.sha256).hexdigest()

    assert Pseudonymizer(VECTOR_KEY, "k1").pseudonym(VECTOR_UUID).hex() != expected


def test_pseudonym_depends_on_key_and_uuid():
    a = Pseudonymizer(VECTOR_KEY, "k1")
    b = Pseudonymizer(b"fedcba9876543210", "k1")

    assert a.pseudonym(VECTOR_UUID) != b.pseudonym(VECTOR_UUID)
    assert a.pseudonym(VECTOR_UUID) != a.pseudonym(uuid.UUID(int=1))
    assert len(a.pseudonym(VECTOR_UUID)) == 32


def test_pseudonym_without_a_key_fails_closed():
    p = Pseudonymizer(None, "k1")

    assert not p.available
    with pytest.raises(PseudonymKeyMissingError):
        p.pseudonym(VECTOR_UUID)


def test_settings_decode_the_key_like_the_plugin_does():
    settings = ApiSettings(pseudonym_key=base64.b64encode(VECTOR_KEY).decode())

    assert settings.pseudonym_key_bytes() == VECTOR_KEY


@pytest.mark.parametrize("bad", ["not base64!!", base64.b64encode(b"short").decode()])
def test_settings_reject_unusable_pseudonym_keys(bad):
    with pytest.raises(ValueError, match="CORVUS_PSEUDONYM_KEY"):
        ApiSettings(pseudonym_key=bad)


def test_settings_read_the_plugin_environment_variables(monkeypatch):
    monkeypatch.setenv("CORVUS_PSEUDONYM_KEY", base64.b64encode(VECTOR_KEY).decode())
    monkeypatch.setenv("CORVUS_PSEUDONYM_KEY_ID", "k7")

    s = ApiSettings()

    assert (s.pseudonym_key_bytes(), s.pseudonym_key_id) == (VECTOR_KEY, "k7")


@pytest.mark.parametrize("bad", ["", "ab" * 31, "zz" * 32, "ab" * 33])
def test_parse_pseudonym_rejects_malformed_values(bad):
    with pytest.raises(ValueError, match="pseudonym"):
        parse_pseudonym(bad)


# --- erasure transaction -------------------------------------------------------------------------


def _store(with_key: bool = True) -> FakeErasureStore:
    store = FakeErasureStore()
    store.add_player(PSEUDONYM, with_key=with_key)
    return store


async def test_erasure_runs_all_steps():
    store = _store()

    request = await erase_player(
        store, PSEUDONYM, requested_by="root", now=FIXED_NOW, new_id=sequential_ids()
    )

    assert PSEUDONYM not in store.keys
    assert store.players[PSEUDONYM].erased_at == FIXED_NOW
    assert store.blocklist[PSEUDONYM].kind == "block"
    assert store.blocklist[PSEUDONYM].source == "erasure"
    assert store.requests[request.id] == request
    assert (request.requested_by, request.status) == ("root", "completed")
    assert (request.key_deleted, request.player_known, request.compaction_pending) == (
        True,
        True,
        True,
    )


@pytest.mark.parametrize(
    "step", ["delete_player_key", "delete_scores", "mark_erased", "block", "record_request"]
)
async def test_erasure_is_all_or_nothing(step):
    store = _store()
    store.fail_at = step

    with pytest.raises(RuntimeError, match=step):
        await erase_player(store, PSEUDONYM, requested_by="root", now=FIXED_NOW)

    assert PSEUDONYM in store.keys, "the key must not be gone when the transaction failed"
    assert store.players[PSEUDONYM].erased_at is None
    assert store.blocklist == {}
    assert store.requests == {}


def _scores(store: FakeErasureStore, pseudonym: bytes) -> None:
    store.latest_rows[pseudonym] = [
        ScoreLatestRecord("pvp", "combat", "gb", "1", "enforce", 0.9, 0.8, 3, FIXED_NOW)
    ]
    store.history_rows[pseudonym] = [
        ScoreHistoryRecord(
            i, b"\x01" * 16, 0, "pvp", "combat", "gb", "1", "shadow", 0.5, 0.4, 1, 0, 1, FIXED_NOW
        )
        for i in (1, 2)
    ]


async def test_erasure_deletes_the_players_scores_but_not_other_players():
    store = _store()
    other = bytes(range(1, 33))
    _scores(store, PSEUDONYM)
    _scores(store, other)

    await erase_player(store, PSEUDONYM, requested_by="root", now=FIXED_NOW)

    assert PSEUDONYM not in store.latest_rows
    assert PSEUDONYM not in store.history_rows
    assert len(store.latest_rows[other]) == 1
    assert len(store.history_rows[other]) == 2


@pytest.mark.parametrize("step", ["mark_erased", "block", "record_request"])
async def test_scores_survive_a_failed_erasure(step):
    store = _store()
    _scores(store, PSEUDONYM)
    store.fail_at = step

    with pytest.raises(RuntimeError):
        await erase_player(store, PSEUDONYM, requested_by="root", now=FIXED_NOW)

    assert len(store.latest_rows[PSEUDONYM]) == 1
    assert len(store.history_rows[PSEUDONYM]) == 2


async def test_erasing_an_unknown_pseudonym_still_deletes_stray_scores():
    store = FakeErasureStore()
    _scores(store, PSEUDONYM)

    await erase_player(store, PSEUDONYM, requested_by="root", now=FIXED_NOW)

    assert PSEUDONYM not in store.latest_rows


def test_erasure_over_http_removes_the_scores_the_scores_endpoint_returns(env: Env, admin):
    env.parts["erasure"].add_player(PSEUDONYM)
    _scores(env.parts["erasure"], PSEUDONYM)
    url = f"/api/v1/scores/players/{PSEUDONYM.hex()}"
    assert env.client.get(url, headers=admin).json()["items"]

    resp = env.client.post(
        "/api/v1/players/erasure", json={"pseudonym": PSEUDONYM.hex()}, headers=admin
    )

    assert resp.status_code == 202
    assert env.client.get(url, headers=admin).json()["items"] == []
    assert env.client.get(url + "/history", headers=admin).json()["items"] == []


async def test_erasing_an_unknown_pseudonym_still_blocklists_and_audits_it():
    store = FakeErasureStore()

    request = await erase_player(store, PSEUDONYM, requested_by="root", now=FIXED_NOW)

    assert (request.player_known, request.key_deleted) == (False, False)
    assert PSEUDONYM in store.blocklist
    assert request.id in store.requests


async def test_erasing_twice_is_idempotent_for_the_player():
    store = _store()
    later = datetime(2026, 10, 16, tzinfo=UTC)

    await erase_player(store, PSEUDONYM, requested_by="root", now=FIXED_NOW)
    second = await erase_player(store, PSEUDONYM, requested_by="root", now=later)

    assert store.players[PSEUDONYM].erased_at == FIXED_NOW
    assert second.key_deleted is False
    assert len(store.requests) == 2


# --- HTTP: lookup and erasure ---------------------------------------------------------------------


@pytest.fixture
def admin(env: Env, token):
    return env.headers(token(["admin"], sub="root"))


def test_lookup_resolves_the_uuid_to_the_pseudonym(env: Env, admin):
    env.parts["erasure"].add_player(PSEUDONYM)

    resp = env.client.post("/api/v1/players/lookup", json={"uuid": str(PLAYER)}, headers=admin)

    assert resp.status_code == 200
    body = resp.json()
    assert body["pseudonym"] == VECTOR_PSEUDONYM
    assert (body["known"], body["key_id"], body["erased_at"]) == (True, "k1", None)


def test_lookup_of_an_unseen_player_is_not_known(env: Env, admin):
    resp = env.client.post("/api/v1/players/lookup", json={"uuid": str(PLAYER)}, headers=admin)

    assert resp.json()["known"] is False


def test_lookup_never_echoes_or_logs_the_clear_uuid(env: Env, admin, caplog):
    with caplog.at_level(logging.DEBUG):
        resp = env.client.post("/api/v1/players/lookup", json={"uuid": str(PLAYER)}, headers=admin)

    assert str(PLAYER) not in resp.text
    assert str(PLAYER) not in caplog.text
    assert PLAYER.hex not in caplog.text


def test_invalid_uuid_error_does_not_echo_the_input(env: Env, admin):
    resp = env.client.post(
        "/api/v1/players/lookup", json={"uuid": "not-a-uuid-SECRET"}, headers=admin
    )

    assert resp.status_code == 422
    assert "SECRET" not in resp.text


def test_lookup_without_a_configured_key_is_unavailable(env: Env, admin):
    env.deps.pseudonymizer = Pseudonymizer(None, "k1")

    resp = env.client.post("/api/v1/players/lookup", json={"uuid": str(PLAYER)}, headers=admin)

    assert resp.status_code == 503
    assert resp.json()["type"].endswith("pseudonym-key-missing")


def test_erasure_by_uuid_returns_202_and_executes(env: Env, admin):
    env.parts["erasure"].add_player(PSEUDONYM)

    resp = env.client.post("/api/v1/players/erasure", json={"uuid": str(PLAYER)}, headers=admin)

    assert resp.status_code == 202
    body = resp.json()
    assert (body["pseudonym"], body["compaction_pending"], body["requested_by"]) == (
        VECTOR_PSEUDONYM,
        True,
        "root",
    )
    assert PSEUDONYM not in env.parts["erasure"].keys
    assert str(PLAYER) not in resp.text
    fetched = env.client.get(f"/api/v1/players/erasure/{body['id']}", headers=admin)
    assert fetched.json() == body


def test_erasure_by_pseudonym_needs_no_key(env: Env, admin):
    env.deps.pseudonymizer = Pseudonymizer(None, "k1")
    env.parts["erasure"].add_player(PSEUDONYM)

    resp = env.client.post(
        "/api/v1/players/erasure", json={"pseudonym": VECTOR_PSEUDONYM}, headers=admin
    )

    assert resp.status_code == 202


@pytest.mark.parametrize(
    "body",
    [{}, {"uuid": str(PLAYER), "pseudonym": VECTOR_PSEUDONYM}, {"pseudonym": "short"}],
)
def test_erasure_needs_exactly_one_valid_identifier(env: Env, admin, body):
    resp = env.client.post("/api/v1/players/erasure", json=body, headers=admin)

    assert resp.status_code == 422


def test_failed_erasure_changes_nothing_and_reports_a_server_error(env: Env, token):
    store = env.parts["erasure"]
    store.add_player(PSEUDONYM)
    store.fail_at = "block"
    client = env.client.__class__(env.client.app, raise_server_exceptions=False)

    resp = client.post(
        "/api/v1/players/erasure",
        json={"uuid": str(PLAYER)},
        headers=env.headers(token(["admin"])),
    )

    assert resp.status_code == 500
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert PSEUDONYM in store.keys
    assert store.blocklist == {}


def test_unknown_erasure_request_is_404(env: Env, admin):
    resp = env.client.get(f"/api/v1/players/erasure/{uuid.UUID(int=5)}", headers=admin)

    assert resp.status_code == 404


# --- HTTP: blocklist ----------------------------------------------------------------------------

P1, P2 = "11" * 32, "22" * 32


def test_blocklist_crud(env: Env, admin):
    put = env.client.put(
        f"/api/v1/blocklist/{P1}", json={"kind": "block", "reason": "opt-out"}, headers=admin
    )
    assert put.status_code == 200
    assert put.json()["source"] == "manual"

    assert env.client.get(f"/api/v1/blocklist/{P1}", headers=admin).json()["reason"] == "opt-out"
    flipped = env.client.put(f"/api/v1/blocklist/{P1}", json={"kind": "allow"}, headers=admin)
    assert flipped.json()["kind"] == "allow"
    assert env.client.delete(f"/api/v1/blocklist/{P1}", headers=admin).status_code == 204
    assert env.client.get(f"/api/v1/blocklist/{P1}", headers=admin).status_code == 404
    assert env.client.delete(f"/api/v1/blocklist/{P1}", headers=admin).status_code == 404


def test_blocklist_list_can_be_filtered_by_kind(env: Env, admin):
    env.client.put(f"/api/v1/blocklist/{P1}", json={"kind": "block"}, headers=admin)
    env.client.put(f"/api/v1/blocklist/{P2}", json={"kind": "allow"}, headers=admin)

    resp = env.client.get("/api/v1/blocklist?kind=allow", headers=admin).json()

    assert [i["pseudonym"] for i in resp["items"]] == [P2]


def test_erasure_entries_cannot_be_changed_or_removed(env: Env, admin):
    env.client.post("/api/v1/players/erasure", json={"pseudonym": VECTOR_PSEUDONYM}, headers=admin)
    path = f"/api/v1/blocklist/{VECTOR_PSEUDONYM}"

    assert env.client.put(path, json={"kind": "allow"}, headers=admin).status_code == 409
    assert env.client.delete(path, headers=admin).status_code == 409
    assert env.client.get(path, headers=admin).json()["kind"] == "block"


def test_blocklist_rejects_malformed_pseudonyms(env: Env, admin):
    assert env.client.get("/api/v1/blocklist/xyz", headers=admin).status_code == 422
    assert (
        env.client.put("/api/v1/blocklist/xyz", json={"kind": "block"}, headers=admin).status_code
        == 422
    )


# --- HTTP: plugin feed --------------------------------------------------------------------------


def _plugin(env: Env, **headers):
    return env.client.get("/api/v1/plugin/blocklist", headers=headers)


def test_plugin_feed_lists_blocked_and_allowed_pseudonyms(env: Env, admin):
    env.client.put(f"/api/v1/blocklist/{P2}", json={"kind": "block"}, headers=admin)
    env.client.put(f"/api/v1/blocklist/{P1}", json={"kind": "block"}, headers=admin)
    env.client.put(f"/api/v1/blocklist/{'33' * 32}", json={"kind": "allow"}, headers=admin)

    resp = _plugin(env, Authorization=f"Bearer {PLUGIN_TOKEN}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["blocked"] == [P1, P2]
    assert body["allowed"] == ["33" * 32]
    assert resp.headers["cache-control"] == "no-store"


def test_plugin_feed_version_changes_with_the_content(env: Env, admin):
    auth = {"Authorization": f"Bearer {PLUGIN_TOKEN}"}
    empty = _plugin(env, **auth).json()["version"]
    env.client.put(f"/api/v1/blocklist/{P1}", json={"kind": "block"}, headers=admin)
    one = _plugin(env, **auth).json()["version"]
    same = _plugin(env, **auth).json()["version"]

    assert empty != one
    assert one == same


def test_plugin_feed_accepts_every_configured_proxy_token(env: Env):
    assert _plugin(env, Authorization="Bearer proxy-token-2").status_code == 200


def test_plugin_feed_rejects_missing_and_wrong_tokens(env: Env):
    assert _plugin(env).status_code == 401
    assert _plugin(env, Authorization="Bearer wrong").status_code == 401
    assert _plugin(env, Authorization=f"Bearer {PLUGIN_TOKEN}x").status_code == 401


def test_plugin_feed_does_not_accept_oidc_tokens(env: Env, token):
    resp = _plugin(env, Authorization=f"Bearer {token(['admin'])}")

    assert resp.status_code == 401


def test_proxy_token_does_not_open_the_admin_api(env: Env):
    resp = env.client.get("/api/v1/blocklist", headers={"Authorization": f"Bearer {PLUGIN_TOKEN}"})

    assert resp.status_code == 401


def test_plugin_feed_is_closed_when_no_tokens_are_configured(env: Env):
    env.deps.plugin_tokens = []

    assert _plugin(env, Authorization="Bearer anything").status_code == 401
