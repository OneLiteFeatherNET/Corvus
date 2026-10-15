import math

import numpy as np
import pytest

from corvus_ml.eval import metrics as M


def hand_made():
    # 10 negatives with scores 0.0 .. 0.9, 4 positives.
    neg = np.arange(10) / 10.0
    pos = np.array([0.95, 0.85, 0.55, 0.05])
    y = np.r_[np.zeros(10), np.ones(4)].astype(np.int8)
    return y, np.r_[neg, pos]


def test_recall_at_fpr_tolerating_one_false_positive():
    y, s = hand_made()
    # FPR 10 % of 10 negatives = 1 false positive: threshold is the 2nd largest negative (0.8).
    assert M.recall_at_fpr(y, s, 0.10) == pytest.approx(0.5)  # 0.95 and 0.85 are above 0.8


def test_recall_at_fpr_with_no_false_positive_allowed():
    y, s = hand_made()
    assert M.recall_at_fpr(y, s, 0.0) == pytest.approx(0.25)  # only 0.95 beats the top negative


def test_threshold_at_fpr_never_exceeds_the_target_rate():
    y, s = hand_made()
    thr = M.threshold_at_fpr(y, s, 0.2)
    assert (s[y == 0] > thr).mean() <= 0.2


def test_resolution_flag_needs_one_over_fpr_negatives():
    y, _ = hand_made()
    assert M.resolution_ok(y, 0.1)
    assert not M.resolution_ok(y, 0.01)


def test_ece_of_a_perfectly_calibrated_set_is_zero():
    p = np.array([0.0] * 10 + [1.0] * 10)
    y = np.array([0] * 10 + [1] * 10)
    assert M.ece(y, p) == pytest.approx(0.0)


def test_ece_of_a_known_example():
    # One bin: confidence 0.8, accuracy 0.5 -> ECE = |0.5 - 0.8| = 0.3.
    p = np.full(4, 0.8)
    y = np.array([1, 0, 1, 0])
    assert M.ece(y, p, bins=10) == pytest.approx(0.3)


def test_roc_and_pr_auc_of_a_perfect_ranking():
    y, s = np.array([0, 0, 1, 1]), np.array([0.1, 0.2, 0.8, 0.9])
    assert M.roc_auc(y, s) == pytest.approx(1.0)
    assert M.pr_auc(y, s) == pytest.approx(1.0)


def test_metrics_are_nan_with_a_single_class():
    y, s = np.zeros(4, dtype=int), np.arange(4.0)
    assert math.isnan(M.roc_auc(y, s))
    assert math.isnan(M.recall_at_fpr(y, s, 0.1))


def test_player_aggregation_uses_max_or_mean_and_any_cheat_label():
    y = np.array([0, 0, 1, 1])
    s = np.array([0.1, 0.9, 0.2, 0.4])
    g = np.array([0, 0, 1, 1])
    yp, smax = M.aggregate_by_player(y, s, g, "max")
    _, smean = M.aggregate_by_player(y, s, g, "mean")
    assert yp.tolist() == [0, 1]
    assert smax.tolist() == pytest.approx([0.9, 0.4])
    assert smean.tolist() == pytest.approx([0.5, 0.3])


def test_player_recall_differs_from_window_recall_when_one_window_stands_out():
    # Cheater 1 has one very high window, the rest low; max-aggregation catches the player.
    y = np.array([0, 0, 0, 0, 1, 1, 1, 1])
    s = np.array([0.1, 0.2, 0.15, 0.3, 0.05, 0.06, 0.07, 0.99])
    g = np.array([0, 0, 1, 1, 2, 2, 2, 2])
    assert M.recall_at_fpr(y, s, 0.0) == pytest.approx(0.25)
    assert M.recall_at_fpr_per_player(y, s, g, 0.0, "max") == pytest.approx(1.0)


def test_confusion_uses_the_validation_threshold_on_test():
    yv = np.array([0, 0, 0, 0, 1])
    sv = np.array([0.1, 0.2, 0.3, 0.4, 0.9])
    yt = np.array([0, 0, 1, 1])
    st = np.array([0.35, 0.5, 0.45, 0.2])
    c = M.confusion_at_val_threshold(yv, sv, yt, st, fpr=0.25)  # one FP allowed -> thr 0.3
    assert c["threshold"] == pytest.approx(0.3)
    assert (c["tp"], c["fp"], c["fn"], c["tn"]) == (1, 2, 1, 0)


def test_learning_curve_calls_run_for_each_fraction():
    calls = []

    def run(frac):
        calls.append(frac)
        return {"pr_auc": frac}

    out = M.learning_curve([0.1, 0.5, 1.0], run)
    assert calls == [0.1, 0.5, 1.0]
    assert [p["fraction"] for p in out] == [0.1, 0.5, 1.0]
