"""Peak memory of the raw-byte models on synthetic windows, one stage per fresh process.

Usage (from ``ml/``)::

    uv run python scripts/measure_raw_memory.py --packets 2048 --batch 16 --stage all

Stages: ``lr`` (LR range test), ``fit`` (training epoch incl. validation pass), ``score``
(anomaly score batches) and ``onnx`` (export + a session run over ``--onnx-rows`` rows).
Every stage runs in a child process so ``VmHWM`` (peak RSS) belongs to that stage alone; the number
printed is the growth over the process after the data was built. The data are random token ids, not
real traffic: attention memory depends on shapes, not on content. ``--micro N`` caps the rows per
forward pass (0: the whole batch at once). A dev tool, not a unit test.
"""

from __future__ import annotations

import argparse
import inspect
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

STAGES = ("lr", "fit", "score", "onnx")


def _hwm_mb() -> float:
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmHWM:"):
            return int(line.split()[1]) / 1024.0
    return 0.0


def _build(args):
    import numpy as np
    import torch

    from corvus_ml.raw.model import ModelConfig, RawAnomalyNet

    cfg = ModelConfig(
        vocab_size=args.vocab, max_tokens=args.tokens, n_special=16,
        d_model=args.d_model, layers=args.layers, heads=args.heads,
        local_dim=args.local_dim, local_layers=1, local_heads=2,
    )  # fmt: skip
    rng = np.random.default_rng(0)
    shape = (args.windows, args.packets, args.tokens)
    ids = rng.integers(16, args.vocab, size=shape).astype(np.int16)
    lengths = rng.integers(12, args.tokens + 1, size=(args.windows, args.packets, 1))
    ids[np.arange(args.tokens)[None, None, :] >= lengths] = 0
    t = np.sort(rng.random(shape[:2]).astype(np.float32) * 20.0, axis=1)
    torch.manual_seed(0)
    torch.set_num_threads(args.threads)
    return RawAnomalyNet(cfg), torch.from_numpy(ids), torch.from_numpy(t)


def _supported(fn, **kw):
    params = inspect.signature(fn).parameters
    return {k: v for k, v in kw.items() if k in params}


def run_stage(args) -> dict:
    import torch

    from corvus_ml.eval.lr import LrFindConfig, find_lr
    from corvus_ml.eval.training import fit

    net, ids, t = _build(args)

    def loss_fn(m, batch):
        return m.pretrain_loss(batch[0].long(), batch[1])

    base = _hwm_mb()
    started = time.perf_counter()
    if args.stage == "lr":
        extra = _supported(LrFindConfig, micro_batch=args.micro or None)
        find_lr(net, loss_fn, (ids, t), LrFindConfig(steps=3, batch_size=args.batch, **extra))
    elif args.stage == "fit":
        n_val = max(args.batch, args.windows // 10)
        kw = _supported(fit, micro_batch=args.micro or None, eval_batch=args.micro or 1024)
        fit(
            net, loss_fn, (ids[n_val:], t[n_val:]), (ids[:n_val], t[:n_val]),
            epochs=1, batch_size=args.batch, lr=1e-3, **kw,
        )  # fmt: skip
    elif args.stage == "score":
        net.eval()
        step = args.micro or args.batch
        with torch.no_grad():
            for i in range(0, args.windows, step):
                net.score_ids(ids[i : i + step].long(), t[i : i + step])
    else:
        import numpy as np

        from corvus_ml.eval import onnx_bench
        from corvus_ml.raw.model import RawHierNet

        hier = RawHierNet(net.cfg).eval()
        rows = min(args.onnx_rows, args.windows)
        x = torch.cat([ids[:rows].float(), t[:rows, :, None]], dim=-1)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.onnx"
            torch.onnx.export(
                hier, (x[:2],), str(path), input_names=["x"], output_names=["score"],
                dynamic_axes={"x": {0: "batch", 1: "packets"}, "score": {0: "batch"}},
                opset_version=17, dynamo=False,
            )  # fmt: skip
            sess = onnx_bench.make_session(path, args.threads)
            base = _hwm_mb()
            step = args.micro or rows
            for i in range(0, rows, step):
                onnx_bench.run_session(sess, np.ascontiguousarray(x[i : i + step].numpy()))
    return {
        "stage": args.stage, "peak_growth_mb": round(_hwm_mb() - base, 1),
        "peak_total_mb": round(_hwm_mb(), 1), "baseline_mb": round(base, 1),
        "seconds": round(time.perf_counter() - started, 1),
    }  # fmt: skip


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--stage", default="all", choices=("all", *STAGES))
    p.add_argument("--packets", type=int, default=2048)
    p.add_argument("--tokens", type=int, default=48)
    p.add_argument("--windows", type=int, default=64)
    p.add_argument("--batch", type=int, default=16, help="the batch the user configured")
    p.add_argument("--micro", type=int, default=0, help="micro-batch (0: none, whole batch)")
    p.add_argument("--vocab", type=int, default=4096)
    p.add_argument("--d-model", type=int, default=64)
    p.add_argument("--layers", type=int, default=2)
    p.add_argument("--heads", type=int, default=4)
    p.add_argument("--local-dim", type=int, default=32)
    p.add_argument("--threads", type=int, default=4)
    p.add_argument("--onnx-rows", type=int, default=64)
    p.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = p.parse_args()
    if args.child:
        print(json.dumps(run_stage(args)))
        return
    argv = [a for a in sys.argv[1:] if a not in ("all",)]
    for stage in STAGES if args.stage == "all" else (args.stage,):
        rest = [a for i, a in enumerate(argv) if a != "--stage" and argv[i - 1] != "--stage"]
        cmd = [sys.executable, __file__, *rest, "--stage", stage, "--child"]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        line = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        print(line or f"{stage}: failed rc={proc.returncode} {proc.stderr[-300:]}", flush=True)


if __name__ == "__main__":
    main()
