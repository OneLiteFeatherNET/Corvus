"""Object keys of the artefacts the jobs write (relative to the lake bucket)."""

from __future__ import annotations

import re

FEATURES_ROOT = "features"
MODELS_ROOT = "models"
REPORTS_ROOT = "reports"

SESSIONS_FILE = "sessions.parquet"
LABELS_FILE = "labels.parquet"
BUILD_FILE = "build.json"  # written last: its presence marks a complete feature build

MODEL_FILE = "model.onnx"
CALIBRATION_FILE = "calibration.json"
MODEL_CARD_FILE = "model_card.json"
EVALUATION_FILE = "evaluation.json"

_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def features_prefix(snapshot_id: str) -> str:
    return f"{FEATURES_ROOT}/{snapshot_id}"


def reports_prefix(snapshot_id: str, job_id: str) -> str:
    return f"{REPORTS_ROOT}/{snapshot_id}/{job_id}"


def model_prefix(name: str, version: str) -> str:
    return f"{MODELS_ROOT}/{name}/{version}"


def safe_name(text: str) -> str:
    """A name usable as a path segment and registry name."""
    return _UNSAFE.sub("_", text).strip("._-") or "model"
