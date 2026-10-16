"""``evaluate`` and ``train``: run the evaluation harness on a snapshot's feature build.

``evaluate`` writes the reports to ``reports/<snapshot_id>/<job_id>/``. ``train`` does the same and
then, per game context, takes the best candidate, uploads its calibrated ONNX graph, the
``feature_spec.json``, ``calibration.json`` and ``model_card.json`` to
``models/<name>/<version>/`` and registers the model (stage ``registered``) with the metrics the
quality gate reads.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import math
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from corvus_ml.features.spec import SPEC_FILE, FeatureSpec
from corvus_ml.jobs import layout
from corvus_ml.jobs.context import JobContext
from corvus_ml.jobs.lifecycle import JobEnv, JobError, JobOutcome, json_safe
from corvus_ml.jobs.params import Params

log = logging.getLogger("corvus.job.model")

DEFAULT_CANDIDATES = ("isolation_forest", "gradient_boosting")
DEFAULT_SELECT_BY = "recall@fpr0.1pct"
DEFAULT_DOMAIN = "general"
# Registered with a candidate type of its own: the graph has the single output ``score`` and is
# already calibrated, unlike the raw sklearn graphs the scorer special-cases by candidate type.
CANDIDATE_TYPE_SUFFIX = "_calibrated"


def metric_key(name: str) -> str:
    """Harness name -> gate name: ``recall@fpr0.1pct`` becomes ``recall_at_fpr_0_1pct``."""
    return name.replace("@fpr", "_at_fpr_").replace(".", "_")


def registry_metrics(candidate: dict[str, Any]) -> dict[str, Any]:
    """The metrics registered with a model, named like the quality gate expects them."""
    out: dict[str, Any] = {}
    renames = {"n_windows": "test_windows", "n_cheat_windows": "test_cheat_windows"}
    for key, value in (candidate.get("test") or {}).items():
        out[renames.get(key, metric_key(key))] = value
    bench = candidate.get("bench") or {}
    for key, name in (
        ("latency_p50_ms", "p50_latency_ms"),
        ("latency_p99_ms", "p99_latency_ms"),
        ("latency_mean_ms", "mean_latency_ms"),
        ("batch_throughput_wps", "batch_throughput_wps"),
        ("peak_rss_mb", "peak_rss_mb"),
        ("file_size_kb", "model_size_kb"),
    ):
        if key in bench:
            out[name] = bench[key]
    out["n_params"] = candidate.get("n_params")
    out["fit_seconds"] = candidate.get("fit_seconds")
    return json_safe(out)


def choose_candidate(candidates: dict[str, dict[str, Any]], select_by: str) -> str | None:
    """Best usable candidate: highest ``select_by`` on test, ties by lower p99 latency."""

    def sort_key(item: tuple[str, dict[str, Any]]) -> tuple[float, float]:
        _, c = item
        metric = (c.get("test") or {}).get(select_by)
        metric = -math.inf if metric is None or math.isnan(metric) else float(metric)
        p99 = (c.get("bench") or {}).get("latency_p99_ms")
        return metric, -(p99 if p99 is not None else math.inf)

    usable = [
        (n, c)
        for n, c in candidates.items()
        if c.get("status") == "ok" and (c.get("onnx") or {}).get("parity_ok")
    ]
    return max(usable, key=sort_key)[0] if usable else None


RAW_WINDOW_KEYS = ("window_seconds", "stride_seconds", "max_packets", "overflow", "min_packets")


def _candidate_names(params: dict[str, Any]) -> list[str]:
    raw = params.get("candidates", list(DEFAULT_CANDIDATES))
    if isinstance(raw, list):
        return [n for n in raw if isinstance(n, str)]
    return [str(n) for n in raw] if isinstance(raw, dict) else []


def wants_raw(params: dict[str, Any]) -> bool:
    """True if the requested candidates read raw packet bytes (they need the ``raw/`` build)."""
    from corvus_ml.eval import candidates as cand_pkg

    kinds = {
        cand_pkg.REGISTRY[n].input_kind == "raw_bytes"
        for n in _candidate_names(params)
        if n in cand_pkg.REGISTRY
    }
    if len(kinds) > 1:
        raise JobError("raw-byte candidates need their own job: do not mix them with others")
    return kinds == {True}


def _tune_params(p: Params) -> dict[str, Any] | None:
    """``tune``: false / absent = off, true = defaults, an object = ``[tune]`` settings."""
    raw = p.raw("tune", False)
    if raw is False or raw is None:
        return None
    if raw is True:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    raise JobError("tune must be true, false or an object")


def _read_params(
    ctx: JobContext, spec: FeatureSpec, train: bool, raw_mode: bool = False
) -> dict[str, Any]:
    from corvus_ml.eval.config import BenchConfig

    p = Params(ctx.params)
    raw = p.raw("candidates", list(DEFAULT_CANDIDATES))
    if isinstance(raw, list) and all(isinstance(n, str) for n in raw):
        candidates = {n: {} for n in raw}
    elif isinstance(raw, dict) and all(isinstance(v, dict) for v in raw.values()):
        candidates = {str(n): dict(v) for n, v in raw.items()}
    else:
        raise JobError("candidates must be a list of names or an object name -> parameters")
    if not candidates:
        raise JobError("candidates must not be empty")
    from corvus_ml.eval.lr import parse_lr

    for cand_name, cand_params in candidates.items():
        for key in ("lr", "pretrain_lr"):
            if key in cand_params:
                try:
                    cand_params[key] = parse_lr(cand_params[key])
                except ValueError as exc:
                    raise JobError(f"candidates.{cand_name}.{key}: {exc}") from exc
    bench_raw = p.mapping("benchmark")
    known = {f.name for f in dataclasses.fields(BenchConfig)}
    if set(bench_raw) - known:
        raise JobError(f"unknown benchmark keys: {sorted(set(bench_raw) - known)}")
    out: dict[str, Any] = {
        "candidates": candidates,
        "contexts": p.game_contexts() or [],
        "unlabelled": p.string("unlabelled", "exclude"),
        "label_sources": p.strings("label_sources"),
        "min_confidence": p.number("min_confidence", 0.0, 0.0, 1.0),
        "seed": p.integer("seed", 1337, 0, 2**31 - 1),
        "threads": p.integer("threads", 1, 1, 8),
        "seq_len": p.integer("seq_len", spec.seq_len, 1, 100_000),
        "split": tuple(p.numbers("split", [0.6, 0.2, 0.2]) or ()),
        "fpr_targets": tuple(p.numbers("fpr_targets", [0.01, 0.001]) or ()),
        "learning_curve": tuple(p.numbers("learning_curve", []) or ()),
        "calibration": p.string("calibration", "auto"),
        "select_by": p.string("select_by", DEFAULT_SELECT_BY),
        "benchmark": BenchConfig(**bench_raw),
        "tune": _tune_params(p),
    }
    if raw_mode:
        macro = p.raw("macro_window_seconds")
        if macro is not None and (isinstance(macro, bool) or not isinstance(macro, (int, float))):
            raise JobError("macro_window_seconds must be a number")
        window: dict[str, Any] = {}  # only what the job sets overrides the build's window
        if p.has("window_seconds"):
            window["window_seconds"] = p.number("window_seconds", 10.0, 0.01, 86_400.0)
        if p.has("stride_seconds"):
            window["stride_seconds"] = p.number("stride_seconds", 5.0, 0.01, 86_400.0)
        if p.has("max_packets"):
            window["max_packets"] = p.integer("max_packets", 1024, 1, 100_000)
        if p.has("overflow"):
            window["overflow"] = p.string("overflow", "recent")
        if p.has("min_packets"):
            window["min_packets"] = p.integer("min_packets", 8, 1, 100_000)
        if macro is not None:
            window["macro_window_seconds"] = float(macro)
        out["raw_window"] = window
        out["max_windows"] = p.integer("max_windows", 200_000, 1, 10_000_000)
    if train:
        out["model_name"] = p.string("model_name")
        out["version"] = p.string("version")
        out["domain"] = p.string("domain", DEFAULT_DOMAIN)
    p.done()
    if out["unlabelled"] not in ("exclude", "assume_legit"):
        raise JobError("unlabelled must be 'exclude' or 'assume_legit'")
    if out["calibration"] == "none":
        raise JobError("jobs always calibrate: calibration 'none' is only for corvus-eval")
    if len(out["split"]) != 3 or abs(sum(out["split"]) - 1.0) > 1e-6:
        raise JobError("split must be three fractions that add up to 1")
    return out


def _filtered_labels(path: Path, sources: list[str] | None, min_confidence: float) -> Path | None:
    """Labels restricted to the requested sources and confidence; None if nothing remains."""
    if not path.is_file():
        return None
    table = pq.read_table(path)
    mask = pc.greater_equal(table["confidence"], pa.scalar(min_confidence))
    if sources:
        mask = pc.and_(mask, pc.is_in(table["source"], pa.array(sources, pa.string())))
    table = table.filter(mask)
    if table.num_rows == 0:
        return None
    target = path.with_name("labels.selected.parquet")
    pq.write_table(table, target)
    return target


def _tune_config(raw: dict[str, Any] | None):
    from corvus_ml.eval.config import TuneConfig

    if raw is None:
        return TuneConfig()
    known = {f.name for f in dataclasses.fields(TuneConfig)}
    if set(raw) - known:
        raise JobError(f"unknown tune keys: {sorted(set(raw) - known)}")
    return TuneConfig(**raw)


def _eval_config(
    ctx: JobContext, opts: dict[str, Any], features: Path, labels: Path | None, raw_mode=False
):
    from corvus_ml.eval.config import EvalConfig, RawEvalConfig

    if raw_mode:
        window = dict(opts["raw_window"])
        return EvalConfig(
            name=f"job-{ctx.job_id[:8]}",
            seed=opts["seed"],
            threads=opts["threads"],
            seq_len=opts["seq_len"],
            unlabelled=opts["unlabelled"],
            calibration=opts["calibration"],
            contexts=opts["contexts"],
            split=opts["split"],
            fpr_targets=opts["fpr_targets"],
            min_confidence=opts["min_confidence"],
            learning_curve=opts["learning_curve"],
            labels=labels,
            raw=RawEvalConfig(
                dir=features, window_overrides=window, max_windows=opts["max_windows"]
            ),
            candidates=opts["candidates"],
            bench=opts["benchmark"],
            tune=_tune_config(opts["tune"]),
        )
    tokens = features / "window_tokens"
    return EvalConfig(
        name=f"job-{ctx.job_id[:8]}",
        seed=opts["seed"],
        threads=opts["threads"],
        seq_len=opts["seq_len"],
        unlabelled=opts["unlabelled"],
        calibration=opts["calibration"],
        contexts=opts["contexts"],
        split=opts["split"],
        fpr_targets=opts["fpr_targets"],
        min_confidence=opts["min_confidence"],
        learning_curve=opts["learning_curve"],
        windows=features / "windows",
        tokens=tokens if tokens.is_dir() else None,
        sessions=features / layout.SESSIONS_FILE,
        labels=labels,
        candidates=opts["candidates"],
        bench=opts["benchmark"],
        tune=_tune_config(opts["tune"]),
    )


def _training_info(candidate: dict[str, Any]) -> dict[str, Any]:
    """Learning rate chosen for the candidate and why (rule, schedule, downsampled curve)."""
    t = candidate.get("training") or {}
    out = {k: t[k] for k in ("lr", "lr_plan", "pretrain_lr_plan") if t.get(k) is not None}
    return {"training": json_safe(out)} if out else {}


def _summary(info: dict[str, Any], chosen: str | None) -> dict[str, Any]:
    tuned = {
        name: {k: t[k] for k in ("n_trials", "n_complete", "n_pruned", "best_value", "best_params")}
        for name, t in (info.get("tune") or {}).items()
    }
    return {
        **({"tune": tuned} if tuned else {}),
        "chosen": chosen,
        "windows": info["windows"],
        "players": info["players"],
        "candidates": {
            name: (
                {"status": "ok", **registry_metrics(c), **_training_info(c)}
                if c["status"] == "ok"
                else {"status": c["status"], "reason": c.get("reason")}
            )
            for name, c in info["candidates"].items()
        },
    }


def _evaluate_and_maybe_train(ctx: JobContext, env: JobEnv, train: bool) -> JobOutcome:
    try:
        from corvus_ml.eval import candidates as cand_pkg
        from corvus_ml.eval.report import write_report
        from corvus_ml.eval.runner import run
    except ModuleNotFoundError as exc:
        raise JobError(f"the eval dependencies are missing from this image: {exc.name}") from None

    raw_mode = wants_raw(ctx.params)
    prefix = layout.features_prefix(ctx.snapshot_id)
    if raw_mode:
        prefix = f"{prefix}/raw"
    if not env.store.exists(f"{prefix}/{layout.BUILD_FILE}"):
        kind = ' with kind "raw_bytes"' if raw_mode else ""
        raise JobError(
            f"no complete feature build for this snapshot: run build_features{kind} first"
        )

    spec_bytes = env.store.get_bytes(f"{prefix}/{SPEC_FILE}")
    features = env.scratch / "features"
    features.mkdir(parents=True, exist_ok=True)
    (features / SPEC_FILE).write_bytes(spec_bytes)
    opts = _read_params(ctx, FeatureSpec.read(features / SPEC_FILE), train, raw_mode)
    unknown = sorted(set(opts["candidates"]) - set(cand_pkg.REGISTRY))
    if unknown:
        raise JobError(f"unknown candidates {unknown}; known: {sorted(cand_pkg.REGISTRY)}")
    need_seq = any(cand_pkg.REGISTRY[n].input_kind == "seq" for n in opts["candidates"])
    env.store.download_dir(
        prefix, features, skip=lambda rel: rel.startswith("window_tokens/") and not need_seq
    )
    if raw_mode:
        FeatureSpec.load(features / SPEC_FILE, require_tokenizer=True)  # hash of tokenizer.json
    labels = _filtered_labels(
        features / layout.LABELS_FILE, opts["label_sources"], opts["min_confidence"]
    )

    cfg = _eval_config(ctx, opts, features, labels, raw_mode)
    out = env.scratch / "eval"
    report = run(cfg, out, tune=opts["tune"] is not None)
    if not report["contexts"]:
        raise JobError("no windows in the requested game contexts")
    json_path, md_path = write_report(report, out)
    report_prefix = layout.reports_prefix(ctx.snapshot_id, ctx.job_id)
    env.store.put_file(f"{report_prefix}/report.json", json_path)
    env.store.put_file(f"{report_prefix}/report.md", md_path)

    chosen = {
        c: choose_candidate(info["candidates"], opts["select_by"])
        for c, info in report["contexts"].items()
    }
    result: dict[str, Any] = {
        "report_prefix": report_prefix,
        "features_prefix": prefix,
        "calibration": opts["calibration"],
        "select_by": opts["select_by"],
        "contexts": {c: _summary(i, chosen[c]) for c, i in report["contexts"].items()},
    }
    if not train:
        names = ", ".join(f"{c}: {n or 'none'}" for c, n in chosen.items())
        return JobOutcome(
            f"evaluated {len(report['contexts'])} game context(s); best {names}", result
        )

    models = []
    for context, name in chosen.items():
        if name is None:
            log.warning("context %s: no usable candidate, nothing registered", context)
            continue
        models.append(
            _publish(
                ctx, env, opts, context, name, report["contexts"][context], out / "onnx" / context
            )
        )
    if not models:
        raise JobError("no candidate produced a usable model in any game context")
    result["models"] = models
    return JobOutcome(
        "registered " + ", ".join(f"{m['name']} {m['version']}" for m in models), json_safe(result)
    )


def _publish(
    ctx: JobContext,
    env: JobEnv,
    opts: dict[str, Any],
    context: str,
    candidate: str,
    info: dict[str, Any],
    onnx_dir: Path,
) -> dict[str, Any]:
    from corvus_ml.eval import candidates as cand_pkg

    res = info["candidates"][candidate]
    cls = cand_pkg.REGISTRY[candidate]
    name = layout.safe_name(f"{opts.get('model_name') or candidate}-{context}")
    version = layout.safe_name(opts.get("version") or f"{ctx.snapshot_id[:8]}-{ctx.job_id[:8]}")
    prefix = layout.model_prefix(name, version)
    metrics = registry_metrics(res)
    calibration = json.loads((onnx_dir / f"{candidate}.calibration.json").read_text())

    meaning = (
        "calibrated estimate of P(cheating | window), fitted on the validation split"
        if cls.supervised
        else "share of legit validation windows with a lower raw score (empirical CDF): "
        "0.99 means higher than 99 % of legit windows"
    )
    card = json_safe(
        {
            "schema_version": 1,
            "name": name,
            "version": version,
            "candidate": candidate,
            "candidate_type": candidate + CANDIDATE_TYPE_SUFFIX,
            "game_context": context,
            "domain": opts["domain"],
            "snapshot_id": ctx.snapshot_id,
            "job_id": ctx.job_id,
            "created_at": env.clock().isoformat(),
            "input": cls.input_kind,
            "input_kind": cls.input_kind,
            "feature_spec": SPEC_FILE,
            **_raw_card(onnx_dir, info),
            "calibration": {
                k: calibration[k] for k in ("method", "kind", "n_fit", "fit_on") if k in calibration
            },
            "output_semantics": {
                "onnx_output": "score",
                "dtype": "float32",
                "range": [0.0, 1.0],
                "higher_means": "more likely cheating",
                "calibrated": True,
                "meaning": meaning,
                "post_processing": "none: the graph output is final, do not rescale or clip-map it",
            },
            "metrics": metrics,
        }
    )
    uploads = {
        "model.onnx": onnx_dir / f"{candidate}.onnx",
        SPEC_FILE: onnx_dir / SPEC_FILE,
        layout.CALIBRATION_FILE: onnx_dir / f"{candidate}.calibration.json",
    }
    if cls.input_kind == "raw_bytes":
        uploads["tokenizer.json"] = onnx_dir / "tokenizer.json"
    for target, source in uploads.items():
        env.store.put_file(f"{prefix}/{target}", source)
    env.store.put_bytes(f"{prefix}/{layout.MODEL_CARD_FILE}", json.dumps(card, indent=2).encode())
    env.store.put_bytes(
        f"{prefix}/{layout.EVALUATION_FILE}", json.dumps(json_safe(res), indent=2).encode()
    )

    onnx_key = f"{prefix}/{layout.MODEL_FILE}"
    registered = env.api.register_model(
        {
            "name": name,
            "version": version,
            "candidate_type": card["candidate_type"],
            "game_context": context,
            "domain": opts["domain"],
            "snapshot_id": ctx.snapshot_id,
            "metrics": metrics,
            "onnx_key": onnx_key,
        }
    )
    return {
        "id": registered.get("id"),
        "name": name,
        "version": version,
        "game_context": context,
        "candidate": candidate,
        "onnx_key": onnx_key,
    }


def _raw_card(onnx_dir: Path, info: dict[str, Any]) -> dict[str, Any]:
    """``model_card.json`` fields of a raw-byte model: tokenizer hash and window configuration."""
    spec = FeatureSpec.read(onnx_dir / SPEC_FILE).raw_bytes
    if spec is None:
        return {}
    return {
        "tokenizer": {"file": spec.tokenizer_file, "sha256": spec.tokenizer_sha256},
        "window": spec.window.__dict__,
        "packet": spec.packet.__dict__,
        "scorer_contract": "docs/reference/raw-byte-tokenizer.md",
    }


def evaluate(ctx: JobContext, env: JobEnv) -> JobOutcome:
    return _evaluate_and_maybe_train(ctx, env, train=False)


def train(ctx: JobContext, env: JobEnv) -> JobOutcome:
    return _evaluate_and_maybe_train(ctx, env, train=True)
