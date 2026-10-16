"""Write ``report.json`` and ``report.md`` for a run."""

from __future__ import annotations

import json
import math
from pathlib import Path

SYNTHETIC_BANNER = (
    "> **SYNTHETIC DATA.** These numbers come from the toy generator in "
    "`corvus_ml.eval.synthetic` (raw packets: `corvus_ml.raw.synthetic`). They show that the "
    "pipeline runs end to end and say nothing about performance on real players."
)


def _f(v, digits: int = 3) -> str:
    if v is None:
        return "-"
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return "n/a"
    return f"{v:.{digits}f}"


def _within(p99, budget) -> str:
    return "-" if p99 is None else ("yes" if p99 <= budget else "NO")


def _tag(fpr: float) -> str:
    return f"{fpr * 100:g}pct"


def _json_default(o):
    if hasattr(o, "item"):
        return o.item()
    raise TypeError(f"not serialisable: {type(o).__name__}")


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return out


def to_markdown(report: dict) -> str:
    fprs = report["fpr_targets"]
    lines = [f"# Evaluation report: {report['name']}", ""]
    if report["synthetic"]:
        lines += [SYNTHETIC_BANNER, ""]
    lines += [
        f"Seed {report['seed']}, threads {report['threads']}, unlabelled windows: "
        f"{report['unlabelled']} ({report['assumed_legit_windows']} assumed legit). "
        "Metrics are on the test split, which is split by player. `R@x%` is recall at a "
        "false-positive rate of x% on the same score set; `player` aggregates the max score "
        "per player.",
        "",
    ]
    if "raw_bytes" in report:
        raw = report["raw_bytes"]
        w = raw["window"]
        lines += [
            f"Raw-byte input: tokenizer `{raw['tokenizer_sha256'][:12]}` "
            f"({raw.get('tokenizer_mode', 'bpe')}, vocab {raw['vocab_size']}), "
            f"windows of {w['window_seconds']:g} s every "
            f"{w['stride_seconds']:g} s, at most "
            f"{w['max_packets']} packets ({w['overflow']}), "
            f"{raw['packet']['max_tokens_per_packet']} tokens per packet"
            + (
                f", macro windows of {w['macro_window_seconds']:g} s."
                if w["macro_window_seconds"]
                else "."
            ),
            "",
        ]
    for ctx, info in report["contexts"].items():
        lines += [f"## game_context `{ctx}`", ""]
        sp = info["split"]
        lines += [
            f"{info['windows']} windows, {info['players']} players "
            f"({info['cheat_windows']} cheat windows). "
            + ", ".join(
                f"{k}: {v['players']} players / {v['windows']} windows / "
                f"{v['cheat_players']} cheat players"
                for k, v in sp.items()
            ),
            "",
        ]
        head = ["candidate", "params", "ROC-AUC", "PR-AUC"]
        for f in fprs:
            head += [f"R@{f * 100:g}% win", f"R@{f * 100:g}% player"]
        budget = report.get("latency_budget_ms")
        head += ["ECE", "p50 ms", "p99 ms", "players/core"]
        head += [f"p99 <= {budget:g} ms"] if budget else []
        head += ["batch w/s", "RSS MB", "ONNX diff"]
        rows = []
        for name, c in info["candidates"].items():
            if c["status"] != "ok":
                rows.append([name, *["skipped"] + ["-"] * (len(head) - 2)])
                continue
            b, o = c.get("bench", {}), c.get("onnx", {})
            t = c["test"] or {}  # empty test split: the metric cells stay empty
            row = [name, str(c["n_params"]), _f(t.get("roc_auc")), _f(t.get("pr_auc"))]
            for f in fprs:
                tag = _tag(f)
                low = "" if t.get(f"resolved@fpr{tag}", True) else "*"
                row += [
                    _f(t.get(f"recall@fpr{tag}")) + low,
                    _f(t.get(f"recall@fpr{tag}_player_max")) + low,
                ]
            row += [
                _f(t.get("ece")),
                _f(b.get("latency_p50_ms"), 3),
                _f(b.get("latency_p99_ms"), 3),
                _f(b.get("players_per_core"), 0),
                *([_within(b.get("latency_p99_ms"), budget)] if budget else []),
                _f(b.get("batch_throughput_wps"), 0),
                _f(b.get("peak_rss_mb"), 0),
                _f(o.get("max_abs_diff"), 6),
            ]
            rows.append(row)
        lines += _table(head, rows)
        lines += [
            "",
            "`*` fewer than 1 / FPR negatives in the test split: the operating point is not "
            "resolved, read the value as an upper bound of what the sample can show.",
            "",
        ]
        skipped = [
            f"- `{n}`: {c['reason']}" for n, c in info["candidates"].items() if c["status"] != "ok"
        ]
        if skipped:
            lines += ["Skipped:", *skipped, ""]
        no_test = [
            f"- `{n}`: {c['test_skipped']}"
            for n, c in info["candidates"].items()
            if c["status"] == "ok" and c.get("test_skipped")
        ]
        if no_test:
            lines += ["No test metrics:", *no_test, ""]
        lines += _tune_section(info)
        lines += _lr_section(info)
        lines += _sizes_section(info, budget)
        lines += _int8_section(info, fprs)
        lines += _confusion_section(info, fprs)
        lines += _curve_section(info, fprs)
    return "\n".join(lines).rstrip() + "\n"


