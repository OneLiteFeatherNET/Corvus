"""Deterministic group split by player (pseudonym). Windows never decide the split."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from corvus_ml.eval.data import LABEL_CHEAT, LABEL_LEGIT, LABEL_UNLABELLED


@dataclass(frozen=True)
class Split:
    train: np.ndarray
    val: np.ndarray
    test: np.ndarray

    def parts(self) -> dict[str, np.ndarray]:
        return {"train": self.train, "val": self.val, "test": self.test}


def player_stratum(labels: np.ndarray, groups: np.ndarray) -> dict[int, int]:
    """Per player: 1 if any cheat window, else 0 if any legit window, else -1."""
    out: dict[int, int] = {}
    for g in np.unique(groups):
        lab = labels[groups == g]
        if (lab == LABEL_CHEAT).any():
            out[int(g)] = LABEL_CHEAT
        elif (lab == LABEL_LEGIT).any():
            out[int(g)] = LABEL_LEGIT
        else:
            out[int(g)] = LABEL_UNLABELLED
    return out


def _counts(m: int, f_val: float, f_test: float) -> tuple[int, int]:
    n_val, n_test = round(m * f_val), round(m * f_test)
    if m >= 3:  # every split gets at least one player of the stratum
        n_val, n_test = max(n_val, 1), max(n_test, 1)
        while n_val + n_test >= m:
            if n_val >= n_test and n_val > 1:
                n_val -= 1
            elif n_test > 1:
                n_test -= 1
            else:
                break
    elif m == 2:
        n_val, n_test = 0, 1
    else:
        n_val, n_test = 0, 0
    return n_val, n_test


def group_split(
    labels: np.ndarray,
    groups: np.ndarray,
    seed: int,
    fractions: tuple[float, float, float] = (0.6, 0.2, 0.2),
) -> Split:
    """Split window indices into train/val/test so that no group lands in two parts.

    ``groups`` is one id per window (the pseudonym). Players are stratified by their label
    (cheat / legit / unlabelled), shuffled with ``seed`` and cut by ``fractions``. The result
    depends only on (player ids, player strata, seed), not on window order.
    """
    if abs(sum(fractions) - 1.0) > 1e-6 or min(fractions) < 0:
        raise ValueError("fractions must be non-negative and sum to 1")
    labels = np.asarray(labels)
    groups = np.asarray(groups)
    strata = player_stratum(labels, groups)
    rng = np.random.default_rng(seed)
    assign: dict[int, str] = {}
    for stratum in (LABEL_CHEAT, LABEL_LEGIT, LABEL_UNLABELLED):
        members = sorted(g for g, s in strata.items() if s == stratum)
        rng.shuffle(members)
        n_val, n_test = _counts(len(members), fractions[1], fractions[2])
        for k, g in enumerate(members):
            assign[g] = "val" if k < n_val else "test" if k < n_val + n_test else "train"
    part = np.array([assign[int(g)] for g in groups])
    split = Split(*(np.flatnonzero(part == name) for name in ("train", "val", "test")))
    assert_no_leakage(groups, split)
    return split


def assert_no_leakage(groups: np.ndarray, split: Split) -> None:
    """Raise AssertionError if a group appears in more than one part."""
    seen: dict[int, str] = {}
    for name, idx in split.parts().items():
        for g in np.unique(groups[idx]):
            prev = seen.setdefault(int(g), name)
            assert prev == name, f"group {g} appears in both {prev} and {name}"


def subsample_groups(
    labels: np.ndarray, groups: np.ndarray, indices: np.ndarray, fraction: float, seed: int
) -> np.ndarray:
    """Window indices of a nested, deterministic, label-stratified subset of the players.

    Nested: the players of a smaller fraction are a subset of those of a larger one.
    """
    if fraction >= 1.0:
        return indices
    strata = player_stratum(labels[indices], groups[indices])
    rng = np.random.default_rng(seed)
    chosen: set[int] = set()
    for stratum in (LABEL_CHEAT, LABEL_LEGIT, LABEL_UNLABELLED):
        members = sorted(g for g, s in strata.items() if s == stratum)
        order = rng.permutation(len(members))
        k = max(1, round(len(members) * fraction)) if members else 0
        chosen.update(members[i] for i in order[:k])
    mask = np.array([int(g) in chosen for g in groups[indices]])
    return indices[mask]
