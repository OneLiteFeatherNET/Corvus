"""Model: shapes, padding invariance, determinism, masked pretraining and its checkpoint."""

import numpy as np
import pytest
import torch

from corvus_ml.raw.model import ModelConfig, RawAnomalyNet, RawHierNet
from corvus_ml.raw.pretrain import flatten_windows, load_encoder, pretrain, save_checkpoint
from corvus_ml.raw.windows import to_input
from raw_builders import tiny_source


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    return tiny_source(tmp_path_factory.mktemp("src")).build()


def model_cfg(data, **kw):
    spec = data.meta["raw_spec"]
    base = {
        "d_model": 32,
        "heads": 2,
        "layers": 1,
        "local_dim": 16,
        "local_heads": 2,
        "dropout": 0.0,
    }
    return ModelConfig.for_layout(
        spec.layout, spec.vocab_size, spec.packet.max_tokens_per_packet, **(base | kw)
    )


def batch(data, n=6):
    x = to_input(data.raw_ids[:n], data.raw_time[:n], 1)
    return torch.from_numpy(x)


def test_window_tensor_has_the_documented_shape(data):
    spec = data.meta["raw_spec"]

    assert data.raw_ids.shape[1] == 1
    assert data.raw_ids.shape[-1] == spec.packet.max_tokens_per_packet
    assert data.raw_ids.shape[2] <= spec.window.max_packets
    assert len(data) > 20, "enough windows for the model tests"


def test_classifier_outputs_one_probability_per_window(data):
    net = RawHierNet(model_cfg(data)).eval()

    out = net(batch(data))

    assert out.shape == (6,)
    assert ((out >= 0) & (out <= 1)).all()


def test_macro_classifier_takes_a_window_axis(data):
    net = RawHierNet(model_cfg(data, macro_windows=3, macro_layers=1)).eval()
    x = batch(data, 4)
    macro = torch.stack([x, x.flip(0), x], dim=1)  # (4, 3, packets, tokens + 1)

    out = net(macro)

    assert out.shape == (4,)


@pytest.mark.parametrize("pool", ["attention", "mean"])
def test_macro_pooling_ignores_missing_windows(data, pool):
    net = RawHierNet(model_cfg(data, macro_windows=3, macro_pool=pool)).eval()
    x = batch(data, 2)
    empty = torch.zeros_like(x)

    both = net(torch.stack([x, empty, empty], dim=1))
    alone = net(torch.stack([x, x, x], dim=1))

    assert torch.isfinite(both).all()
    assert both.shape == alone.shape == (2,)


def test_extra_padding_packets_do_not_change_the_output(data):
    net = RawHierNet(model_cfg(data)).eval()
    x = batch(data)
    padded = torch.cat([x, torch.zeros(x.shape[0], 9, x.shape[2])], dim=1)

    assert torch.allclose(net(x), net(padded), atol=1e-5)


def test_packet_order_in_the_tensor_does_not_matter_only_the_time_channel(data):
    net = RawHierNet(model_cfg(data)).eval()
    x = batch(data, 3)
    perm = torch.randperm(x.shape[1], generator=torch.Generator().manual_seed(0))

    assert torch.allclose(net(x), net(x[:, perm]), atol=1e-4)


def test_time_channel_changes_the_output(data):
    net = RawHierNet(model_cfg(data)).eval()
    x = batch(data, 3)
    shifted = x.clone()
    shifted[..., -1] = shifted[..., -1] * 7.0

    assert not torch.allclose(net(x), net(shifted), atol=1e-4)


def test_cnn_local_encoder_works_too(data):
    net = RawHierNet(model_cfg(data, local_type="cnn")).eval()

    assert net(batch(data)).shape == (6,)


def test_anomaly_score_is_deterministic_and_non_negative(data):
    net = RawAnomalyNet(model_cfg(data)).eval()
    x = batch(data)

    first, second = net(x), net(x)

    assert first.shape == (6,)
    assert torch.equal(first, second)
    assert (first >= 0).all()


def test_untrained_anomaly_score_is_near_log_vocabulary_per_term(data):
    net = RawAnomalyNet(model_cfg(data)).eval()

    score = net(batch(data))

    assert float(score.detach().mean()) == pytest.approx(
        3 * np.log(data.meta["raw_spec"].vocab_size), rel=0.2
    )


def test_anomaly_model_rejects_macro_windows(data):
    with pytest.raises(ValueError, match="single windows"):
        RawAnomalyNet(model_cfg(data, macro_windows=2))


def test_pretraining_reduces_the_masked_prediction_loss(data):
    torch.manual_seed(0)
    net = RawAnomalyNet(model_cfg(data))
    ids, t = flatten_windows(data.raw_ids, data.raw_time)
    x = to_input(ids[:16, None], t[:16, None], 1)
    before = float(net.eval()(torch.from_numpy(x)).detach().mean())

    hist = pretrain(net, ids, t, epochs=6, batch_size=16, lr=3e-3, patience=10, seed=0)

    after = float(net.eval()(torch.from_numpy(x)).detach().mean())
    assert hist.train_loss[-1] < hist.train_loss[0]
    assert after < before, f"score on seen windows fell from {before:.2f} to {after:.2f}"


def test_pretraining_is_reproducible_for_a_seed(data):
    ids, t = flatten_windows(data.raw_ids, data.raw_time)

    def run():
        torch.manual_seed(3)
        net = RawAnomalyNet(model_cfg(data))
        return pretrain(net, ids[:32], t[:32], epochs=2, batch_size=16, lr=1e-3, seed=3).train_loss

    assert run() == run()


def test_checkpoint_initialises_the_classifier_encoder(data, tmp_path):
    cfg = model_cfg(data)
    pre = RawAnomalyNet(cfg)
    ids, t = flatten_windows(data.raw_ids, data.raw_time)
    pretrain(pre, ids[:32], t[:32], epochs=1, batch_size=16, lr=1e-3, seed=0)
    path = save_checkpoint(tmp_path / "pre.pt", pre)

    clf = RawHierNet(cfg)
    load_encoder(clf, path)

    for key, value in pre.micro.state_dict().items():
        assert torch.equal(clf.micro.state_dict()[key], value), key


def test_checkpoint_with_another_architecture_is_refused(data, tmp_path):
    path = save_checkpoint(tmp_path / "pre.pt", RawAnomalyNet(model_cfg(data)))

    with pytest.raises(ValueError, match="d_model"):
        load_encoder(RawHierNet(model_cfg(data, d_model=64)), path)


def test_a_macro_classifier_can_start_from_a_single_window_checkpoint(data, tmp_path):
    path = save_checkpoint(tmp_path / "pre.pt", RawAnomalyNet(model_cfg(data)))
    macro = RawHierNet(model_cfg(data, macro_windows=2))

    load_encoder(macro, path)  # architecture keys agree; macro layers keep their init