def _tune_section(info: dict) -> list[str]:
    studies = info.get("tune") or {}
    if not studies:
        return []
    lines = ["### Hyper-parameter search (Optuna)", ""]
    for name, t in studies.items():
        best = ", ".join(
            f"{k}={v:.4g}" if isinstance(v, float) else f"{k}={v}"
            for k, v in t["best_params"].items()
        )
        lines += [
            f"`{name}`: {t['n_trials']} trials ({t['n_complete']} complete, "
            f"{t['n_pruned']} pruned), {t['sampler']}, {t['pruner']}, storage `{t['storage']}`. "
            f"Objective: {t['direction']}; best {_f(t['best_value'], 4)} "
            f"(trial {t['best_trial']}).",
            "",
            f"Best parameters: {best or 'none (no complete trial)'}",
            "",
        ]
        if t.get("lr_find"):
            lines += [
                _lr_line(t["lr_find"], "Range test run once for all trials (base parameters)"),
                "",
            ]
    return lines


def _lr_line(plan: dict, label: str) -> str:
    s = plan["schedule"]
    head = f"{label}: lr {plan['lr']:.3g} ({plan['source']}"
    find = plan.get("lr_find")
    if find:
        sug = ", ".join(f"{k} {v:.3g}" for k, v in find["suggestions"].items() if v)
        head += (
            f", rule {find['rule']}: {sug}; x{plan['multiplier']:.3g} multiplier, "
            f"{find['steps_run']} range-test steps"
            + (f", stopped: {find['stop_reason']}" if find["stopped_early"] else "")
        )
    warn = find.get("warning") if find else ""
    return (
        f"{head}); {s['kind']} schedule over {s['total_steps']} steps, warmup "
        f"{s['warmup_steps']}, floor {s['floor_lr']:.3g}."
        + (f" **Warning: {warn}.**" if warn else "")
    )


