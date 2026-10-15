"""The HTTP adapter of the API port, driven through an injected opener (no network)."""

import io
import json
import urllib.error

import pytest

from corvus_ml.jobs.api import ApiError, HttpApiClient
from corvus_ml.jobs.context import ApiSettings
from job_fakes import JOB_ID, TOKEN


class Response:
    def __init__(self, body):
        self._body = json.dumps(body).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class ScriptedOpener:
    """Returns the scripted responses in order; an exception instance is raised instead."""

    def __init__(self, *script):
        self.script = list(script)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return Response(step)


def http_error(code, body=None):
    payload = json.dumps(body or {}).encode()
    return urllib.error.HTTPError("http://api", code, "x", {}, io.BytesIO(payload))


def client(opener, retries=3, sleeps=None):
    return HttpApiClient(
        ApiSettings("http://api:8080", TOKEN),
        JOB_ID,
        opener=opener,
        retries=retries,
        page_size=2,
        sleep=(sleeps if sleeps is not None else []).append,
    )


def test_requests_carry_the_bearer_token_and_the_job_id_header():
    opener = ScriptedOpener({})

    client(opener).update_job(JOB_ID, "running", message="started")

    request = opener.requests[0]
    assert request.get_method() == "PATCH"
    assert request.full_url == f"http://api:8080/api/v1/jobs/{JOB_ID}"
    assert request.get_header("Authorization") == f"Bearer {TOKEN}"
    assert request.get_header("X-corvus-job-id") == JOB_ID
    assert json.loads(request.data) == {"status": "running", "message": "started"}


def test_update_sends_the_result_only_when_given():
    opener = ScriptedOpener({}, {})

    api = client(opener)
    api.update_job(JOB_ID, "succeeded", "done", {"windows": 3})
    api.update_job(JOB_ID, "failed", "boom")

    assert json.loads(opener.requests[0].data)["result"] == {"windows": 3}
    assert "result" not in json.loads(opener.requests[1].data)


def test_an_unknown_status_is_refused_before_any_request():
    opener = ScriptedOpener()

    with pytest.raises(ValueError, match="status"):
        client(opener).update_job(JOB_ID, "queued")

    assert opener.requests == []


def test_sessions_are_paged_and_ask_for_erased_players_to_be_excluded():
    opener = ScriptedOpener(
        {"items": [{"session_id": "a"}, {"session_id": "b"}], "next_cursor": "c1"},
        {"items": [{"session_id": "c"}], "next_cursor": None},
    )

    sessions = list(client(opener).iter_snapshot_sessions("snap"))

    assert [s["session_id"] for s in sessions] == ["a", "b", "c"]
    first, second = (r.full_url for r in opener.requests)
    assert "/snapshots/snap/sessions?" in first
    assert "exclude_erased=true" in first
    assert "cursor" not in first
    assert "cursor=c1" in second
    assert "exclude_erased=true" in second


def test_transient_errors_are_retried_with_backoff():
    sleeps = []
    opener = ScriptedOpener(http_error(503), urllib.error.URLError("reset"), {"id": "snap"})

    snapshot = client(opener, sleeps=sleeps).get_snapshot("snap")

    assert snapshot == {"id": "snap"}
    assert len(opener.requests) == 3
    assert sleeps == [1.0, 2.0]


def test_client_errors_are_not_retried_and_do_not_leak_the_token():
    opener = ScriptedOpener(http_error(403, {"title": "Forbidden", "detail": "role missing"}))

    with pytest.raises(ApiError) as caught:
        client(opener).get_snapshot("snap")

    assert caught.value.status == 403
    assert "role missing" in str(caught.value)
    assert TOKEN not in str(caught.value)
    assert len(opener.requests) == 1


def test_a_failure_that_outlasts_the_retries_is_an_api_error():
    opener = ScriptedOpener(*[http_error(502)] * 3)

    with pytest.raises(ApiError, match="502"):
        client(opener).get_snapshot("snap")


def test_model_registration_is_posted_once_and_never_retried():
    opener = ScriptedOpener(http_error(503))

    with pytest.raises(ApiError):
        client(opener).register_model({"name": "m"})

    assert len(opener.requests) == 1
    assert opener.requests[0].get_method() == "POST"
    assert opener.requests[0].full_url.endswith("/api/v1/models")
