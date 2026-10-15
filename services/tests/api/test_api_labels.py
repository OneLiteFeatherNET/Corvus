from __future__ import annotations

import pytest
from api_support import Env
from pydantic import ValidationError

from corvus_services.api.labels import CsvError, LabelIn, parse_csv

PSEUDO = "ab" * 32
SESSION = "cd" * 16


def _label(**over):
    data = {
        "subject_type": "pseudonym",
        "subject": PSEUDO,
        "label": "cheat",
        "source": "staff_verdict",
        "confidence": 0.9,
    }
    data.update(over)
    return data


def test_valid_label_is_accepted():
    assert LabelIn.model_validate(_label()).subject_bytes() == bytes.fromhex(PSEUDO)


def test_session_label_takes_a_16_byte_id():
    label = LabelIn.model_validate(_label(subject_type="session", subject=SESSION))

    assert len(label.subject_bytes()) == 16


@pytest.mark.parametrize(
    "bad",
    [
        {"label": "maybe"},
        {"source": "gut_feeling"},
        {"confidence": 1.01},
        {"confidence": -0.01},
        {"subject": "abc"},
        {"subject": "zz" * 32},
        {"subject_type": "session"},  # a pseudonym is not a session id
        {"subject_type": "uuid"},
        {"valid_from": "2026-02-01T00:00:00Z", "valid_to": "2026-01-01T00:00:00Z"},
        {"valid_from": "2026-01-01T00:00:00Z", "valid_to": "2026-01-01T00:00:00Z"},
        {"valid_from": "2026-01-01T00:00:00"},  # naive timestamp
        {"note": "x" * 2001},
        {"surprise": 1},
    ],
)
def test_invalid_labels_are_rejected(bad):
    with pytest.raises(ValidationError):
        LabelIn.model_validate(_label(**bad))


@pytest.mark.parametrize("confidence", [0, 0.0, 1, 1.0])
def test_confidence_bounds_are_inclusive(confidence):
    assert LabelIn.model_validate(_label(confidence=confidence))


# --- CSV ----------------------------------------------------------------------------------------

HEADER = "subject_type,subject,label,source,confidence,valid_from,valid_to,note\n"


def test_csv_rows_are_parsed():
    text = HEADER + f"pseudonym,{PSEUDO},cheat,ban_import,1,2026-01-01T00:00:00Z,,banned\n"
    text += f"session,{SESSION},legit,honeypot,0.5,,,\n"

    rows = parse_csv(text, max_rows=10)

    assert [r.label for r in rows] == ["cheat", "legit"]
    assert rows[0].note == "banned"
    assert rows[1].valid_from is None


def test_optional_columns_may_be_missing_from_the_header():
    text = (
        "subject_type,subject,label,source,confidence\n"
        + f"pseudonym,{PSEUDO},cheat,ban_import,1\n"
    )

    assert len(parse_csv(text, 10)) == 1


def test_csv_with_one_bad_row_rejects_everything_and_reports_the_line():
    text = HEADER
    text += f"pseudonym,{PSEUDO},cheat,ban_import,1,,,\n"
    text += f"pseudonym,{PSEUDO},cheat,ban_import,7,,,\n"  # line 3
    text += f"pseudonym,{PSEUDO},nope,ban_import,1,,,\n"  # line 4

    with pytest.raises(CsvError) as err:
        parse_csv(text, 10)

    assert [e.row for e in err.value.errors] == [3, 4]


def test_csv_errors_do_not_echo_row_content():
    text = HEADER + "pseudonym,SECRET-SUBJECT,cheat,ban_import,1,,,\n"

    with pytest.raises(CsvError) as err:
        parse_csv(text, 10)

    assert "SECRET-SUBJECT" not in repr(err.value.errors)


@pytest.mark.parametrize(
    "header",
    ["subject,label,source,confidence\n", HEADER.strip() + ",extra\n", "\n"],
)
def test_csv_header_is_validated(header):
    with pytest.raises(CsvError, match="bad header"):
        parse_csv(header + "x\n", 10)


def test_csv_row_limit_is_enforced():
    text = HEADER + (f"pseudonym,{PSEUDO},cheat,ban_import,1,,,\n" * 4)

    with pytest.raises(CsvError, match="too many rows"):
        parse_csv(text, max_rows=3)


