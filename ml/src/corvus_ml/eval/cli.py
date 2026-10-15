"""``corvus-eval`` command line."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from corvus_ml.eval.config import EvalConfig

MISSING_EXTRA = (
    "corvus-eval needs the optional 'eval' dependencies (torch, scikit-learn, onnx, ...): "
    "install them with `uv sync --extra eval` or `pip install 'corvus-ml[eval]'`"
)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="corvus-eval")
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="evaluate the configured candidates")
    r.add_argument("--config", required=True, type=Path, help="eval.toml")
    r.add_argument("--out", required=True, type=Path, help="report directory")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        from corvus_ml.eval import candidates as _candidates  # noqa: F401 - registers them
        from corvus_ml.eval.report import write_report
        from corvus_ml.eval.runner import run
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.split(".")[0] in {
            "torch",
            "sklearn",
            "skl2onnx",
            "onnx",
            "onnxruntime",
        }:
            print(f"{MISSING_EXTRA} (missing module: {exc.name})", file=sys.stderr)
            return 2
        raise
    cfg = EvalConfig.load(args.config)
    report = run(cfg, args.out)
    js, md = write_report(report, args.out)
    print(md.read_text())
    print(f"wrote {js} and {md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
