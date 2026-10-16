"""Memory-aware micro-batching: the estimate, the budget and the cap are pure functions."""

import logging

import pytest

from corvus_ml.cgroup import memory_limit, parse_memory_max
from corvus_ml.membudget import (
    BUDGET_ENV,
    DEFAULT_FRACTION,
    activation_bytes_per_sample,
    memory_budget_bytes,
    micro_batch_size,
    plan_micro_batch,
)
from corvus_ml.raw.model import ModelConfig

MB = 1024 * 1024
F = 4  # float32


def cfg(**kw) -> ModelConfig:
    base = {
        "vocab_size": 100, "max_tokens": 16, "d_model": 32, "heads": 2, "layers": 1,
        "local_dim": 16, "local_heads": 2, "local_layers": 1, "ffn_mult": 2, "max_masked": 64,
    }  # fmt: skip
    return ModelConfig(**(base | kw))


# --- cgroup memory limit -------------------------------------------------------------------------


def test_memory_max_of_a_limited_cgroup_is_parsed_in_bytes():
    assert parse_memory_max("8589934592\n") == 8 * 1024**3


@pytest.mark.parametrize("text", ["max", "max\n", "", "garbage", "0", "-5", str(2**62)])
def test_memory_max_without_a_usable_limit_is_none(text):
    assert parse_memory_max(text) is None


def test_missing_memory_file_means_no_limit(tmp_path):
    assert memory_limit(tmp_path / "absent") is None


# --- estimate ------------------------------------------------------------------------------------


def test_training_estimate_follows_the_documented_formula():
    c, p = cfg(), 10
    t, dl, d = c.max_tokens, c.local_dim, c.d_model
    global_att = c.heads * (p + 1) ** 2 * F
    local_att = c.local_heads * t * t * p * F
    tokens = p * t * dl * F
    expected = (
        4 * (c.layers * global_att + c.local_layers * local_att)
        + c.local_layers * (6 + 2 * c.ffn_mult) * tokens
        + c.layers * (8 + 2 * c.ffn_mult) * (p + 1) * d * F
        + 3 * c.max_masked * c.vocab_size * F
    )
    assert activation_bytes_per_sample(c, p, training=True) == expected


def test_inference_estimate_follows_the_documented_formula():
    c, p = cfg(), 10
    global_att = c.heads * (p + 1) ** 2 * F
    local_att = c.local_heads * c.max_tokens**2 * p * F
    tokens = p * c.max_tokens * c.local_dim * F
    expected = 3 * max(global_att, local_att) + (6 + 2 * c.ffn_mult) * tokens
    assert activation_bytes_per_sample(c, p, training=False) == expected


def test_global_attention_makes_the_estimate_grow_quadratically_with_the_packets():
    small = activation_bytes_per_sample(cfg(local_type="cnn"), 256, training=False)
    large = activation_bytes_per_sample(cfg(local_type="cnn"), 2048, training=False)
    assert large > 16 * small, "8x the packets must cost far more than 8x (P^2 attention)"


def test_training_needs_more_than_inference():
    c = cfg()
    assert activation_bytes_per_sample(c, 64, training=True) > activation_bytes_per_sample(
        c, 64, training=False
    )


def test_macro_windows_multiply_the_cost_of_a_sample():
    one = activation_bytes_per_sample(cfg(), 32, training=True)
    four = activation_bytes_per_sample(cfg(macro_windows=4), 32, training=True)
    assert four == 4 * one


def test_production_shape_exceeds_the_eight_gib_pod_at_the_default_batch():
    prod = ModelConfig(vocab_size=4096, max_tokens=48, d_model=64, heads=4, layers=2)
    per_sample = activation_bytes_per_sample(prod, 2048, training=True)
    assert 16 * per_sample > 8 * 1024**3, "the failure this guards against: batch 16 at P=2048"


# --- budget --------------------------------------------------------------------------------------


def test_budget_is_a_fraction_of_the_cgroup_limit():
    assert memory_budget_bytes(limit=8 * 1024 * MB, env={}) == int(8 * 1024 * MB * DEFAULT_FRACTION)


def test_explicit_budget_wins_over_environment_and_limit():
    env = {BUDGET_ENV: "999"}
    assert memory_budget_bytes(512, limit=8 * 1024 * MB, env=env) == 512 * MB


def test_environment_overrides_the_limit():
    assert memory_budget_bytes(limit=8 * 1024 * MB, env={BUDGET_ENV: "300"}) == 300 * MB


def test_unusable_environment_value_is_an_error():
    with pytest.raises(ValueError, match=BUDGET_ENV):
        memory_budget_bytes(limit=MB, env={BUDGET_ENV: "lots"})


def test_non_positive_budget_is_an_error():
    with pytest.raises(ValueError, match="memory_budget_mb"):
        memory_budget_bytes(0, env={})


def test_without_cgroup_limit_the_physical_memory_is_used(monkeypatch):
    monkeypatch.setattr("corvus_ml.membudget.memory_limit", lambda *a, **k: None)
    monkeypatch.setattr("corvus_ml.membudget.physical_memory_bytes", lambda: 4096 * MB)
    assert memory_budget_bytes(env={}) == int(4096 * MB * DEFAULT_FRACTION)


# --- micro batch ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("batch", "per_sample", "budget", "expected"),
    [
        (16, 100, 10_000, 16),  # everything fits
        (16, 1_000, 4_000, 4),  # capped by the budget
        (16, 1_000, 4_500, 4),  # floor, never round up past the budget
        (16, 10_000, 4_000, 1),  # a single sample does not fit: still run it
        (3, 1, 10**9, 3),  # never larger than the batch
    ],
)
def test_micro_batch_is_the_largest_that_fits_at_least_one(batch, per_sample, budget, expected):
    assert micro_batch_size(batch, per_sample, budget) == expected


def test_plan_caps_the_micro_batch_for_a_fake_memory_limit_and_keeps_the_batch(caplog):
    c = cfg()
    per_sample = activation_bytes_per_sample(c, 64, training=True)
    budget_mb = max(1, int(3.5 * per_sample / MB) + 1)  # room for 3 samples, not 16
    with caplog.at_level(logging.WARNING, logger="corvus_ml.membudget"):
        plan = plan_micro_batch(c, 64, 16, training=True, budget_mb=budget_mb, env={})
    assert plan.batch == 16, "the optimiser step still sees the configured batch"
    assert 1 <= plan.micro < 16
    assert plan.micro * per_sample <= budget_mb * MB
    assert plan.steps == -(-16 // plan.micro)
    assert plan.reduced
    assert any("micro-batch" in r.message for r in caplog.records), "the reduction is logged"


def test_plan_does_not_reduce_or_log_when_everything_fits(caplog):
    with caplog.at_level(logging.WARNING, logger="corvus_ml.membudget"):
        plan = plan_micro_batch(cfg(), 8, 4, training=True, budget_mb=4096, env={})
    assert (plan.micro, plan.steps, plan.reduced) == (4, 1, False)
    assert not caplog.records
