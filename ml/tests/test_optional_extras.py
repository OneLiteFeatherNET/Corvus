"""Core install (numpy, pyarrow, protobuf) must not need the optional eval or serve stacks."""

import subprocess
import sys
import textwrap

import pytest

HEAVY = "{'torch', 'sklearn', 'onnx', 'skl2onnx', 'onnxruntime'}"


def run_isolated(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-I", "-c", textwrap.dedent(code)], capture_output=True, text=True
    )


@pytest.mark.parametrize(
    "module",
    [
        "corvus_ml.features",
        "corvus_ml.features.pipeline",
        "corvus_ml.features.cli",
        "corvus_ml.features.model_input",
        "corvus_ml.features.spec",
        "corvus_ml.eval.dataset",
        "corvus_ml.eval.config",
        "corvus_ml.pilot",
    ],
)
def test_importing_core_modules_does_not_import_ml_stacks(module):
    done = run_isolated(
        f"""
        import importlib, sys
        importlib.import_module({module!r})
        loaded = {HEAVY} & set(sys.modules)
        assert not loaded, sorted(loaded)
        """
    )
    assert done.returncode == 0, done.stderr


def test_eval_cli_explains_the_missing_extra_instead_of_a_traceback():
    done = run_isolated(
        """
        import sys
        for name in ("torch", "sklearn", "skl2onnx", "onnx", "onnxruntime"):
            sys.modules[name] = None  # import raises ModuleNotFoundError, as without the extra
        from corvus_ml.eval import cli
        code = cli.main(["run", "--config", "missing.toml", "--out", "out"])
        raise SystemExit(code)
        """
    )
    assert done.returncode == 2, done.stderr
    assert "'eval'" in done.stderr
    assert "uv sync --extra eval" in done.stderr
