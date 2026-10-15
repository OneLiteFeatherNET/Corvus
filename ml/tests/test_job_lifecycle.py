"""Job status lifecycle, the environment contract and secret hygiene."""

import math

import pytest

from corvus_ml.jobs import cli
from corvus_ml.jobs.api import ApiError
from corvus_ml.jobs.context import ContractError, JobContext, Secrets
from corvus_ml.jobs.lifecycle import JobError, JobOutcome, JobTerminated, run_job
from job_fakes import JOB_ID, SNAPSHOT_ID, TOKEN, FakeApi, make_context, make_env


def test_a_successful_job_reports_running_then_succeeded_with_message_and_result(tmp_path):
    api = FakeApi()
    env = make_env(tmp_path, api)

    code = run_job(
        make_context(), env, lambda ctx, e: JobOutcome("built 3 windows", {"windows": 3})
    )

    assert code == 0
    assert api.statuses == ["running", "succeeded"]
    assert api.updates[0]["job_id"] == JOB_ID
    assert api.updates[1]["message"] == "built 3 windows"
    assert api.updates[1]["result"] == {"windows": 3}


def failing(exc):
    def handler(ctx, env):
        raise exc

    return handler


def test_a_failing_job_reports_failed_with_the_reason_and_exits_non_zero(tmp_path):
    api = FakeApi()

    code = run_job(
        make_context(), make_env(tmp_path, api), failing(JobError("snapshot has no events files"))
    )

    assert code == 1
    assert api.statuses == ["running", "failed"]
    assert api.updates[-1]["message"] == "snapshot has no events files"
    assert api.updates[-1]["result"] is None


def test_an_unexpected_exception_is_reported_with_its_type(tmp_path):
    api = FakeApi()

    run_job(make_context(), make_env(tmp_path, api), failing(KeyError("files")))

    assert api.updates[-1]["message"].startswith("KeyError")


def test_a_terminated_job_is_reported_as_failed(tmp_path):
    api = FakeApi()

    code = run_job(make_context(), make_env(tmp_path, api), failing(JobTerminated()))

    assert (code, api.statuses) == (1, ["running", "failed"])
    assert "terminated" in api.updates[-1]["message"]


def test_secrets_never_reach_the_reported_message(tmp_path):
    api = FakeApi()
    boom = RuntimeError(f"upload failed with key {TOKEN} and header Bearer abc.def-123")

    run_job(make_context(), make_env(tmp_path, api), failing(boom))

    message = api.updates[-1]["message"]
    assert TOKEN not in message
    assert "abc.def-123" not in message
    assert "***" in message


def test_the_success_message_is_scrubbed_too(tmp_path):
    api = FakeApi()

    run_job(make_context(), make_env(tmp_path, api), lambda c, e: JobOutcome(f"used {TOKEN}", {}))

    assert TOKEN not in api.updates[-1]["message"]


def test_long_messages_are_truncated(tmp_path):
    api = FakeApi()

    run_job(make_context(), make_env(tmp_path, api), failing(JobError("x" * 5000)))

    assert len(api.updates[-1]["message"]) <= 500


def test_non_finite_numbers_are_dropped_from_the_result(tmp_path):
    api = FakeApi()
    result = {"ok": 1.0, "nan": math.nan, "inf": math.inf, "nested": {"x": [1, math.nan]}}

    run_job(make_context(), make_env(tmp_path, api), lambda c, e: JobOutcome("done", result))

    assert api.updates[-1]["result"] == {"ok": 1.0, "nested": {"x": [1, None]}}


def test_the_handler_does_not_run_when_running_cannot_be_reported(tmp_path):
    api = FakeApi()
    api.fail_on["update:running"] = ApiError("down")
    called = []

    code = run_job(
        make_context(), make_env(tmp_path, api), lambda c, e: called.append(1) or JobOutcome("", {})
    )

    assert code == 2
    assert called == []


def test_a_lost_final_update_is_an_error_exit(tmp_path):
    api = FakeApi()
    api.fail_on["update:succeeded"] = ApiError("down")

    assert run_job(make_context(), make_env(tmp_path, api), lambda c, e: JobOutcome("ok", {})) == 2


# --- environment contract -----------------------------------------------------------------


def contract_env(**extra):
    env = {
        "CORVUS_JOB_ID": JOB_ID,
        "CORVUS_JOB_TYPE": "build_features",
        "CORVUS_JOB_SNAPSHOT_ID": SNAPSHOT_ID,
        "CORVUS_JOB_PARAMS": '{"window": 8}',
        "CORVUS_API_URL": "http://api:8080",
        "CORVUS_API_TOKEN": TOKEN,
    }
    return env | extra


def test_the_context_is_read_from_the_environment():
    ctx = JobContext.from_env(contract_env(), "build_features")

    assert (ctx.job_id, ctx.snapshot_id, ctx.params) == (JOB_ID, SNAPSHOT_ID, {"window": 8})


def test_the_job_type_defaults_to_the_environment():
    assert JobContext.from_env(contract_env(CORVUS_JOB_TYPE="train")).job_type == "train"


@pytest.mark.parametrize(
    "broken",
    [
        {"CORVUS_JOB_ID": "not-a-uuid"},
        {"CORVUS_JOB_SNAPSHOT_ID": ""},
        {"CORVUS_JOB_PARAMS": "{nope"},
        {"CORVUS_JOB_PARAMS": "[1, 2]"},
        {"CORVUS_JOB_TYPE": "evaluate"},  # disagrees with the argument
    ],
)
def test_a_broken_contract_is_rejected(broken):
    with pytest.raises(ContractError):
        JobContext.from_env(contract_env(**broken), "build_features")


def test_missing_params_mean_an_empty_object():
    env = contract_env()
    del env["CORVUS_JOB_PARAMS"]

    assert JobContext.from_env(env, "build_features").params == {}


def test_secrets_scrub_known_values_and_bearer_tokens():
    secrets = Secrets("topsecret-value", None, "ab")

    text = secrets.scrub("key topsecret-value, Authorization: Bearer xyz.123, ab stays")

    assert "topsecret-value" not in text
    assert "xyz.123" not in text
    assert "ab stays" in text  # values under four characters are not treated as secrets


# --- command line -------------------------------------------------------------------------


def test_the_cli_exits_2_without_the_api_settings(capsys):
    env = contract_env()
    del env["CORVUS_API_TOKEN"]

    assert cli.main(["build_features"], env) == 2
    assert "CORVUS_API_TOKEN" in capsys.readouterr().err


def test_the_cli_reports_invalid_params_as_a_failed_job(tmp_path):
    api = FakeApi()
    env = contract_env(CORVUS_JOB_PARAMS='{"window": "wide"}', CORVUS_JOB_SCRATCH_DIR=str(tmp_path))

    code = cli.main(
        ["build_features"],
        env,
        api_factory=lambda settings, job_id: api,
        store_factory=lambda settings: make_env(tmp_path).store,
    )

    assert code == 1
    assert api.statuses == ["running", "failed"]
    assert "window" in api.updates[-1]["message"]
