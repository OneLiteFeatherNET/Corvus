"""The default job command must be the entry point the ml image really provides."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from corvus_services.api.settings import ApiSettings

REPO_ROOT = Path(__file__).resolve().parents[3]
ML_DIR = REPO_ROOT / "ml"


def _settings() -> ApiSettings:
    return ApiSettings(_env_file=None)  # type: ignore[call-arg]


def test_default_kubernetes_job_command_is_corvus_job():
    assert _settings().job_k8s_command == ["corvus-job"]


def test_default_local_job_command_is_corvus_job():
    assert _settings().job_local_command == ["corvus-job"]


@pytest.mark.skipif(
    not (ML_DIR / "pyproject.toml").is_file(),
    reason="ml/ is not checked out next to services/ (standalone services run)",
)
def test_default_job_commands_are_console_scripts_of_the_ml_package():
    scripts = tomllib.loads((ML_DIR / "pyproject.toml").read_text())["project"]["scripts"]
    settings = _settings()

    assert settings.job_k8s_command[0] in scripts
    assert settings.job_local_command[0] in scripts


@pytest.mark.skipif(
    not (ML_DIR / "Dockerfile").is_file(),
    reason="ml/ is not checked out next to services/ (standalone services run)",
)
def test_default_kubernetes_command_equals_the_ml_image_entrypoint():
    match = re.search(r"^ENTRYPOINT\s+(\[.*\])\s*$", (ML_DIR / "Dockerfile").read_text(), re.M)
    assert match, "ml/Dockerfile has no exec-form ENTRYPOINT"
    entrypoint = tomllib.loads(f"v = {match.group(1)}")["v"]

    assert _settings().job_k8s_command == entrypoint
