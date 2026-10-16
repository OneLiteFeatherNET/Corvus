"""Learning-rate range test, suggestion rules, schedule and the "auto" parameter."""

import copy
import math

import numpy as np
import pytest
import torch

from corvus_ml.eval import candidates
from corvus_ml.eval.lr import (
    LrFindConfig,
    LrSchedule,
    assess_curve,
    exponential_lrs,
    find_lr,
    lr_kwargs,
    parse_lr,
    plan_lr,
    scale_lr_for_batch,
    smooth_ema,
    suggest,
    suggest_steepest,
    suggest_valley,
    total_steps,
    warmup_steps_for,
)
from corvus_ml.eval.training import fit, lr_at, lr_range_test

# --- pure functions ------------------------------------------------------------------------------


def test_exponential_sweep_hits_both_ends_and_has_the_step_count():
    lrs = exponential_lrs(1e-7, 1.0, 100)
    assert len(lrs) == 100
    assert lrs[0] == pytest.approx(1e-7)
    assert lrs[-1] == pytest.approx(1.0)
    ratios = lrs[1:] / lrs[:-1]
    assert np.allclose(ratios, ratios[0]), "constant ratio between steps"


def test_exponential_sweep_rejects_an_empty_range():
    with pytest.raises(ValueError, match="lr_min < lr_max"):
        exponential_lrs(1.0, 1e-3, 10)


def test_ema_is_bias_corrected_so_a_constant_stays_constant():
    assert smooth_ema([2.0, 2.0, 2.0, 2.0], beta=0.9) == pytest.approx([2.0] * 4)


def test_ema_first_value_is_the_first_sample():
    assert smooth_ema([5.0, 1.0], beta=0.9)[0] == pytest.approx(5.0)


def synthetic_curve():
    """Flat, then one sigmoid descent centred on 1e-3, minimum near 1e-2, then divergence."""
    x = np.linspace(-7, 0, 200)  # log10(lr)
    loss = 1.0 - 0.8 / (1 + np.exp(-3 * (x + 3))) + 2.0 / (1 + np.exp(-6 * (x + 1)))
    return 10.0**x, loss


def test_steepest_rule_finds_the_centre_of_the_descent():
    lrs, loss = synthetic_curve()
    assert math.log10(suggest_steepest(lrs, loss)) == pytest.approx(-3, abs=0.1)


def test_valley_rule_is_a_tenth_of_the_minimum():
    lrs, loss = synthetic_curve()
    assert suggest_valley(lrs, loss) == pytest.approx(lrs[int(np.argmin(loss))] / 10)


def test_steepest_rule_ignores_the_climb_after_the_minimum():
    lrs = np.logspace(-5, 0, 50)
    loss = np.concatenate([np.linspace(1.0, 0.5, 25), np.linspace(0.5, 50.0, 25)])
    assert suggest_steepest(lrs, loss) < lrs[25]


def test_steepest_rule_gives_up_on_a_curve_that_never_falls():
    lrs = np.logspace(-5, 0, 20)
    assert suggest_steepest(lrs, np.linspace(1.0, 2.0, 20)) is None
    assert suggest(lrs, np.linspace(1.0, 2.0, 20))["valley"] == pytest.approx(lrs[0] / 10)


# --- range test on a tiny model ---


def regression(n=128, seed=0):
    g = torch.Generator().manual_seed(seed)
    x = torch.randn(n, 4, generator=g)
    y = x @ torch.tensor([1.0, -2.0, 0.5, 0.0])
    return x, y


def mse(model, batch):
    return torch.nn.functional.mse_loss(model(batch[0]).squeeze(-1), batch[1])


def tiny():
    torch.manual_seed(0)
    return torch.nn.Linear(4, 1)


CFG = LrFindConfig(lr_min=1e-5, lr_max=0.1, steps=30, batch_size=32, seed=1)