def _lr_section(info: dict) -> list[str]:
    lines: list[str] = []
    for name, c in info["candidates"].items():
        plan = (c.get("training") or {}).get("lr_plan")
        pre = (c.get("training") or {}).get("pretrain_lr_plan")
        if not plan and not pre:
            continue
        if not lines:
            lines = ["### Learning rate", ""]
        for label, p in ((f"`{name}`", plan), (f"`{name}` pretraining", pre)):
            if not p:
                continue
            lines += [_lr_line(p, label), ""]
            curve = (p.get("lr_find") or {}).get("curve") or []
            if curve:
                pts = curve[:: max(1, len(curve) // 8)]
                lines += [
                    "Range-test curve (lr: smoothed loss; full curve in the JSON report): "
                    + ", ".join(f"{a:.2g}: {b:.3g}" for a, b in pts),
                    "",
                ]
    return lines


def _sizes_section(info: dict, budget: float | None = None) -> list[str]:
    rows = []
    for name, c in info["candidates"].items():
        for count, b in (c.get("bench_sizes") or {}).items():
            rows.append(
                [
                    name,
                    count,
                    _f(b["latency_p50_ms"], 2),
                    _f(b["latency_p99_ms"], 2),
                    _f(b.get("players_per_core"), 0),
                    *([_within(b["latency_p99_ms"], budget)] if budget else []),
                    f"{b['threads']}",
                ]
            )
    if not rows:
        return []
    head = ["candidate", "packets", "p50 ms", "p99 ms", "players/core"]
    head += [f"p99 <= {budget:g} ms"] if budget else []
    head += ["threads"]
    return ["### CPU latency per window by packet count (batch of 1)", "", *_table(head, rows), ""]


def _int8_section(info: dict, fprs: list[float]) -> list[str]:
    rows = []
    for name, c in info["candidates"].items():
        q = c.get("int8")
        if not q:
            continue
        if "error" in q:
            rows.append([name, q["error"], *["-"] * 5])
            continue
        b = q["bench"]
        rows.append(
            [
                name,
                _f(q["delta"]["roc_auc"], 4),
                _f(q["delta"]["pr_auc"], 4),
                _f(q["delta"][f"recall@fpr{_tag(fprs[0])}"], 4),
                _f(b["latency_p50_ms"]),
                _f(b["latency_p99_ms"]),
                _f(q["file_size_kb"], 0),
            ]
        )
    if not rows:
        return []
    head = [
        "candidate",
        "dROC-AUC",
        "dPR-AUC",
        f"dR@{fprs[0] * 100:g}%",
        "p50 ms",
        "p99 ms",
        "size KiB",
    ]
    return ["### int8 dynamic quantisation (delta vs fp32)", "", *_table(head, rows), ""]


def _confusion_section(info: dict, fprs: list[float]) -> list[str]:
    rows = []
    for name, c in info["candidates"].items():
        if c["status"] != "ok":
            continue
        for f in fprs:
            k = c["confusion_at_val_threshold"].get(_tag(f), {})
            if "tp" not in k:
                continue
            rows.append(
                [
                    name,
                    f"{f * 100:g}%",
                    _f(k["threshold"], 4),
                    str(k["tp"]),
                    str(k["fp"]),
                    str(k["fn"]),
                    str(k["tn"]),
                    _f(k["recall"]),
                    _f(k["fpr"], 4),
                ]
            )
    if not rows:
        return []
    head = ["candidate", "target FPR", "threshold", "TP", "FP", "FN", "TN", "recall", "FPR"]
    return [
        "### Confusion on test at the threshold chosen on validation",
        "",
        *_table(head, rows),
        "",
    ]


def _curve_section(info: dict, fprs: list[float]) -> list[str]:
    rows = []
    key = f"recall@fpr{_tag(fprs[0])}"
    for name, c in info["candidates"].items():
        for point in c.get("learning_curve", []):
            if "pr_auc" not in point:
                continue
            rows.append(
                [
                    name,
                    f"{point['fraction'] * 100:g}%",
                    _f(point["n_train_players"], 0),
                    _f(point["roc_auc"]),
                    _f(point["pr_auc"]),
                    _f(point[key]),
                ]
            )
    if not rows:
        return []
    head = [
        "candidate",
        "train share",
        "train players",
        "ROC-AUC",
        "PR-AUC",
        f"R@{fprs[0] * 100:g}%",
    ]
    return [
        "### Learning curve (test metrics vs share of training players)",
        "",
        *_table(head, rows),
        "",
    ]


def write_report(report: dict, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    js = out_dir / "report.json"
    md = out_dir / "report.md"
    js.write_text(json.dumps(report, indent=2, default=_json_default, allow_nan=True) + "\n")
    md.write_text(to_markdown(report))
    return js, md
