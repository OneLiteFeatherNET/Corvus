"""Loader for the generated capture protobuf bindings.

The bindings are generated from ``proto/corvus/capture/v1/capture.proto`` into the
gitignored ``_gen`` directory. They are (re)generated on first import when missing
or older than the .proto, so ``uv run pytest`` works from a fresh checkout.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType

_PROTO_REL = Path("corvus/capture/v1/capture.proto")
_PB2_REL = Path("corvus/capture/v1/capture_pb2.py")
_GEN_DIR = Path(__file__).resolve().parent / "_gen"


def proto_dir() -> Path:
    """Directory containing the ``corvus/capture/v1/capture.proto`` tree."""
    override = os.environ.get("CORVUS_PROTO_DIR")
    if override:
        return Path(override)
    # src/corvus_ml/proto.py -> ml/src/corvus_ml -> repo root is parents[3]
    return Path(__file__).resolve().parents[3] / "proto"


def _is_fresh(pb2: Path, proto: Path) -> bool:
    return pb2.exists() and pb2.stat().st_mtime >= proto.stat().st_mtime


def generate(proto_root: Path, gen_dir: Path) -> Path:
    """Run protoc into a temp dir and atomically move the result into place."""
    proto = proto_root / _PROTO_REL
    if not proto.exists():
        raise FileNotFoundError(f"capture.proto not found at {proto}; set CORVUS_PROTO_DIR")
    target = gen_dir / _PB2_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=gen_dir) as tmp:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "grpc_tools.protoc",
                f"--proto_path={proto_root}",
                f"--python_out={tmp}",
                str(proto),
            ],
            check=True,
        )
        os.replace(Path(tmp) / _PB2_REL, target)
    return target


def _load() -> ModuleType:
    pb2 = _GEN_DIR / _PB2_REL
    proto = proto_dir() / _PROTO_REL
    if not _is_fresh(pb2, proto):
        _GEN_DIR.mkdir(parents=True, exist_ok=True)
        generate(proto_dir(), _GEN_DIR)
    spec = importlib.util.spec_from_file_location("corvus_ml._gen.capture_pb2", pb2)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


pb = _load()
"""The generated ``capture_pb2`` module."""
