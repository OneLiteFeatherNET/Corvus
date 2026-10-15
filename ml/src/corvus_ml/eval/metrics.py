"""Evaluation metrics. Scores are "higher = more likely cheat"; labels are 0 / 1."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence

import numpy as np
from sklearn.metrics import average_precision_score, roc_auc_score

NAN = float("nan")


def _both_classes(y: np.ndarray) -> bool:
    return bool((y == 1).any() and (y == 0).any())


def roc_auc(y: np.ndarray, s: np.ndarray) -> float:
    return float(roc_auc_score(y, s)) if _both_classes(y) else NAN


def pr_auc(y: np.ndarray, s: np.ndarray) -> float:
    """Average precision (area under the precision-recall step curve)."""
    return float(average_precision_score(y, s)) if _both_classes(y) else NAN


def threshold_at_fpr(y: np.ndarray, s: np.ndarray, fpr: float) -> float:
    """Score threshold so that ``s > threshold`` flags at most ``fpr`` of the negatives.

    With k = floor(fpr * n_neg) tolerated false positives the threshold is the (k + 1)-th
    largest negative score; ties can only lower the achieved FPR. -inf if every negative may fire.
    """
    neg = np.sort(s[y == 0])[::-1]
    if neg.size == 0:
        return NAN
    k = math.floor(fpr * neg.size + 1e-9)
    return -math.inf if k >= neg.size else float(neg[k])


def recall_at_fpr(y: np.ndarray, s: np.ndarray, fpr: float) -> float:
    """Largest recall with FPR <= ``fpr`` on this very score set (ROC read-out)."""
    if not _both_classes(y):
        return NAN
    thr = threshold_at_fpr(y, s, fpr)
    return float((s[y == 1] > thr).mean())


def resolution_ok(y: np.ndarray, fpr: float) -> bool:
    """False when there are too few negatives to resolve ``fpr`` (fewer than 1 / fpr)."""
    return int((y == 0).sum()) * fpr >= 1.0


def aggregate_by_player(
    y: np.ndarray, s: np.ndarray, groups: np.ndarray, how: str = "max"
) -> tuple[np.ndarray, np.ndarray]:
    """One row per player: score = max/mean over windows, label = 1 if any window is cheat."""
    if how not in ("max", "mean"):
        raise ValueError("how must be 'max' or 'mean'")
    players = np.unique(groups)
    ys = np.empty(players.size, dtype=np.int8)
    ss = np.empty(players.size, dtype=np.float64)
    for i, g in enumerate(players):
        m = groups == g
        ys[i] = 1 if (y[m] == 1).any() else 0
        ss[i] = s[m].max() if how == "max" else s[m].mean()
    return ys, ss


def recall_at_fpr_per_player(
    y: np.ndarray, s: np.ndarray, groups: np.ndarray, fpr: float, how: str = "max"
) -> float:
    yp, sp = aggregate_by_player(y, s, groups, how)
    return recall_at_fpr(yp, sp, fpr)


def ece(y: np.ndarray, p: np.ndarray, bins: int = 10) -> float:
    """Expected calibration error with equal-width probability bins (binary, positive class)."""
    if y.size == 0:
        return NAN
    idx = np.minimum((np.clip(p, 0.0, 1.0) * bins).astype(int), bins - 1)
    total = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            total += m.mean() * abs(float(y[m].mean()) - float(p[m].mean()))
    return float(total)


def confusion_at_threshold(y: np.ndarray, s: np.ndarray, thr: float) -> dict[str, float]:
    pred = s > thr
    tp = int((pred & (y == 1)).sum())
    fp = int((pred & (y == 0)).sum())
    fn = int((~pred & (y == 1)).sum())
    tn = int((~pred & (y == 0)).sum())
    return {
        "threshold": thr,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "recall": tp / (tp + fn) if tp + fn else NAN,
        "fpr": fp / (fp + tn) if fp + tn else NAN,
        "precision": tp / (tp + fp) if tp + fp else NAN,
    }


def confusion_at_val_threshold(
    y_val: np.ndarray, s_val: np.ndarray, y_test: np.ndarray, s_test: np.ndarray, fpr: float
) -> dict[str, float]:
    """Pick the threshold for ``fpr`` on validation, apply it to test."""
    thr = threshold_at_fpr(y_val, s_val, fpr)
    if math.isnan(thr):
        return {"threshold": NAN}
    return confusion_at_threshold(y_test, s_test, thr)


def window_metrics(
    y: np.ndarray, s: np.ndarray, groups: np.ndarray, fprs: Sequence[float]
) -> dict[str, float]:
    """All ranking metrics for one evaluation set, per window and per player."""
    out: dict[str, float] = {
        "n_windows": float(y.size),
        "n_cheat_windows": float((y == 1).sum()),
        "n_players": float(np.unique(groups).size),
        "roc_auc": roc_auc(y, s),
        "pr_auc": pr_auc(y, s),
    }
    for fpr in fprs:
        tag = f"{fpr * 100:g}pct"
        out[f"recall@fpr{tag}"] = recall_at_fpr(y, s, fpr)
        out[f"recall@fpr{tag}_player_max"] = recall_at_fpr_per_player(y, s, groups, fpr, "max")
        out[f"recall@fpr{tag}_player_mean"] = recall_at_fpr_per_player(y, s, groups, fpr, "mean")
        out[f"resolved@fpr{tag}"] = float(resolution_ok(y, fpr))
    return out


def learning_curve(
    fractions: Sequence[float], run: Callable[[float], dict[str, float]]
) -> list[dict[str, float]]:
    """Evaluate ``run(fraction)`` for each training-set fraction (e.g. 0.1, 0.25, 0.5, 1.0).

    ``run`` fits on that share of the training players and returns test metrics. A curve that is
    still rising at 1.0 means more data would help.
    """
    return [{"fraction": float(f), **run(float(f))} for f in fractions]
