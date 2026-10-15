from __future__ import annotations

from datetime import timedelta

import pytest
from api_fakes import FIXED_NOW
from api_support import Env

from corvus_services.api.ports import ScoreHistoryRecord, ScoreLatestRecord

PSEUDONYM = bytes(range(32))
OTHER = bytes(range(1, 33))
URL = f"/api/v1/scores/players/{PSEUDONYM.hex()}"


def _latest(model: str = "gb", stage: str = "enforce", ctx: str = "pvp") -> ScoreLatestRecord:
    return ScoreLatestRecord(ctx, "combat", model, "1", stage, 0.8, 0.7, 5, FIXED_NOW)


def _history(i: int, ctx: str = "pvp", stage: str = "shadow") -> ScoreHistoryRecord:
    return ScoreHistoryRecord(
        i, b"\x07" * 16, 0, ctx, "combat", "gb", "1", stage, 0.5, 0.4, i,
        1_000, 2_000, FIXED_NOW + timedelta(seconds=i),
    )  # fmt: skip


@pytest.fixture
def staff(env: Env, token) -> dict[str, str]:
    return env.headers(token(["staff"]))


def test_latest_scores_list_every_model_including_shadow(env: Env, staff):
    env.parts["scores"].latest_rows[PSEUDONYM] = [
        _latest("b", "shadow"),
        _latest("a", "enforce"),
    ]

    r = env.client.get(URL, headers=staff)

    assert r.status_code == 200
    items = r.json()["items"]
    assert [(i["model_name"], i["stage"]) for i in items] == [("a", "enforce"), ("b", "shadow")]
    assert items[0]["smoothed"] == pytest.approx(0.7)
    assert items[0]["windows"] == 5


def test_latest_scores_of_an_unknown_player_are_empty_not_404(env: Env, staff):
    r = env.client.get(f"/api/v1/scores/players/{OTHER.hex()}", headers=staff)

    assert r.status_code == 200
    assert r.json() == {"items": []}


def test_latest_scores_only_return_the_requested_player(env: Env, staff):
    env.parts["scores"].latest_rows[OTHER] = [_latest("other")]

    assert env.client.get(URL, headers=staff).json() == {"items": []}


def test_history_is_newest_first_and_limited(env: Env, staff):
    env.parts["scores"].history_rows[PSEUDONYM] = [_history(i) for i in range(1, 6)]

    r = env.client.get(f"{URL}/history?limit=3", headers=staff)

    assert r.status_code == 200
    assert [i["windows"] for i in r.json()["items"]] == [5, 4, 3]
    assert r.json()["items"][0]["session_id"] == "07" * 16


def test_history_filters_by_game_context(env: Env, staff):
    env.parts["scores"].history_rows[PSEUDONYM] = [_history(1, "pvp"), _history(2, "survival")]

    r = env.client.get(f"{URL}/history?game_context=survival", headers=staff)

    assert [i["game_context"] for i in r.json()["items"]] == ["survival"]


@pytest.mark.parametrize("limit", ["0", "1001", "x"])
def test_history_rejects_an_invalid_limit(env: Env, staff, limit):
    assert env.client.get(f"{URL}/history?limit={limit}", headers=staff).status_code == 422


def test_a_malformed_pseudonym_is_a_validation_error(env: Env, staff):
    assert env.client.get("/api/v1/scores/players/abc", headers=staff).status_code == 422
    assert env.client.get("/api/v1/scores/players/abc/history", headers=staff).status_code == 422


@pytest.mark.parametrize("path", ["", "/history"])
def test_scores_need_a_token(env: Env, path):
    assert env.client.get(URL + path).status_code == 401


@pytest.mark.parametrize("role", ["viewer", "data_scientist"])
@pytest.mark.parametrize("path", ["", "/history"])
def test_scores_are_forbidden_for_roles_other_than_staff(env: Env, token, role, path):
    r = env.client.get(URL + path, headers=env.headers(token([role])))

    assert r.status_code == 403


@pytest.mark.parametrize("path", ["", "/history"])
def test_admin_may_read_scores(env: Env, token, path):
    assert env.client.get(URL + path, headers=env.headers(token(["admin"]))).status_code == 200