def test_range_test_runs_the_requested_steps_over_the_whole_range():
    res = find_lr(tiny(), mse, regression(), LrFindConfig(**{**CFG.__dict__, "divergence": 1e9}))
    assert len(res.lrs) == len(res.losses) == len(res.raw_losses) == 30
    assert res.lrs[0] == pytest.approx(1e-5)
    assert res.lrs[-1] == pytest.approx(0.1)
    assert not res.stopped_early
    assert 1e-5 <= res.suggested_lr <= 0.1


def test_range_test_stops_early_when_the_loss_diverges():
    def exploding(model, batch):
        return mse(model, batch) * (1.0 + 1e6 * model.weight.abs().sum())

    cfg = LrFindConfig(**{**CFG.__dict__, "lr_max": 1e3, "steps": 60})
    res = find_lr(tiny(), exploding, regression(), cfg)
    assert res.stopped_early
    assert res.stop_reason
    assert len(res.lrs) < 60


def test_range_test_leaves_model_optimizer_and_rng_untouched():
    model = tiny()
    opt = torch.optim.AdamW(model.parameters(), lr=1e-3)
    before = copy.deepcopy(model.state_dict())
    rng = torch.get_rng_state()
    model.eval()
    find_lr(model, mse, regression(), CFG)
    for k, v in model.state_dict().items():
        assert torch.equal(v, before[k]), f"{k} changed"
    assert opt.state_dict()["state"] == {}, "no optimizer step on the caller's optimizer"
    assert all(p.grad is None for p in model.parameters())
    assert not model.training, "train/eval mode of the caller's model is kept"
    assert torch.equal(torch.get_rng_state(), rng), "global RNG state is restored"


def test_range_test_is_deterministic():
    a = find_lr(tiny(), mse, regression(), CFG)
    b = find_lr(tiny(), mse, regression(), CFG)
    assert a.losses == b.losses
    assert a.suggested_lr == b.suggested_lr


def test_range_test_works_with_a_self_supervised_loss_closure():
    def reconstruction(model, batch):
        return torch.nn.functional.mse_loss(model(batch[0]), batch[0])

    torch.manual_seed(0)
    res = find_lr(torch.nn.Linear(4, 4), reconstruction, (regression()[0],), CFG)
    assert len(res.lrs) >= 3


def test_result_reports_rules_and_a_downsampled_curve():
    res = find_lr(tiny(), mse, regression(), CFG)
    d = res.to_dict(max_points=10)
    assert set(d["suggestions"]) == {"steepest", "valley"}
    assert d["rule"] in {"steepest", "valley", "fallback"}
    assert len(d["curve"]) <= 10
    assert d["curve"][0][0] == pytest.approx(res.lrs[0])
    assert d["curve"][-1][0] == pytest.approx(res.lrs[-1])
    assert d["steps_requested"] == 30


def test_old_entry_point_still_works():
    res = lr_range_test(tiny(), mse, regression(), batch_size=32, lr_min=1e-5, steps=40)
    assert len(res.lrs) == len(res.losses) >= 3
    assert 1e-5 <= res.suggested_lr <= 1.0


# --- schedule ----------------------------------------------------------------------------------


def test_total_steps_counts_the_partial_last_batch():
    assert total_steps(1000, 64, 3) == 3 * 16


def test_warmup_is_a_fraction_with_bounds():
    assert warmup_steps_for(10_000) == 500
    assert warmup_steps_for(40) == 5, "lower bound"
    assert warmup_steps_for(1_000_000) == 2000, "upper bound"
    assert warmup_steps_for(6) == 3, "never more than half of the run"


def test_cosine_schedule_at_start_end_of_warmup_midpoint_and_end():
    s = LrSchedule(total_steps=110, warmup_steps=10, peak_lr=1e-3, floor_lr=1e-4)
    assert s(0) == 0.0
    assert s(5) == pytest.approx(5e-4)
    assert s(10) == pytest.approx(1e-3)
    assert s(60) == pytest.approx((1e-3 + 1e-4) / 2), "cosine midpoint"
    assert s(110) == pytest.approx(1e-4)
    assert s(500) == pytest.approx(1e-4)


