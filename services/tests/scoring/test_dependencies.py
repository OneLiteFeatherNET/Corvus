"""The scorer must not drag the training stack into the service image."""

from __future__ import annotations

import subprocess
import sys

CHECK = """
import sys
import corvus_services.scoring.main
import corvus_services.scoring.scorer
heavy = [m for m in ("torch", "sklearn", "skl2onnx", "onnx") if m in sys.modules]
assert not heavy, f"imported by the scorer: {heavy}"
assert "onnxruntime" in sys.modules
"""


def test_importing_the_scorer_does_not_import_torch_or_scikit_learn():
    result = subprocess.run(
        [sys.executable, "-I", "-c", CHECK], capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stderr


def test_the_training_stack_is_not_installed_at_all():
    code = "import importlib.util as u; print([m for m in ('torch', 'sklearn') if u.find_spec(m)])"
    result = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, check=False
    )

    assert result.stdout.strip() == "[]", "the service environment pulled the eval extra"
