"""Loader for the generated protobuf bindings (capture and score).

The bindings are generated at build/dev time by ``scripts/gen_proto.sh`` into the gitignored
``_gen`` directory. Nothing here runs protoc: if the bindings are missing, importing fails with a
hint. Tests generate them through a session fixture in ``tests/conftest.py``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

GEN_DIR = Path(__file__).resolve().parent / "_gen"
PB2_REL = Path("corvus/capture/v1/capture_pb2.py")
SCORE_PB2_REL = Path("corvus/score/v1/score_pb2.py")


def load_generated(rel: Path, name: str) -> ModuleType:
    """Import the generated module at ``_gen/<rel>`` under ``name``."""
    pb2 = GEN_DIR / rel
    if not pb2.exists():
        raise ImportError(f"{pb2} is missing; run services/scripts/gen_proto.sh")
    spec = importlib.util.spec_from_file_location(name, pb2)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


pb = load_generated(PB2_REL, "corvus_services._gen.capture_pb2")
"""The generated ``capture_pb2`` module."""