def test_cosine_schedule_matches_the_old_lr_at():
    s = LrSchedule(100, 10, 1e-3, 1e-5)
    assert all(s(i) == pytest.approx(lr_at(i, 100, 10, 1e-3, 1e-5)) for i in range(0, 101, 7))


def test_wsd_schedule_holds_the_peak_then_decays_linearly():
    s = LrSchedule(100, 10, 1e-3, 1e-4, kind="wsd")
    assert s(10) == pytest.approx(1e-3)
    assert s(80) == pytest.approx(1e-3), "stable phase lasts until 80 %"
    assert s(90) == pytest.approx(5.5e-4)
    assert s(100) == pytest.approx(1e-4)


def test_batch_scaling_is_the_square_root_rule():
    assert scale_lr_for_batch(1e-3, 16, 64) == pytest.approx(2e-3)
    assert scale_lr_for_batch(1e-3, 64, 64) == pytest.approx(1e-3)


# --- "auto" parameter --------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["auto", "AUTO", " Auto "])
def test_parse_lr_accepts_auto_in_any_case(value):
    assert parse_lr(value) == "auto"


@pytest.mark.parametrize(("value", "expected"), [(0.003, 0.003), (1, 1.0), ("0.01", 0.01)])
def test_parse_lr_keeps_numbers(value, expected):
    assert parse_lr(value) == expected


@pytest.mark.parametrize("value", [0, -1e-3, "fast", None, True, float("nan"), [0.1]])
def test_parse_lr_rejects_everything_else(value):
    with pytest.raises(ValueError, match="lr must be"):
        parse_lr(value)


def test_numeric_lr_keeps_the_fixed_schedule_and_runs_no_range_test():
    x, y = regression()
    plan = plan_lr(0.01, tiny(), mse, (x, y), epochs=5, batch_size=32)
    assert plan.source == "fixed"
    assert plan.find is None
    assert plan.peak_lr == 0.01
    assert plan.schedule.floor_lr == 0.0
    assert plan.schedule.warmup_steps == max(1, int(plan.schedule.total_steps * 0.1))


def test_auto_lr_uses_the_found_peak_multiplier_and_a_five_percent_warmup():
    x, y = regression(n=640)
    plan = plan_lr("auto", tiny(), mse, (x, y), epochs=10, batch_size=32, find=CFG, multiplier=2.0)
    assert plan.source == "auto"
    assert plan.find is not None
    assert plan.peak_lr == pytest.approx(plan.find.suggested_lr * 2.0)
    assert plan.schedule.total_steps == 200
    assert plan.schedule.warmup_steps == 10
    assert plan.schedule.floor_lr == pytest.approx(plan.peak_lr * 0.1)


def test_auto_lr_scales_for_a_different_batch_size_than_the_range_test():
    x, y = regression()
    plan = plan_lr("auto", tiny(), mse, (x, y), epochs=2, batch_size=128, find=CFG)
    assert plan.batch_scale == pytest.approx(2.0)


def test_fit_with_auto_records_the_lr_decision_in_its_history():
    x, y = regression()
    model = tiny()
    hist = fit(model, mse, (x, y), None, epochs=3, batch_size=32, lr="auto", lr_find=CFG)
    assert hist.lr_used == pytest.approx(hist.schedule.peak_lr)
    assert hist.lr_find is not None
    assert hist.lr_plan["lr_find"]["curve"]
    assert hist.lr_plan["schedule"]["total_steps"] == 12


def test_fit_with_a_number_reports_the_fixed_plan():
    x, y = regression()
    hist = fit(tiny(), mse, (x, y), None, epochs=2, batch_size=32, lr=0.02)
    assert hist.lr_used == 0.02
    assert hist.lr_find is None
    assert hist.lr_plan["source"] == "fixed"


