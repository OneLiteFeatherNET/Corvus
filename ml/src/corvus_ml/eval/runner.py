"""Run the evaluation: split by player, fit each candidate per game_context, score, export,
benchmark. Produces a plain dict that :mod:`corvus_ml.eval.report` renders."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import numpy as np

from corvus_ml.eval import candidates as cand_pkg
from corvus_ml.eval import metrics as M
from corvus_ml.eval import onnx_bench
from corvus_ml.eval.candidates.base import Candidate
from corvus_ml.eval.config import EvalConfig
from corvus_ml.eval.data import LABEL_CHEAT, LABEL_LEGIT, LABEL_UNLABELLED, EvalData
from corvus_ml.eval.dataset import build_eval_data, load_dataset
from corvus_ml.eval.split import group_split, subsample_groups
from corvus_ml.eval.synthetic import generate

log = logging.getLogger(__name__)

PARITY_RTOL, PARITY_ATOL = 1e-3, 1e-4


def _tag(fpr: float) -> str:
    return f"{fpr * 100:g}pct"


def load_data(cfg: EvalConfig) -> tuple[EvalData, bool]:
    """Return the dataset and whether it is synthetic."""
    need_seq = any(cand_pkg.REGISTRY[n].input_kind == "seq" for n in cfg.candidates)
    if cfg.synthetic is not None:
        t = generate(cfg.synthetic)
        data = build_eval_data(
            t.windows,
            t.tokens if need_seq else None,
            t.sessions,
            t.labels,
            cfg.seq_len,
            cfg.min_confidence,
        )
        return data, True
    assert cfg.windows is not None
    assert cfg.sessions is not None
    if need_seq and cfg.tokens is None:
        raise ValueError("a sequence candidate is configured but data.tokens is missing")
    data = load_dataset(
        cfg.windows,
        cfg.tokens if need_seq else None,
        cfg.sessions,
        cfg.labels,
        cfg.seq_len,
        cfg.min_confidence,
    )
    return data, False


def _training_sets(
    cand_cls: type[Candidate], data: EvalData, idx_train: np.ndarray, idx_val: np.ndarray
) -> tuple[EvalData, EvalData]:
    """Supervised: labelled windows. Unsupervised: legit (or assumed legit) windows only."""

    def pick(idx: np.ndarray) -> EvalData:
        lab = data.label[idx]
        keep = (lab != LABEL_UNLABELLED) if cand_cls.supervised else (lab == LABEL_LEGIT)
        return data.take(idx[keep])

    return pick(idx_train), pick(idx_val)


def _eval_metrics(cand: Candidate, data: EvalData, idx: np.ndarray, fprs) -> dict:
    sub = data.take(idx[data.label[idx] != LABEL_UNLABELLED])
    y = (sub.label == LABEL_CHEAT).astype(np.int8)
    s = cand.score(sub)
    out = M.window_metrics(y, s, sub.group_codes(), fprs)
    if cand.supervised:
        out["ece"] = M.ece(y, s)
    return out


def _fit(cand_name: str, cfg: EvalConfig, data: EvalData, train_idx, val_idx) -> Candidate | None:
    cls = cand_pkg.REGISTRY[cand_name]
    train, val = _training_sets(cls, data, train_idx, val_idx)
    if len(train) == 0:
        return None
    if cls.supervised and len(np.unique(train.label)) < 2:
        return None
    cand = cand_pkg.create(cand_name, cfg.seed, cfg.threads, cfg.candidates[cand_name])
    cand.fit(train, val if len(val) else None)
    return cand


def _scores_and_labels(cand: Candidate, data: EvalData, idx: np.ndarray):
    sub = data.take(idx[data.label[idx] != LABEL_UNLABELLED])
    return (sub.label == LABEL_CHEAT).astype(np.int8), cand.score(sub), sub.group_codes()


def _export_and_bench(
    cand: Candidate, cfg: EvalConfig, data: EvalData, test_idx: np.ndarray, out: Path, fprs
) -> dict:
    res: dict = {}
    out.mkdir(parents=True, exist_ok=True)
    test = data.take(test_idx[data.label[test_idx] != LABEL_UNLABELLED])
    if len(test) == 0:
        return {"error": "no labelled test windows"}
    x = cand.inputs(test)
    path = cand.export_onnx(out / f"{cand.name}.onnx", x)
    sess = onnx_bench.make_session(path, cfg.threads)
    onnx_scores = cand.onnx_to_score(onnx_bench.run_session(sess, x))
    native = cand.score(test)
    res["onnx"] = {
        "path": str(path.name),
        "max_abs_diff": float(np.abs(onnx_scores - native).max()),
        "parity_ok": bool(np.allclose(onnx_scores, native, rtol=PARITY_RTOL, atol=PARITY_ATOL)),
    }
    if not cfg.bench.enabled:
        return res
    bench = onnx_bench.benchmark_isolated if cfg.bench.isolated else onnx_bench.benchmark
    kw = {"warmup": cfg.bench.warmup, "iters": cfg.bench.iters, "batch": cfg.bench.batch}
    res["bench"] = bench(path, x, cfg.threads, **kw).to_dict()
    if cand.quantizable and cfg.bench.quantize:
        res["int8"] = _quantized(cand, cfg, x, test, path, native, fprs, bench, kw)
    return res


def _quantized(cand, cfg, x, test, path, native, fprs, bench, kw) -> dict:
    try:
        qpath = onnx_bench.quantize_int8(path, path.with_name(f"{cand.name}.int8.onnx"))
        sess = onnx_bench.make_session(qpath, cfg.threads)
        q = cand.onnx_to_score(onnx_bench.run_session(sess, x))
        y = (test.label == LABEL_CHEAT).astype(np.int8)
        groups = test.group_codes()
        full = M.window_metrics(y, native, groups, fprs)
        quant = M.window_metrics(y, q, groups, fprs)
        keys = ["roc_auc", "pr_auc", *[f"recall@fpr{_tag(f)}" for f in fprs]]
        return {
            "max_abs_diff": float(np.abs(q - native).max()),
            "metrics": {k: quant[k] for k in keys},
            "delta": {k: quant[k] - full[k] for k in keys},
            "bench": bench(qpath, x, cfg.threads, **kw).to_dict(),
            "file_size_kb": qpath.stat().st_size / 1024.0,
        }
    except Exception as exc:  # quantisation support differs per operator set
        return {"error": f"{type(exc).__name__}: {exc}"}


def _learning_curve(name, cfg, data, split, fprs) -> list[dict]:
    groups = data.group_codes()

    def run(frac: float) -> dict[str, float]:
        sub = subsample_groups(data.label, groups, split.train, frac, cfg.seed)
        cand = _fit(name, cfg, data, sub, split.val)
        if cand is None:
            return {}
        m = _eval_metrics(cand, data, split.test, fprs)
        keys = ["roc_auc", "pr_auc", *[f"recall@fpr{_tag(f)}" for f in fprs]]
        return {
            "n_train_players": float(np.unique(groups[sub]).size),
            "n_train_windows": float(sub.size),
            **{k: m[k] for k in keys},
        }

    return M.learning_curve(cfg.learning_curve, run)


def run_candidate(
    name: str, cfg: EvalConfig, data: EvalData, split, out_dir: Path, ctx: str
) -> dict:
    fprs = cfg.fpr_targets
    cls = cand_pkg.REGISTRY[name]
    base = {"supervised": cls.supervised, "input": cls.input_kind}
    t0 = time.perf_counter()
    cand = _fit(name, cfg, data, split.train, split.val)
    if cand is None:
        reason = (
            "needs labelled windows of both classes in the training split"
            if cls.supervised
            else "no legit training windows (set run.unlabelled = 'assume_legit' or add labels)"
        )
        return {**base, "status": "skipped", "reason": reason}
    fit_s = time.perf_counter() - t0
    res = {**base, "status": "ok", "n_params": cand.n_params, "fit_seconds": fit_s}
    res["test"] = _eval_metrics(cand, data, split.test, fprs)
    res["val"] = _eval_metrics(cand, data, split.val, fprs)
    yv, sv, _ = _scores_and_labels(cand, data, split.val)
    yt, st, _ = _scores_and_labels(cand, data, split.test)
    res["confusion_at_val_threshold"] = {
        _tag(f): M.confusion_at_val_threshold(yv, sv, yt, st, f) for f in fprs
    }
    if hasattr(cand, "history"):
        res["training"] = {
            "best_epoch": cand.history.best_epoch,
            "epochs_run": len(cand.history.train_loss),
            "lr": cand.lr_used,
            "lr_range_suggested": cand.lr_range.suggested_lr if cand.lr_range else None,
        }
    res.update(_export_and_bench(cand, cfg, data, split.test, out_dir / "onnx" / ctx, fprs))
    if cfg.learning_curve:
        res["learning_curve"] = _learning_curve(name, cfg, data, split, fprs)
    return res


def run(cfg: EvalConfig, out_dir: Path) -> dict:
    """Evaluate every configured candidate in every game context; returns the report dict."""
    from corvus_ml.eval.training import seed_everything

    seed_everything(cfg.seed, cfg.threads)
    unknown = [n for n in cfg.candidates if n not in cand_pkg.REGISTRY]
    if unknown:
        raise ValueError(f"unknown candidates {unknown}; known: {sorted(cand_pkg.REGISTRY)}")
    data, synthetic = load_data(cfg)
    assumed = 0
    if cfg.unlabelled == "assume_legit":
        mask = data.label == LABEL_UNLABELLED
        assumed = int(mask.sum())
        data.label[mask] = LABEL_LEGIT
    contexts = cfg.contexts or sorted(set(data.game_context.tolist()))
    report: dict = {
        "name": cfg.name,
        "synthetic": synthetic,
        "seed": cfg.seed,
        "threads": cfg.threads,
        "unlabelled": cfg.unlabelled,
        "assumed_legit_windows": assumed,
        "fpr_targets": list(cfg.fpr_targets),
        "split": list(cfg.split),
        "contexts": {},
    }
    for ctx in contexts:
        sub = data.take(np.flatnonzero(data.game_context == ctx))
        if len(sub) == 0:
            continue
        groups = sub.group_codes()
        split = group_split(sub.label, groups, cfg.seed, cfg.split)
        info = {
            "windows": len(sub),
            "players": int(np.unique(groups).size),
            "labelled_windows": int(sub.labelled_mask().sum()),
            "cheat_windows": int((sub.label == LABEL_CHEAT).sum()),
            "split": {
                k: {
                    "windows": int(v.size),
                    "players": int(np.unique(groups[v]).size),
                    "cheat_players": int(np.unique(groups[v][sub.label[v] == LABEL_CHEAT]).size),
                }
                for k, v in split.parts().items()
            },
            "candidates": {},
        }
        for name in cfg.candidates:
            log.info("context %s: %s", ctx, name)
            info["candidates"][name] = run_candidate(name, cfg, sub, split, out_dir, ctx)
        report["contexts"][ctx] = info
    return report
