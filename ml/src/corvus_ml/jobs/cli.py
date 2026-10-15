"""``corvus-job <build_features|evaluate|train>``: entry point of the control plane's jobs."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path

from corvus_ml.jobs import features_job, model_jobs
from corvus_ml.jobs.api import HttpApiClient, JobApi
from corvus_ml.jobs.context import (
    JOB_TYPES,
    ApiSettings,
    ContractError,
    JobContext,
    S3Settings,
    Secrets,
)
from corvus_ml.jobs.lifecycle import Handler, JobEnv, JobTerminated, run_job
from corvus_ml.jobs.params import ParamError
from corvus_ml.jobs.storage import ObjectStore, s3_store

HANDLERS: dict[str, Handler] = {
    "build_features": features_job.build_features,
    "evaluate": model_jobs.evaluate,
    "train": model_jobs.train,
}

log = logging.getLogger("corvus.job")


def _on_sigterm(signum: int, frame: object) -> None:
    raise JobTerminated("terminated")


def main(
    argv: list[str] | None = None,
    environ: Mapping[str, str] | None = None,
    *,
    api_factory: Callable[[ApiSettings, str], JobApi] = HttpApiClient,
    store_factory: Callable[[S3Settings], ObjectStore] = s3_store,
) -> int:
    parser = argparse.ArgumentParser(prog="corvus-job", description=__doc__)
    parser.add_argument("job_type", nargs="?", choices=JOB_TYPES, help="default: CORVUS_JOB_TYPE")
    args = parser.parse_args(argv)
    env = os.environ if environ is None else environ
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    secrets = Secrets.from_env(env)
    try:
        ctx = JobContext.from_env(env, args.job_type)
        api_settings = ApiSettings.from_env(env)
    except ContractError as exc:
        print(f"corvus-job: {secrets.scrub(str(exc))}", file=sys.stderr)
        return 2

    api = api_factory(api_settings, ctx.job_id)
    scratch_root = env.get("CORVUS_JOB_SCRATCH_DIR") or None
    previous = signal.signal(signal.SIGTERM, _on_sigterm)
    try:
        with tempfile.TemporaryDirectory(prefix="corvus-job-", dir=scratch_root) as tmp:
            job_env = JobEnv(
                api=api,
                store=store_factory(S3Settings.from_env(env)),
                scratch=Path(tmp),
                secrets=secrets,
            )
            return run_job(ctx, job_env, _checked(HANDLERS[ctx.job_type]))
    finally:
        signal.signal(signal.SIGTERM, previous)


def _checked(handler: Handler) -> Handler:
    """Turn parameter errors into plain failures with their message."""

    def run(ctx: JobContext, env: JobEnv):
        try:
            return handler(ctx, env)
        except ParamError as exc:
            raise ValueError(f"invalid params: {exc}") from None

    return run


if __name__ == "__main__":
    raise SystemExit(main())