def test_lr_kwargs_reads_prefixed_parameters():
    kw = lr_kwargs(
        {"pretrain_lr": "auto", "pretrain_lr_steps": 20, "pretrain_lr_mult": 0.5},
        prefix="pretrain_",
        default_lr=1e-3,
        batch_size=16,
        seed=3,
        weight_decay=0.01,
    )
    assert kw["lr"] == "auto"
    assert kw["lr_mult"] == 0.5
    assert kw["lr_find"].steps == 20
    assert kw["lr_find"].seed == 3


def test_lr_kwargs_defaults_to_the_old_fixed_value():
    kw = lr_kwargs({}, default_lr=2e-3, batch_size=16, seed=0, weight_decay=0.01)
    assert kw["lr"] == 2e-3


def test_candidates_accept_the_lr_parameters():
    cand = candidates.create(
        "raw_hier_classifier", 0, 1, {"lr": "auto", "lr_mult": 1.5, "pretrain_lr": "auto"}
    )
    assert cand.params["lr"] == "auto"


def test_the_default_rule_is_the_robust_valley_rule():
    cfg = LrFindConfig(lr_min=1e-3, lr_max=0.3, steps=40, batch_size=32, seed=1)
    res = find_lr(tiny(), mse, regression(512), cfg)
    assert not res.flat, res.flat_reason
    assert res.rule == "valley"
    assert res.suggested_lr == pytest.approx(min(max(res.suggestions["valley"], 1e-3), 0.3))


def test_a_one_step_noise_dip_does_not_beat_the_real_descent():
    lrs, loss = synthetic_curve()
    noisy = loss.copy()
    noisy[20] -= 0.3  # a single-step dip far left of the real descent
    assert math.log10(suggest_steepest(lrs, noisy)) == pytest.approx(-3, abs=0.3)


# --- flat-curve guard ---


def noisy(loss, sigma, seed=0):
    return loss + np.random.default_rng(seed).normal(0, sigma, len(loss))


def test_a_clear_descent_is_not_flat():
    _, loss = synthetic_curve()
    raw = noisy(loss, 0.02)
    check = assess_curve(raw, smooth_ema(raw, 0.9))
    assert not check.flat
    assert check.rel_drop > 0.5


def test_a_barely_falling_noisy_curve_is_flat():
    lrs = np.logspace(-7, 0, 60)
    loss = 0.95 - 0.05 * np.exp(-((np.log10(lrs) + 4) ** 2)) + (np.log10(lrs) > -1) * 0.5
    raw = noisy(loss, 0.08, seed=3)
    check = assess_curve(raw, smooth_ema(raw, 0.9))
    assert check.flat
    assert "flat" in check.reason


def test_a_small_but_clean_drop_below_the_minimum_relative_drop_is_flat():
    raw = np.concatenate([np.linspace(1.0, 0.97, 30), np.linspace(0.97, 3.0, 30)])
    check = assess_curve(raw, smooth_ema(raw, 0.9))
    assert check.flat, "3 % is below min_rel_drop"


def test_a_flat_range_test_uses_the_fallback_and_says_so():
    def constant(model, batch):  # the loss does not depend on the model: nothing to learn
        return torch.tensor(1.0) + 0.0 * model.weight.sum()

    cfg = LrFindConfig(**{**CFG.__dict__, "fallback_lr": 3e-3})
    res = find_lr(tiny(), constant, regression(), cfg)
    assert res.flat
    assert res.suggested_lr == 3e-3
    assert res.rule == "fallback"
    assert "inconclusive" in res.warning
    assert res.to_dict()["flat"] is True


def test_lr_kwargs_passes_the_numeric_default_as_the_fallback():
    kw = lr_kwargs({}, default_lr=1e-3, batch_size=16, seed=0, weight_decay=0.0)
    assert kw["lr_find"].fallback_lr == 1e-3
