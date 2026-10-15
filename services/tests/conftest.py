"""Test setup: make sure the protobuf bindings exist before any test module imports them."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_SERVICES = Path(__file__).resolve().parents[1]
_PB2 = _SERVICES / "src/corvus_services/_gen/corvus/capture/v1/capture_pb2.py"
_PROTO = _SERVICES.parent / "proto/corvus/capture/v1/capture.proto"


def _ensure_bindings() -> None:
    """Generate the bindings once per checkout (or when the .proto is newer)."""
    if _PB2.exists() and _PB2.stat().st_mtime >= _PROTO.stat().st_mtime:
        return
    out = _PB2.parents[3]
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "grpc_tools.protoc",
            f"--proto_path={_PROTO.parents[3]}",
            f"--python_out={out}",
            str(_PROTO),
        ],
        check=True,
    )


# Must run at import time: test modules import corvus_services.proto during collection.
_ensure_bindings()
