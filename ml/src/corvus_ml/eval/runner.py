"""Run the evaluation: split by player, fit each candidate per game_context, score, export,
benchmark. Produces a plain dict that :mod:`corvus_ml.eval.report` renders."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import replace
from pathlib import Path

import numpy as np

from corvus_ml import calibration as cal
from corvus_ml.eval import candidates as cand_pkg
from corvus_ml.eval import metrics as M
from corvus_ml.eval import onnx_bench
from corvus_ml.eval.candidates.base import Candidate
from corvus_ml.eval.capacity import annotate_capacity
from corvus_ml.eval.config import EvalConfig
from corvus_ml.eval.data import LABEL_CHEAT, LABEL_LEGIT, LABEL_UNLABELLED, EvalData
from corvus_ml.eval.dataset import build_eval_data, load_dataset
from corvus_ml.eval.lr import lr_kwargs
from corvus_ml.eval.split import group_split, subsample_groups
from corvus_ml.eval.synthetic import generate
from corvus_ml.features.spec import SPEC_FILE, FeatureSpec

log = logging.getLogger(__name__)

PARITY_RTOL, PARITY_ATOL = 1e-3, 1e-4
# Calibrated graphs: float32 interpolation next to a steep step of the calibration can differ
# from the float64 reference by a fraction of that step.
CALIBRATED_PARITY_ATOL = 2e-3


def _tag(fpr: float) -> str:
    return f"{fpr * 100:g}pct"


def load_raw_source(cfg: EvalConfig, workdir: Path):
    """The packet-level source of a raw-byte run (real table or synthetic)."""
    from corvus_ml.eval import raw_data
    from corvus_ml.raw import pipeline
    from corvus_ml.raw.synthetic import generate as generate_raw

    raw = cfg.raw
    assert raw is not None
    if raw.synthetic is not None:
        syn = generate_raw(replace(raw.synthetic, seed=raw.synthetic.seed or cfg.seed))
        return pipeline.synthetic_source(
            syn, workdir / "tokenizer", raw.tokenizer, raw.packet, raw.window()
        )
    assert raw.dir is not None
    base = raw_data.load_raw_source(raw.dir, labels=cfg.labels)
    return base.with_window(raw.window(base.spec.window))


def load_data(cfg: EvalConfig) -> tuple[EvalData, bool]:
    """Return the dataset and whether it is synthetic."""
    if cfg.raw is not None:
        import tempfile

        with tempfile.TemporaryDirectory(prefix="corvus-raw-") as tmp:
            source = load_raw_source(cfg, Path(tmp))
            data = source.build(cfg.min_confidence, cfg.raw.max_windows, cfg.seed)
            _keep_tokenizer(data)
            data.meta["raw_source"] = source
        return data, source.synthetic
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


def _keep_tokenizer(data: EvalData) -> None:
    """Copy the tokenizer into memory-independent storage: the temp dir is about to go."""
    directory = data.meta.get("tokenizer_dir")
    if directory is not None:
        data.meta["tokenizer_json"] = (Path(directory) / "tokenizer.json").read_bytes()


def _training_sets(
    cand_cls: type[Candidate], data: EvalData, idx_train: np.ndarray, idx_val: np.ndarray
) -> tuple[EvalData, EvalData]:
    """Supervised: labelled windows. Unsupervised: legit (or assumed legit) windows only."""

    def pick(idx: np.ndarray) -> EvalData:
        lab = data.label[idx]
        keep = (lab != LABEL_UNLABELLED) if cand_cls.supervised else (lab == LABEL_LEGIT)
        return data.take(idx[keep])

    return pick(idx_train), pick(idx_val)


EMPTY_SPLIT_HINT = "needs at least 2 players per label stratum"


def _empty_reason(part: str, idx: np.ndarray) -> str:
    """Why a split cannot be evaluated: it has no windows, or none that carry a label."""
    if len(idx) == 0:
        return f"{part} split empty: {EMPTY_SPLIT_HINT}"
    return f"{part} split has no labelled windows"


def _eval_metrics(cand: Candidate, data: EvalData, idx: np.ndarray, fprs) -> dict | None:
    """Metrics on the labelled windows of ``idx``; None (candidate not called) if there are none."""
    sub = data.take(idx[data.label[idx] != LABEL_UNLABELLED])
    if len(sub) == 0:
        return None
    y = (sub.label == LABEL_CHEAT).astype(np.int8)
    s = cand.calibrated_score(sub)
    out = M.window_metrics(y, s, sub.group_codes(), fprs)
    if cand.supervised:
        out["ece"] = M.ece(y, s)
    return out


def _calibrate(cand: Candidate, cfg: EvalConfig, train: EvalData, val: EvalData) -> None:
    """Fit the calibration on the validation split (supervised: labelled windows; unsupervised:
    legit windows). The shipped score is then ``cand.calibrated_score``."""
    if cfg.calibration == "none":
        return
    raw = cand.score(val) if len(val) else np.zeros(0)
    if cand.supervised:
        cand.calibration = cal.fit_calibration(
            cfg.calibration, True, raw, (val.label == LABEL_CHEAT).astype(np.int8),
            bounded=cand.bounded,
        )  # fmt: skip
    else:
        fallback = cand.score(train) if len(train) else None
        cand.calibration = cal.fit_calibration(cfg.calibration, False, raw, None, fallback)


def _fit(cand_name: str, cfg: EvalConfig, data: EvalData, train_idx, val_idx) -> Candidate | None:
    cls = cand_pkg.REGISTRY[cand_name]
    train, val = _training_sets(cls, data, train_idx, val_idx)
    if len(train) == 0:
        return None
    if cls.supervised and len(np.unique(train.label)) < 2:
        return None
    cand = cand_pkg.create(cand_name, cfg.seed, cfg.threads, cfg.candidates[cand_name])
    if cls.pretrains:
        # Masked pretraining sees every window of the training players, labelled or not.
        cand.fit(train, val if len(val) else None, pool=data.take(train_idx))
    else:
        cand.fit(train, val if len(val) else None)
    try:
        _calibrate(cand, cfg, train, val)
    except ValueError as exc:
        log.warning("%s: no calibration possible (%s); skipping the candidate", cand_name, exc)
        return None
    return cand


def _scores_and_labels(cand: Candidate, data: EvalData, idx: np.ndarray):
    sub = data.take(idx[data.label[idx] != LABEL_UNLABELLED])
    return (
        (sub.label == LABEL_CHEAT).astype(np.int8),
        cand.calibrated_score(sub),
        sub.group_codes(),
    )


def resolve_spec(cfg: EvalConfig) -> FeatureSpec:
    """The feature spec of the evaluated data.

    ``corvus-features build`` writes ``feature_spec.json`` next to its ``windows`` /
    ``window_tokens`` directories; it is validated against this checkout (a mismatch is an error).
    Without it (synthetic data, older builds) the default windowing parameters are assumed.
    """
    if cfg.raw is not None:
        return FeatureSpec.current(seq_len=cfg.seq_len)  # replaced by the source's spec in run()
    if cfg.windows is not None:
        candidate = cfg.windows.parent / SPEC_FILE
        if candidate.is_file():
            return replace(FeatureSpec.load(candidate), seq_len=cfg.seq_len)
        log.warning("no %s next to %s; assuming default window parameters", SPEC_FILE, cfg.windows)
    return FeatureSpec.current(seq_len=cfg.seq_len)


def _parity_windows(data: EvalData, split) -> tuple[EvalData, str]:
    """Real windows for the ONNX parity check and the benchmark: the labelled windows of the
    first non-empty split in the order test, val, train (then any train window, labelled or
    not). A tiny dataset may leave the held-out splits empty; the graph is still checked."""
    for part, idx in (("test", split.test), ("val", split.val), ("train", split.train)):
        labelled = idx[data.label[idx] != LABEL_UNLABELLED]
        if len(labelled):
            return data.take(labelled), part
    return data.take(split.train), "train"


def _export_and_bench(
    cand: Candidate,
    cfg: EvalConfig,
    data: EvalData,
    split,
    out: Path,
    fprs,
    spec: FeatureSpec,
) -> dict:
    res: dict = {}
    out.mkdir(parents=True, exist_ok=True)
    spec.write(out)  # the exported graphs are only meaningful together with this layout
    if spec.raw_bytes is not None and data.meta.get("tokenizer_json"):
        (out / spec.raw_bytes.tokenizer_file).write_bytes(data.meta["tokenizer_json"])
    test, parity_on = _parity_windows(data, split)
    if cand.parity_rows is not None:
        test = test.take(np.arange(min(len(test), cand.parity_rows)))
    if len(test) == 0:
        return {"error": "no windows to check the ONNX graph on"}
    x = cand.inputs(test)
    shipped = out / f"{cand.name}.onnx"
    native_raw = cand.score(test)
    if cand.calibration is None:
        path = raw_path = cand.export_onnx(shipped, x)
        native, atol = native_raw, PARITY_ATOL
    else:
        path = cand.export_calibrated_onnx(shipped, x)
        raw_path = path.with_name(f"{cand.name}.raw.onnx")
        native, atol = cand.calibrated_score(test), CALIBRATED_PARITY_ATOL
        (out / f"{cand.name}.calibration.json").write_text(
            json.dumps(cand.calibration.to_dict(), indent=2) + "\n"
        )
        raw_scores = cand.onnx_to_score(
            onnx_bench.run_session(onnx_bench.make_session(raw_path, cfg.threads), x)
        )
        res["onnx_raw"] = {
            "path": raw_path.name,
            "max_abs_diff": float(np.abs(raw_scores - native_raw).max()),
        }
    sess = onnx_bench.make_session(path, cfg.threads)
    outputs = onnx_bench.run_session(sess, x)
    # A calibrated graph has the single output ``score``; a raw one is candidate specific.
    onnx_scores = (
        cand.onnx_to_score(outputs)
        if cand.calibration is None
        else np.asarray(outputs[0], dtype=np.float64)
    )
    res["onnx"] = {
        "path": str(path.name),
        "calibrated": cand.calibration is not None,
        "max_abs_diff": float(np.abs(onnx_scores - native).max()),
        "parity_ok": bool(np.allclose(onnx_scores, native, rtol=PARITY_RTOL, atol=atol)),
        "parity_on": parity_on,
    }
    if not cfg.bench.enabled:
        return res
    bench = onnx_bench.benchmark_isolated if cfg.bench.isolated else onnx_bench.benchmark
    kw = {"warmup": cfg.bench.warmup, "iters": cfg.bench.iters, "batch": cfg.bench.batch}
    res["bench"] = bench(path, x, cfg.threads, **kw).to_dict()
    if cand.input_kind == "raw_bytes" and cfg.bench.raw_packets:
        res["bench_sizes"] = _bench_sizes(path, x, cfg, kw)
    stride = spec.raw_bytes.window.stride_seconds if spec.raw_bytes is not None else None
    annotate_capacity(res, stride, cfg.threads)
    if cand.quantizable and cfg.bench.quantize:
        res["int8"] = _quantized(cand, cfg, x, test, raw_path, native_raw, fprs, bench, kw)
    return res


def _bench_sizes(path: Path, x: np.ndarray, cfg: EvalConfig, kw: dict) -> dict:
    """Single-window latency with 1, 128, ... packets: real rows tiled to the requested count."""
    out = {}
    # The real (non-padding) packets of the fullest window, repeated up to the requested count.
    first = x[np.argmax((x[..., 0] != 0).reshape(len(x), -1).sum(1))]
    flat = first.reshape(-1, first.shape[-1]) if first.ndim == 3 else first
    real = flat[flat[:, 0] != 0]
    for count in cfg.bench.raw_packets:
        rows = real[np.arange(count) % len(real)]
        tiled = np.broadcast_to(rows, first.shape[:-2] + rows.shape)[None].copy()
        sized = dict(kw, batch=1, iters=max(10, kw["iters"] // 4), warmup=min(kw["warmup"], 5))
        bench = onnx_bench.benchmark_isolated if cfg.bench.isolated else onnx_bench.benchmark
        out[str(count)] = bench(path, tiled, cfg.threads, **sized).to_dict()
    return out


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
        if m is None:
            return {}
        keys = ["roc_auc", "pr_auc", *[f"recall@fpr{_tag(f)}" for f in fprs]]
        return {
            "n_train_players": float(np.unique(groups[sub]).size),
            "n_train_windows": float(sub.size),
            **{k: m[k] for k in keys},
        }

    return M.learning_curve(cfg.learning_curve, run)


def run_candidate(
    name: str,
    cfg: EvalConfig,
    data: EvalData,
    split,
    out_dir: Path,
    ctx: str,
    spec: FeatureSpec | None = None,
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
    if cand.calibration is not None:
        c = cand.calibration
        res["calibration"] = {"method": c.method, "n_fit": c.n_fit, "fit_on": c.fit_on}
    for part, idx in (("test", split.test), ("val", split.val)):
        res[part] = _eval_metrics(cand, data, idx, fprs)
        if res[part] is None:
            res[f"{part}_skipped"] = _empty_reason(part, idx)
            log.warning("%s: %s; no %s metrics", name, res[f"{part}_skipped"], part)
    res["confusion_at_val_threshold"] = {}
    if res["test"] is not None and res["val"] is not None:
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
            "lr_plan": cand.history.lr_plan,
        }
        pre = getattr(cand, "pretrain_history", None)
        if pre is not None and pre.lr_plan is not None:
            res["training"]["pretrain_lr_plan"] = pre.lr_plan
    spec = spec or FeatureSpec.current(seq_len=cfg.seq_len)
    res.update(_export_and_bench(cand, cfg, data, split, out_dir / "onnx" / ctx, fprs, spec))
    if cfg.learning_curve and res["test"] is not None:
        res["learning_curve"] = _learning_curve(name, cfg, data, split, fprs)
    return res


def _apply_tuned(name, best, cfg, data, split, spec):
    """Config / data / split / spec with the best parameters of a study applied."""
    from corvus_ml.eval import tune as tuner

    window_keys = set(tuner.WINDOW_SPACE)
    model_params = {k: v for k, v in best.items() if k not in window_keys}
    new_cfg = replace(
        cfg, candidates={**cfg.candidates, name: {**cfg.candidates[name], **model_params}}
    )
    source = data.meta.get("raw_source")
    if source is None or not (window_keys & set(best)):
        return new_cfg, data, split, spec
    window = tuner.window_for(data.meta["raw_spec"].window, best)
    rebuilt = source.with_window(window).build(cfg.min_confidence, cfg.raw.max_windows, cfg.seed)
    ctx = data.game_context[0]
    rebuilt = rebuilt.take(np.flatnonzero(rebuilt.game_context == ctx))
    rebuilt.meta["raw_source"] = source
    rebuilt.meta["tokenizer_json"] = data.meta.get("tokenizer_json")
    if cfg.unlabelled == "assume_legit":
        rebuilt.label[rebuilt.label == LABEL_UNLABELLED] = LABEL_LEGIT
    new_split = tuner.split_like(rebuilt, tuner.player_parts(data, split))
    return new_cfg, rebuilt, new_split, FeatureSpec.current(raw_bytes=rebuilt.meta["raw_spec"])


def pretrain_checkpoints(cfg: EvalConfig, out_dir: Path) -> dict:
    """Masked pretraining on the unlabelled windows of the training players, one checkpoint per
    game context (``<out>/pretrain/<context>.pt``). The model shape comes from the parameters of
    the first raw-byte candidate; finetune with ``init_from = "<checkpoint>"``."""
    from corvus_ml.eval.training import seed_everything
    from corvus_ml.raw.model import RawAnomalyNet
    from corvus_ml.raw.pretrain import flatten_windows, pretrain, save_checkpoint

    if cfg.raw is None:
        raise ValueError("pretraining needs a [raw] table")
    seed_everything(cfg.seed, cfg.threads)
    data, _ = load_data(cfg)
    name = next(iter(cfg.candidates))
    params = dict(cfg.candidates[name])
    out: dict = {}
    for ctx in cfg.contexts or sorted(set(data.game_context.tolist())):
        sub = data.take(np.flatnonzero(data.game_context == ctx))
        if len(sub) == 0:
            continue
        split = group_split(sub.label, sub.group_codes(), cfg.seed, cfg.split)
        pool = sub.take(split.train)
        cand = cand_pkg.create(name, cfg.seed, cfg.threads, params)
        model_cfg = cand.model_config(sub)  # type: ignore[attr-defined]
        net = RawAnomalyNet(replace(model_cfg, macro_windows=1))
        ids, t = flatten_windows(pool.raw_ids, pool.raw_time)  # type: ignore[arg-type]
        batch = int(params.get("batch_size", 16))
        wd = float(params.get("weight_decay", 0.01))
        hist = pretrain(
            net, ids, t, epochs=int(params.get("pretrain_epochs", 5)), batch_size=batch,
            weight_decay=wd, patience=int(params.get("patience", 3)), seed=cfg.seed,
            **lr_kwargs(params, prefix="pretrain_", default_lr=1e-3, batch_size=batch,
                        seed=cfg.seed, weight_decay=wd),
        )  # fmt: skip
        path = save_checkpoint(out_dir / "pretrain" / f"{_safe(ctx)}.pt", net)
        out[ctx] = {
            "checkpoint": str(path),
            "windows": int(len(ids)),
            "lr": hist.lr_plan,
            "epochs_run": len(hist.train_loss),
            "train_loss": hist.train_loss,
            "val_loss": hist.val_loss,
        }
    return out


def find_learning_rates(cfg: EvalConfig, out_dir: Path, *, steps: int | None = None) -> dict:
    """Range test for every torch candidate and game context; nothing is trained.

    Each candidate is "fitted" with ``lr = "auto"`` and zero epochs, so the finder sees exactly the
    model, loss and training windows (and batch size) a real run would. The schedule is computed
    for the configured number of epochs. Writes ``lr/<context>__<candidate>.json`` (and ``.csv``
    with the curve) below ``out_dir``.
    """
    from corvus_ml.eval.lr import AUTO_FLOOR_FRAC, LrSchedule, total_steps, warmup_steps_for
    from corvus_ml.eval.training import seed_everything

    seed_everything(cfg.seed, cfg.threads)
    data, _ = load_data(cfg)
    if cfg.unlabelled == "assume_legit":
        data.label[data.label == LABEL_UNLABELLED] = LABEL_LEGIT
    out: dict = {}
    for ctx in cfg.contexts or sorted(set(data.game_context.tolist())):
        sub = data.take(np.flatnonzero(data.game_context == ctx))
        if len(sub) == 0:
            continue
        split = group_split(sub.label, sub.group_codes(), cfg.seed, cfg.split)
        for name, params in cfg.candidates.items():
            cls = cand_pkg.REGISTRY[name]
            if not cls.tunable:  # torch candidates only: sklearn models have no learning rate
                continue
            train, val = _training_sets(cls, sub, split.train, split.val)
            if len(train) == 0 or (cls.supervised and len(np.unique(train.label)) < 2):
                continue
            epochs = int(params.get("epochs", 10))
            probe = {**params, "lr": "auto", "epochs": 0, "pretrain_epochs": 0}
            if steps is not None:
                probe["lr_steps"] = steps
            probe.pop("init_from", None)
            cand = cand_pkg.create(name, cfg.seed, cfg.threads, probe)
            if cls.pretrains:
                cand.fit(train, val if len(val) else None, pool=sub.take(split.train))
            else:
                cand.fit(train, val if len(val) else None)
            entry = dict(cand.history.lr_plan)
            n_tr = len(train)
            batch = int(params.get("batch_size", 16 if cls.pretrains else 64))
            total = total_steps(n_tr, batch, epochs)
            peak = cand.lr_used
            entry["schedule"] = LrSchedule(
                total, warmup_steps_for(total), peak, peak * AUTO_FLOOR_FRAC,
                str(params.get("lr_schedule", "cosine")),
            ).to_dict()  # fmt: skip
            entry["train_windows"] = n_tr
            if cls.pretrains and int(params.get("pretrain_epochs", 0)) > 0:
                pool = sub.take(split.train)
                pre_params = {**params, "pretrain_lr": "auto"}
                pre_cand = cand_pkg.create(name, cfg.seed, cfg.threads, pre_params)
                entry["pretrain"] = pre_cand.probe_pretrain_lr(train, pool)  # type: ignore[attr-defined]
            out.setdefault(ctx, {})[name] = entry
    write_lr_find(out, out_dir)
    return out


def write_lr_find(found: dict, out_dir: Path) -> list[Path]:
    """One JSON and one CSV (``lr,smoothed_loss``) per (context, candidate); plus a PNG if
    matplotlib happens to be installed (it is not a dependency)."""
    target = out_dir / "lr"
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for ctx, per_cand in found.items():
        for name, entry in per_cand.items():
            stem = f"{_safe(ctx)}__{name}"
            js = target / f"{stem}.json"
            js.write_text(json.dumps(entry, indent=2))
            written.append(js)
            curve = entry.get("lr_find", {}).get("curve", [])
            csv = target / f"{stem}.csv"
            csv.write_text(
                "lr,smoothed_loss\n" + "".join(f"{lr:.8g},{loss:.8g}\n" for lr, loss in curve)
            )
            written.append(csv)
            png = _plot_curve(target / f"{stem}.png", curve, entry)
            if png is not None:
                written.append(png)
    return written


def _plot_curve(path: Path, curve: list, entry: dict) -> Path | None:
    try:
        import matplotlib  # not a dependency: plot only if present

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ModuleNotFoundError:
        return None
    if not curve:
        return None
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.plot([c[0] for c in curve], [c[1] for c in curve])
    ax.axvline(entry["lr"], color="tab:red", linestyle="--", label=f"lr {entry['lr']:.2g}")
    ax.set_xscale("log")
    ax.set_xlabel("learning rate")
    ax.set_ylabel("smoothed loss")
    ax.legend()
    fig.savefig(path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return path


def _safe(text: str) -> str:
    return "".join(c if c.isalnum() or c in "_.-" else "_" for c in text) or "context"


def run(cfg: EvalConfig, out_dir: Path, tune: bool = False) -> dict:
    """Evaluate every configured candidate in every game context; returns the report dict.

    With ``tune`` the tunable candidates first get an Optuna study (``cfg.tune``) on the train /
    val split; the best parameters (and, for raw-byte models, the best window) are then used for
    the evaluation on the test split.
    """
    from corvus_ml.eval import tune as tuner
    from corvus_ml.eval.training import seed_everything

    seed_everything(cfg.seed, cfg.threads)
    unknown = [n for n in cfg.candidates if n not in cand_pkg.REGISTRY]
    if unknown:
        raise ValueError(f"unknown candidates {unknown}; known: {sorted(cand_pkg.REGISTRY)}")
    spec = resolve_spec(cfg)
    data, synthetic = load_data(cfg)
    if cfg.raw is not None:
        spec = FeatureSpec.current(raw_bytes=data.meta["raw_spec"])
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
        "latency_budget_ms": cfg.bench.latency_budget_ms,
        "contexts": {},
    }
    if cfg.raw is not None:
        report["raw_bytes"] = {
            "tokenizer_sha256": spec.raw_bytes.tokenizer_sha256,  # type: ignore[union-attr]
            "vocab_size": spec.raw_bytes.vocab_size,  # type: ignore[union-attr]
            "tokenizer_mode": spec.raw_bytes.tokenizer_mode,  # type: ignore[union-attr]
            "window": spec.raw_bytes.window.__dict__,  # type: ignore[union-attr]
            "packet": spec.raw_bytes.packet.__dict__,  # type: ignore[union-attr]
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
            c_cfg, c_data, c_split, c_spec = cfg, sub, split, spec
            if tune and cand_pkg.REGISTRY[name].tunable:
                summary = tuner.tune_candidate(name, cfg, ctx, sub, split, out_dir / "tune")
                info.setdefault("tune", {})[name] = summary
                c_cfg, c_data, c_split, c_spec = _apply_tuned(
                    name, summary["best_params"], cfg, sub, split, spec
                )
            info["candidates"][name] = run_candidate(
                name, c_cfg, c_data, c_split, out_dir, ctx, c_spec
            )
        report["contexts"][ctx] = info
    return report
