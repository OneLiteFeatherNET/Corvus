"""``corvus-eval`` command line."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import replace
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
    t = sub.add_parser(
        "tune", help="Optuna search for the tunable candidates, then evaluate with the best"
    )
    t.add_argument("--config", required=True, type=Path, help="eval.toml (optional [tune] table)")
    t.add_argument("--out", required=True, type=Path, help="report directory")
    t.add_argument("--trials", type=int, default=None, help="overrides tune.trials")
    q = sub.add_parser("pretrain", help="masked pretraining of a raw-byte model, saves checkpoints")
    q.add_argument("--config", required=True, type=Path, help="eval.toml with a [raw] table")
    q.add_argument("--out", required=True, type=Path, help="directory for pretrain/<context>.pt")
    f = sub.add_parser(
        "lr-find",
        help="learning-rate range test of the torch candidates (nothing is trained)",
    )
    f.add_argument("--config", required=True, type=Path, help="eval.toml")
    f.add_argument("--out", required=True, type=Path, help="writes <out>/lr/<context>__<cand>.*")
    f.add_argument("--steps", type=int, default=None, help="range-test steps (default: lr_steps)")
    return p


def format_lr_find(found: dict, out_dir: Path) -> str:
    lines = []
    for ctx, per_cand in found.items():
        for name, e in per_cand.items():
            find = e.get("lr_find", {})
            s = e["schedule"]
            sug = ", ".join(f"{k}={v:.3g}" for k, v in find.get("suggestions", {}).items() if v)
            lines.append(
                f"{ctx} / {name}: lr {e['lr']:.3g} (rule {find.get('rule')}; {sug}; "
                f"{find.get('steps_run')} steps"
                + (f", stopped: {find['stop_reason']}" if find.get("stopped_early") else "")
                + f"); schedule {s['kind']} {s['total_steps']} steps, warmup {s['warmup_steps']}, "
                f"floor {s['floor_lr']:.3g}"
            )
            pre = (e.get("pretrain") or {}).get("lr_find")
            if pre:
                lines.append(f"  pretraining: lr {e['pretrain']['lr']:.3g} (rule {pre['rule']})")
    lines.append(f"curves written to {out_dir / 'lr'}")
    return "\n".join(lines)


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
    if args.command == "lr-find":
        from corvus_ml.eval.runner import find_learning_rates

        print(format_lr_find(find_learning_rates(cfg, args.out, steps=args.steps), args.out))
        return 0
    if args.command == "pretrain":
        from corvus_ml.eval.runner import pretrain_checkpoints

        print(json.dumps(pretrain_checkpoints(cfg, args.out), indent=2))
        return 0
    if args.command == "tune" and args.trials is not None:
        cfg.tune = replace(cfg.tune, trials=args.trials)
    report = run(cfg, args.out, tune=args.command == "tune")
    js, md = write_report(report, args.out)
    print(md.read_text())
    print(f"wrote {js} and {md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
