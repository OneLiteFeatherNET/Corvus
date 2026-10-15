import numpy as np
import pytest

from corvus_ml.eval.split import Split, assert_no_leakage, group_split, subsample_groups


def population(n_legit=20, n_cheat=10, windows=5):
    groups = np.repeat(np.arange(n_legit + n_cheat), windows)
    labels = np.where(groups >= n_legit, 1, 0).astype(np.int8)
    return labels, groups


def test_group_split_never_puts_a_player_in_two_parts():
    labels, groups = population()
    split = group_split(labels, groups, seed=3)
    seen = {}
    for name, idx in split.parts().items():
        for g in np.unique(groups[idx]):
            assert seen.setdefault(int(g), name) == name, f"player {g} leaked"


def test_group_split_covers_every_window_exactly_once():
    labels, groups = population()
    split = group_split(labels, groups, seed=3)
    all_idx = np.concatenate([split.train, split.val, split.test])
    assert sorted(all_idx.tolist()) == list(range(len(groups)))


def test_group_split_is_deterministic_for_a_seed_and_differs_between_seeds():
    labels, groups = population()
    a = group_split(labels, groups, seed=5)
    b = group_split(labels, groups, seed=5)
    c = group_split(labels, groups, seed=6)
    assert np.array_equal(a.test, b.test)
    assert not np.array_equal(a.test, c.test)


def test_group_split_stratifies_cheaters_into_every_part():
    labels, groups = population()
    split = group_split(labels, groups, seed=1)
    for name, idx in split.parts().items():
        assert (labels[idx] == 1).any(), f"no cheat player in {name}"
        assert (labels[idx] == 0).any(), f"no legit player in {name}"


def test_group_split_does_not_depend_on_window_order():
    labels, groups = population()
    perm = np.random.default_rng(0).permutation(len(groups))
    a = group_split(labels, groups, seed=2)
    b = group_split(labels[perm], groups[perm], seed=2)
    assert set(groups[a.test]) == set(groups[perm][b.test])


def test_group_split_rejects_bad_fractions():
    labels, groups = population()
    with pytest.raises(ValueError, match="sum to 1"):
        group_split(labels, groups, seed=1, fractions=(0.5, 0.5, 0.5))


def test_assert_no_leakage_detects_a_shared_player():
    groups = np.array([0, 0, 1, 1])
    bad = Split(np.array([0, 2]), np.array([1]), np.array([3]))
    with pytest.raises(AssertionError, match="appears in both"):
        assert_no_leakage(groups, bad)


def test_subsample_groups_is_nested_and_keeps_both_classes():
    labels, groups = population()
    idx = np.arange(len(groups))
    small = subsample_groups(labels, groups, idx, 0.25, seed=1)
    large = subsample_groups(labels, groups, idx, 0.5, seed=1)
    assert set(groups[small]) <= set(groups[large])
    assert (labels[small] == 1).any()
    assert (labels[small] == 0).any()
