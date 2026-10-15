import math

import numpy as np
import pytest
import torch

from corvus_ml.eval.training import fit, lr_at, lr_range_test, seed_everything


def test_lr_schedule_reaches_base_lr_at_the_end_of_warmup():
    assert lr_at(0, 100, 10, 1e-3) == 0.0
    assert lr_at(5, 100, 10, 1e-3) == pytest.approx(5e-4)
    assert lr_at(10, 100, 10, 1e-3) == pytest.approx(1e-3)


def test_lr_schedule_decays_to_the_minimum_at_the_end():
    assert lr_at(100, 100, 10, 1e-3, min_lr=1e-5) == pytest.approx(1e-5)
    assert lr_at(55, 100, 10, 1e-3) == pytest.approx(5e-4)  # cosine midpoint
    assert lr_at(60, 100, 10, 1e-3) < lr_at(50, 100, 10, 1e-3)


def regression(n=256, seed=0):
    g = torch.Generator().manual_seed(seed)
    x = torch.randn(n, 4, generator=g)
    y = x @ torch.tensor([1.0, -2.0, 0.5, 0.0]) + 0.01 * torch.randn(n, generator=g)
    return x, y


def mse(model, batch):
    return torch.nn.functional.mse_loss(model(batch[0]).squeeze(-1), batch[1])


def make_model():
    seed_everything(0)
    return torch.nn.Linear(4, 1)


def test_fit_lowers_the_loss_and_restores_the_best_weights():
    x, y = regression()
    model = make_model()
    hist = fit(
        model, mse, (x[:192], y[:192]), (x[192:], y[192:]), epochs=40, batch_size=32, lr=0.05
    )
    assert hist.train_loss[-1] < hist.train_loss[0]
    assert hist.val_loss[hist.best_epoch] == min(hist.val_loss)


def test_fit_stops_early_when_validation_stops_improving():
    x, y = regression()
    model = make_model()
    # Validation targets are unrelated noise: no improvement after the first epochs.
    noise = torch.randn(64, generator=torch.Generator().manual_seed(1)) * 5
    hist = fit(
        model,
        mse,
        (x[:192], y[:192]),
        (x[192:], noise),
        epochs=200,
        batch_size=32,
        lr=0.05,
        patience=3,
    )
    assert hist.stopped_early
    assert len(hist.train_loss) < 200


def test_fit_is_reproducible_for_a_seed():
    x, y = regression()
    losses = []
    for _ in range(2):
        model = make_model()
        hist = fit(model, mse, (x, y), None, epochs=5, batch_size=32, lr=0.02, seed=4)
        losses.append(hist.train_loss)
    assert losses[0] == losses[1]


def test_lr_range_test_suggests_an_lr_inside_the_scanned_range():
    x, y = regression()
    model = make_model()
    before = [p.clone() for p in model.parameters()]
    res = lr_range_test(model, mse, (x, y), batch_size=32, lr_min=1e-5, lr_max=1.0, steps=40)
    assert len(res.lrs) == len(res.losses) >= 3
    assert 1e-5 <= res.suggested_lr <= 1.0
    assert all(math.isfinite(v) for v in res.losses)
    for p, q in zip(model.parameters(), before, strict=True):
        assert torch.equal(p, q), "the range test must not change the model"


def test_seed_everything_makes_torch_and_numpy_repeat():
    seed_everything(9)
    a = (torch.rand(3).tolist(), np.random.rand(3).tolist())  # noqa: NPY002
    seed_everything(9)
    b = (torch.rand(3).tolist(), np.random.rand(3).tolist())  # noqa: NPY002
    assert a == b