def test_empty_csv_is_rejected():
    with pytest.raises(CsvError, match="no rows"):
        parse_csv(HEADER, 10)


def test_quoted_notes_with_commas_and_newlines_survive():
    text = HEADER + f'pseudonym,{PSEUDO},cheat,ban_import,1,,,"a, b\nc"\n'

    assert parse_csv(text, 10)[0].note == "a, b\nc"


# --- HTTP ---------------------------------------------------------------------------------------


def test_staff_creates_a_label_with_created_by_from_the_token(env: Env, token):
    resp = env.client.post(
        "/api/v1/labels", json=_label(), headers=env.headers(token(["staff"], sub="steve"))
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["created_by"] == "steve"
    assert body["subject"] == PSEUDO
    assert len(env.parts["labels"].rows) == 1


def test_created_by_in_the_body_is_rejected_not_trusted(env: Env, token):
    resp = env.client.post(
        "/api/v1/labels",
        json=_label(created_by="someone-else"),
        headers=env.headers(token(["staff"])),
    )

    assert resp.status_code == 422


def test_invalid_label_returns_422_without_echoing_the_input(env: Env, token):
    resp = env.client.post(
        "/api/v1/labels",
        json=_label(note="top-secret-note" * 200),
        headers=env.headers(token(["staff"])),
    )

    assert resp.status_code == 422
    assert "top-secret-note" not in resp.text


def test_viewer_reads_but_cannot_write(env: Env, token):
    viewer = env.headers(token(["viewer"]))

    assert env.client.get("/api/v1/labels", headers=viewer).status_code == 200
    assert env.client.post("/api/v1/labels", json=_label(), headers=viewer).status_code == 403


def test_labels_can_be_filtered_by_subject(env: Env, token):
    staff = env.headers(token(["staff"]))
    env.client.post("/api/v1/labels", json=_label(), headers=staff)
    env.client.post("/api/v1/labels", json=_label(subject="ef" * 32), headers=staff)

    resp = env.client.get(
        f"/api/v1/labels?subject_type=pseudonym&subject={PSEUDO}", headers=staff
    ).json()

    assert [i["subject"] for i in resp["items"]] == [PSEUDO]


def test_subject_filter_needs_a_subject_type(env: Env, token):
    resp = env.client.get(
        f"/api/v1/labels?subject={PSEUDO}", headers=env.headers(token(["viewer"]))
    )

    assert resp.status_code == 422


def _csv(env: Env, token, body: str, roles=("data_scientist",)):
    return env.client.post(
        "/api/v1/labels/import",
        content=body.encode(),
        headers={**env.headers(token(list(roles), sub="dana")), "Content-Type": "text/csv"},
    )


def test_csv_import_inserts_all_rows(env: Env, token):
    body = HEADER + f"pseudonym,{PSEUDO},cheat,ban_import,1,,,\n" * 3

    resp = _csv(env, token, body)

    assert resp.status_code == 201
    assert resp.json() == {"imported": 3}
    assert {r.created_by for r in env.parts["labels"].rows} == {"dana"}


def test_csv_import_with_an_invalid_row_imports_nothing(env: Env, token):
    body = (
        HEADER
        + f"pseudonym,{PSEUDO},cheat,ban_import,1,,,\n"
        + "pseudonym,bad,cheat,ban_import,1,,,\n"
    )

    resp = _csv(env, token, body)

    assert resp.status_code == 422
    assert resp.json()["errors"][0]["row"] == 3
    assert env.parts["labels"].rows == []


def test_csv_import_is_not_allowed_for_staff(env: Env, token):
    assert _csv(env, token, HEADER, roles=("staff",)).status_code == 403


def test_csv_import_enforces_the_size_limit(env: Env, token):
    resp = _csv(env, token, HEADER + "x" * 5000)

    assert resp.status_code == 413


def test_csv_import_rejects_non_utf8(env: Env, token):
    resp = env.client.post(
        "/api/v1/labels/import",
        content=b"\xff\xfe\x00bad",
        headers={**env.headers(token(["data_scientist"])), "Content-Type": "text/csv"},
    )

    assert resp.status_code == 422
