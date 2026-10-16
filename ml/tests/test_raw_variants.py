"""Raw-byte model variants of the tokenizer ablation: plain-byte tokenizer, packet-type position
embedding, MAE-style masking, entropy-weighted loss, JEPA latent objective, kNN combination."""

from dataclasses import replace

import numpy as np
import onnxruntime as ort
import pytest
import torch

from corvus_ml.features.spec import FeatureSpec
from corvus_ml.raw import layout as L
from corvus_ml.raw.entropy import byte_entropy_weights
from corvus_ml.raw.model import ModelConfig, RawAnomalyNet, RawHierNet, type_bucket
from corvus_ml.raw.pretrain import flatten_windows, pretrain
from corvus_ml.raw.spec import PacketConfig, RawBytesSpec, WindowConfig
from corvus_ml.raw.tokenizer import RawTokenizer, TokenizerConfig
from corvus_ml.raw.windows import to_input
from raw_builders import SMALL_LAYOUT, small_tokenizer, tiny_source, tiny_synthetic


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    return tiny_source(tmp_path_factory.mktemp("variants")).build()


def model_cfg(data, **kw):
    spec = data.meta["raw_spec"]
    base = {"d_model": 32, "heads": 2, "layers": 1, "local_dim": 16, "local_heads": 2}
    base["dropout"] = 0.0
    max_tokens = kw.pop("max_tokens", spec.packet.max_tokens_per_packet)
    return ModelConfig.for_layout(spec.layout, spec.vocab_size, max_tokens, **(base | kw))


def batch(data, n=6):
    return torch.from_numpy(to_input(data.raw_ids[:n], data.raw_time[:n], 1))


def export_and_run(net, x, tmp_path, name="m"):
    net.eval()
    path = tmp_path / f"{name}.onnx"
    torch.onnx.export(
        net, (x[:2],), str(path), input_names=["x"], output_names=["score"],
        dynamic_axes={"x": {0: "batch", 1: "packets"}, "score": {0: "batch"}},
        opset_version=17, dynamo=False,
    )  # fmt: skip
    sess = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    return sess.run(None, {"x": x.numpy()})[0]


# --- 1. plain-byte tokenizer ------------------------------------------------------------------


@pytest.fixture(scope="module")
def syn():
    return tiny_synthetic()


@pytest.fixture(scope="module")
def byte_tok(syn):
    cfg = TokenizerConfig(mode="bytes", layout=SMALL_LAYOUT)
    return RawTokenizer.train((p for sp in syn.sessions for p in sp.payloads if p), cfg)


def test_byte_tokenizer_has_one_token_per_byte_after_the_reserved_ids(byte_tok):
    n = byte_tok.layout.n_special

    assert byte_tok.mode == "bytes"
    assert byte_tok.vocab_size == n + 256
    assert byte_tok.encode(bytes(range(256))) == list(range(n, n + 256))


def test_byte_tokenizer_round_trips_arbitrary_bytes(byte_tok):
    rng = np.random.default_rng(4)
    for size in (0, 1, 17, 300):
        data = rng.integers(0, 256, size, dtype=np.uint8).tobytes()

        assert byte_tok.decode(byte_tok.encode(data)) == data


def test_byte_tokens_never_collide_with_reserved_ids(byte_tok):
    ids = byte_tok.encode(b"[PAD][SEP][MASK][CLS][PKT_1][DT_0]\x00\xff")

    assert min(ids) >= byte_tok.layout.n_special


def test_byte_tokenizer_is_deterministic_and_does_not_depend_on_the_data(syn, byte_tok, tmp_path):
    other = RawTokenizer.train([b"abc"], TokenizerConfig(mode="bytes", layout=SMALL_LAYOUT))
    byte_tok.save(tmp_path / "a")
    other.save(tmp_path / "b")

    assert (tmp_path / "a/tokenizer.json").read_bytes() == (
        tmp_path / "b/tokenizer.json"
    ).read_bytes()


def test_unknown_tokenizer_mode_is_refused():
    with pytest.raises(ValueError, match="mode"):
        TokenizerConfig(mode="words")


def test_byte_tokenizer_survives_save_and_load_with_its_spec(byte_tok, tmp_path):
    byte_tok.save(tmp_path)
    spec = byte_tok.spec(tmp_path, PacketConfig(), WindowConfig())

    loaded = RawTokenizer.load(tmp_path, spec)

    assert loaded.mode == "bytes"
    assert loaded.encode(b"\x01\x02") == byte_tok.encode(b"\x01\x02")


def test_spec_hash_and_mode_differ_between_tokenizers(syn, byte_tok, tmp_path):
    bpe = small_tokenizer(syn)
    bpe.save(tmp_path / "bpe")
    byte_tok.save(tmp_path / "bytes")
    a = bpe.spec(tmp_path / "bpe", PacketConfig(), WindowConfig())
    b = byte_tok.spec(tmp_path / "bytes", PacketConfig(), WindowConfig())

    diffs = a.differences(b)

    assert (a.tokenizer_mode, b.tokenizer_mode) == ("bpe", "bytes")
    assert a.tokenizer_sha256 != b.tokenizer_sha256
    assert any("tokenizer.mode" in d for d in diffs), diffs
    assert FeatureSpec.current(raw_bytes=a).to_dict() != FeatureSpec.current(raw_bytes=b).to_dict()


def test_spec_without_a_mode_is_a_bpe_spec(syn, tmp_path):
    bpe = small_tokenizer(syn)
    bpe.save(tmp_path)
    raw = bpe.spec(tmp_path, PacketConfig(), WindowConfig()).to_dict()
    del raw["tokenizer"]["mode"]

    assert RawBytesSpec.from_dict(raw).tokenizer_mode == "bpe"


def test_bytes_tokenizer_feeds_the_whole_pipeline(tmp_path):
    cfg = TokenizerConfig(mode="bytes", layout=SMALL_LAYOUT)
    data = tiny_source(
        tmp_path, tokenizer=cfg, packet=PacketConfig(max_tokens_per_packet=24)
    ).build()
    spec = data.meta["raw_spec"]

    assert spec.tokenizer_mode == "bytes"
    assert spec.vocab_size == SMALL_LAYOUT.n_special + 256
    assert data.raw_ids.max() < spec.vocab_size


# --- 2. packet-type-conditioned position embedding --------------------------------------------


def with_type(ids, type_id):
    out = ids.clone()
    out[..., 1] = torch.where(ids[..., 1] != 0, torch.tensor(SMALL_LAYOUT.pkt_id(type_id)), 0)
    return out


def test_type_bucket_separates_known_types_and_maps_unknown_to_zero(data):
    cfg = model_cfg(data, type_pos=True, type_buckets=32)
    ids = torch.tensor([[[5, SMALL_LAYOUT.pkt_id(3), 11], [5, SMALL_LAYOUT.pkt_id(4), 11]]])
    other = torch.tensor([[[5, L.PKT_OTHER_ID, 11], [5, L.MASK_ID, 11], [0, 0, 0]]])

    b = type_bucket(ids, cfg)

    assert b[0, 0] != b[0, 1]
    assert (b > 0).all()
    assert (type_bucket(other, cfg) == 0).all()


def test_position_embedding_depends_on_the_packet_type_only_when_enabled(data):
    ids = batch(data, 2)[..., :-1].round().long()
    on = RawHierNet(model_cfg(data, type_pos=True)).eval().micro
    off = RawHierNet(model_cfg(data)).eval().micro
    a, b = with_type(ids, 3), with_type(ids, 7)
    real = a[..., 0] != 0

    differs = (on.position_embedding(a) != on.position_embedding(b)).any(-1)[real][:, 4:].all()
    assert differs, "same byte offset, other packet type: other embedding"
    assert torch.equal(on.position_embedding(a), on.position_embedding(a))
    assert torch.equal(off.position_embedding(a), off.position_embedding(b))


def test_type_conditioned_model_scores_other_packet_types_differently(data):
    net = RawAnomalyNet(model_cfg(data, type_pos=True)).eval()
    x = batch(data)
    other = x.clone()
    other[..., :-1] = with_type(x[..., :-1].round().long(), 9).float()

    assert not torch.allclose(net(x), net(other), atol=1e-6)


@pytest.mark.parametrize("cls", [RawHierNet, RawAnomalyNet])
def test_type_conditioned_model_exports_to_onnx_with_identical_outputs(data, tmp_path, cls):
    torch.manual_seed(0)
    net = cls(model_cfg(data, type_pos=True, type_buckets=16)).eval()
    x = batch(data)

    got = export_and_run(net, x, tmp_path)

    with torch.no_grad():
        want = net(x).numpy()
    np.testing.assert_allclose(got, want, rtol=1e-4, atol=1e-4)


def test_default_model_has_no_type_embedding(data):
    net = RawHierNet(model_cfg(data))

    assert not any("type" in k for k in net.state_dict())


# --- 3. MAE-style masking ----------------------------------------------------------------------


def fake_ids(n=48, p=48, length=24, seed=0):
    g = torch.Generator().manual_seed(seed)
    ids = torch.randint(
        SMALL_LAYOUT.n_special, SMALL_LAYOUT.n_special + 256, (n, p, length), generator=g
    )
    ids[..., 0], ids[..., 1] = L.DIR_SB_ID, SMALL_LAYOUT.pkt_id(2)
    return ids


@pytest.mark.parametrize("ratio", [0.1, 0.3, 0.6, 0.9])
def test_packet_mask_ratio_is_respected(data, ratio):
    torch.manual_seed(1)
    net = RawAnomalyNet(model_cfg(data, p_packet=ratio))
    ids = fake_ids(length=16)

    _, pkt = net._masks(ids, ids[..., 0] != 0, True, 0)

    assert float(pkt.float().mean()) == pytest.approx(ratio, abs=0.04)


def test_mask_ratio_outside_the_supported_range_is_refused(data):
    with pytest.raises(ValueError, match="p_packet"):
        model_cfg(data, p_packet=1.0)
    with pytest.raises(ValueError, match="span_mean"):
        model_cfg(data, span_mean=0.5)


def run_lengths(mask):
    """Lengths of the runs of True along the last axis."""
    out = []
    for row in mask.reshape(-1, mask.shape[-1]).tolist():
        n = 0
        for v in [*row, False]:
            if v:
                n += 1
            elif n:
                out.append(n)
                n = 0
    return out


def test_byte_span_masking_has_the_configured_mean_span_and_token_ratio(data):
    torch.manual_seed(2)
    net = RawAnomalyNet(model_cfg(data, p_token=0.2, span_mean=4.0))
    ids = fake_ids(length=16)

    tok, _ = net._masks(ids, ids[..., 0] != 0, True, 0)

    assert float(tok[..., 3:].float().mean()) == pytest.approx(0.2, abs=0.04)
    assert np.mean(run_lengths(tok[..., 3:])) > 2.5, "spans, not independent positions"


def test_single_token_masking_stays_independent_by_default(data):
    torch.manual_seed(2)
    net = RawAnomalyNet(model_cfg(data, p_token=0.2))
    ids = fake_ids(length=16)

    tok, _ = net._masks(ids, ids[..., 0] != 0, True, 0)

    assert np.mean(run_lengths(tok[..., 3:])) < 1.6


def test_full_packet_masking_hides_every_byte_of_a_masked_packet(data):
    cfg = model_cfg(data, p_packet=0.5, packet_mask="full")
    net = RawAnomalyNet(cfg)
    ids = fake_ids(length=16)
    torch.manual_seed(0)

    tok, pkt = net._masks(ids, ids[..., 0] != 0, True, 0)

    assert bool(tok[pkt][:, 3:].all())


@pytest.mark.parametrize(
    "extra", [{"span_mean": 3.0, "p_packet": 0.5, "packet_mask": "full"}, {"p_packet": 0.9}]
)
def test_mae_options_train_and_export(data, extra, tmp_path):
    torch.manual_seed(0)
    net = RawAnomalyNet(model_cfg(data, **extra))
    ids, t = flatten_windows(data.raw_ids, data.raw_time)

    hist = pretrain(net, ids[:32], t[:32], epochs=2, batch_size=16, lr=3e-3, seed=0)

    assert np.isfinite(hist.train_loss).all()
    x = batch(data)
    np.testing.assert_allclose(export_and_run(net, x, tmp_path), net(x).detach().numpy(), atol=1e-3)


# --- 4. entropy-weighted byte loss -------------------------------------------------------------


def synthetic_positions():
    rng = np.random.default_rng(0)
    ids = np.zeros((40, 8, 12), dtype=np.int16)
    ids[..., 0] = L.DIR_SB_ID
    ids[..., 1] = SMALL_LAYOUT.pkt_id(5)
    ids[..., 3] = SMALL_LAYOUT.n_special + 7  # always the same byte: constant
    ids[..., 4] = SMALL_LAYOUT.n_special + rng.integers(0, 256, ids[..., 4].shape)  # random
    ids[..., 5] = SMALL_LAYOUT.n_special + rng.integers(0, 2, ids[..., 5].shape)  # two values
    ids[..., 6:] = SMALL_LAYOUT.n_special + 1
    return ids


def entropy_cfg(data, **kw):
    return model_cfg(data, max_tokens=12, type_buckets=16, **kw)


def test_entropy_weights_are_lower_for_random_than_for_constant_positions(data):
    cfg = entropy_cfg(data)
    bucket = int(type_bucket(torch.tensor(synthetic_positions()[:1, :1]).long(), cfg)[0, 0])

    w = byte_entropy_weights(synthetic_positions(), cfg)

    assert w.shape == (cfg.type_buckets, cfg.max_tokens)
    assert w[bucket, 4] < w[bucket, 5] < w[bucket, 3]
    assert w[bucket, 4] < 0.2 * w[bucket, 3]


def test_entropy_weights_are_deterministic_and_neutral_without_evidence(data):
    cfg = entropy_cfg(data)
    ids = synthetic_positions()
    unseen = int(type_bucket(torch.tensor(ids[:1, :1]).long(), cfg)[0, 0]) + 1

    a, b = byte_entropy_weights(ids, cfg), byte_entropy_weights(ids, cfg)

    assert np.array_equal(a, b)
    assert (a[unseen] == 1.0).all(), "a packet type never seen in training keeps weight 1"
    assert a.mean() > 0


def test_weighted_loss_differs_and_is_stored_with_the_model(data):
    cfg = model_cfg(data, entropy_weight=True)
    ids, t = flatten_windows(data.raw_ids, data.raw_time)
    plain = RawAnomalyNet(cfg)
    weighted = RawAnomalyNet(cfg)
    weighted.load_state_dict(plain.state_dict())
    weighted.set_byte_weights(byte_entropy_weights(ids, cfg))
    x_ids, x_t = torch.from_numpy(ids[:8]).long(), torch.from_numpy(t[:8])
    plain.train()
    weighted.train()

    torch.manual_seed(0)
    a = plain.pretrain_loss(x_ids, x_t)
    torch.manual_seed(0)
    b = weighted.pretrain_loss(x_ids, x_t)

    assert float(a) != pytest.approx(float(b))
    assert "byte_w" in weighted.state_dict()
    assert not torch.equal(weighted.byte_w, torch.ones_like(weighted.byte_w))


def test_weights_do_not_change_the_score_unless_asked_to(data):
    cfg = model_cfg(data)
    ids, t = flatten_windows(data.raw_ids, data.raw_time)
    base = RawAnomalyNet(cfg).eval()
    other = RawAnomalyNet(model_cfg(data, entropy_weight=True)).eval()
    other.load_state_dict(base.state_dict(), strict=False)
    other.set_byte_weights(byte_entropy_weights(ids, other.cfg))
    x = batch(data)

    assert torch.allclose(base(x), other(x), atol=1e-6)


# --- 5. JEPA latent objective --------------------------------------------------------------------


def jepa_cfg(data, **kw):
    return model_cfg(data, objective="jepa", ema_momentum=0.9, p_packet=0.4, **kw)


def test_jepa_target_encoder_is_an_ema_copy_without_gradients(data):
    torch.manual_seed(0)
    net = RawAnomalyNet(jepa_cfg(data))
    ids, t = flatten_windows(data.raw_ids, data.raw_time)
    assert all(not p.requires_grad for p in net.target.parameters())
    opt = torch.optim.SGD([p for p in net.parameters() if p.requires_grad], lr=0.1)
    x_ids, x_t = torch.from_numpy(ids[:8]).long(), torch.from_numpy(t[:8])
    before = {k: v.clone() for k, v in net.target.state_dict().items()}
    net.train()

    for _ in range(3):
        opt.zero_grad()
        net.pretrain_loss(x_ids, x_t).backward()
        opt.step()

    moved = [k for k, v in net.target.state_dict().items() if v.dtype.is_floating_point
             and not torch.equal(v, before[k])]  # fmt: skip
    assert moved, "EMA update moved the target"
    assert all(p.grad is None for p in net.target.parameters())
    online = net.micro.state_dict()
    assert any(not torch.equal(online[k], v) for k, v in net.target.state_dict().items()
               if v.dtype.is_floating_point)  # fmt: skip


def test_jepa_loss_decreases_on_synthetic_data(data):
    torch.manual_seed(0)
    net = RawAnomalyNet(jepa_cfg(data))
    ids, t = flatten_windows(data.raw_ids, data.raw_time)

    hist = pretrain(net, ids, t, epochs=8, batch_size=16, lr=3e-3, patience=20, seed=0)

    assert hist.train_loss[-1] < hist.train_loss[0], hist.train_loss


@pytest.mark.parametrize("loss", ["smooth_l1", "cosine"])
def test_jepa_score_is_deterministic_non_negative_and_exports_in_one_graph(data, tmp_path, loss):
    torch.manual_seed(0)
    net = RawAnomalyNet(jepa_cfg(data, jepa_loss=loss)).eval()
    x = batch(data)

    with torch.no_grad():
        first, second = net(x), net(x)
    got = export_and_run(net, x, tmp_path, loss)

    assert torch.equal(first, second)
    assert (first >= 0).all()
    np.testing.assert_allclose(got, first.numpy(), rtol=1e-4, atol=1e-4)
    import onnx

    ops = {n.op_type for n in onnx.load(str(tmp_path / f"{loss}.onnx")).graph.node}
    assert not {n for n in ops if n.startswith("Py") or n == "Custom"}


def test_objective_both_adds_the_reconstruction_terms(data):
    torch.manual_seed(0)
    only = RawAnomalyNet(jepa_cfg(data)).eval()
    both = RawAnomalyNet(replace(only.cfg, objective="both")).eval()

    assert hasattr(both, "tok_head")
    assert not hasattr(only, "tok_head")
    assert float(both(batch(data)).mean()) > float(only(batch(data)).mean())


def test_unknown_objective_is_refused(data):
    with pytest.raises(ValueError, match="objective"):
        model_cfg(data, objective="gan")


# --- 6. kNN score combination ------------------------------------------------------------------


def fitted_memory(data, combine, seed=0, **kw):
    torch.manual_seed(0)
    net = RawAnomalyNet(
        model_cfg(data, combine=combine, **({"knn_bank": 24, "knn_k": 3} | kw))
    ).eval()
    ids, t = flatten_windows(data.raw_ids, data.raw_time)
    net.fit_memory(ids, t, seed=seed)
    return net


@pytest.mark.parametrize("combine", ["rank", "zsum"])
def test_knn_combination_is_deterministic(data, combine):
    a, b = fitted_memory(data, combine), fitted_memory(data, combine)
    x = batch(data)

    assert torch.equal(a.bank, b.bank)
    assert torch.equal(a(x), b(x))
    assert a.bank.shape == (24, a.cfg.d_model)
    assert torch.allclose(a.bank.norm(dim=-1), torch.ones(24), atol=1e-5)


def test_knn_bank_depends_on_the_seed(data):
    a = fitted_memory(data, "rank", 0, knn_bank=8)
    b = fitted_memory(data, "rank", 1, knn_bank=8)

    assert not torch.equal(a.bank, b.bank)


def test_rank_combination_stays_in_the_unit_interval_and_differs_from_plain(data):
    net = fitted_memory(data, "rank")
    plain = RawAnomalyNet(replace(net.cfg, combine="none")).eval()
    plain.load_state_dict(net.state_dict(), strict=False)
    x = batch(data)

    s = net(x)

    assert ((s >= 0) & (s <= 1)).all()
    assert not torch.allclose(s, plain(x))


def test_unfitted_memory_falls_back_to_the_reconstruction_score(data):
    cfg = model_cfg(data, combine="rank", knn_bank=8)
    net = RawAnomalyNet(cfg).eval()
    base = RawAnomalyNet(model_cfg(data)).eval()
    base.load_state_dict(net.state_dict(), strict=False)

    assert torch.allclose(net(batch(data)), base(batch(data)))


@pytest.mark.parametrize("combine", ["rank", "zsum"])
def test_knn_combination_exports_to_onnx(data, tmp_path, combine):
    net = fitted_memory(data, combine)
    x = batch(data)

    got = export_and_run(net, x, tmp_path, combine)

    np.testing.assert_allclose(got, net(x).detach().numpy(), rtol=1e-4, atol=1e-4)


def test_memory_bank_is_capped_by_the_number_of_windows(data):
    torch.manual_seed(0)
    net = RawAnomalyNet(model_cfg(data, combine="zsum", knn_bank=100000, knn_k=3)).eval()
    ids, t = flatten_windows(data.raw_ids, data.raw_time)

    net.fit_memory(ids, t, seed=0)

    assert net.bank.shape[0] == len(ids)


# --- checkpoints ---------------------------------------------------------------------------------


def test_checkpoint_from_before_the_variants_loads_into_a_default_model(data, tmp_path):
    from corvus_ml.raw.pretrain import load_encoder, save_checkpoint

    path = save_checkpoint(tmp_path / "old.pt", RawAnomalyNet(model_cfg(data)))
    blob = torch.load(path, weights_only=True)
    for key in ("type_pos", "type_buckets", "pkt_lo", "pkt_hi", "objective", "combine"):
        blob["config"].pop(key)
    torch.save(blob, path)

    load_encoder(RawHierNet(model_cfg(data)), path)


def test_checkpoint_with_another_position_embedding_is_refused(data, tmp_path):
    from corvus_ml.raw.pretrain import load_encoder, save_checkpoint

    path = save_checkpoint(tmp_path / "pre.pt", RawAnomalyNet(model_cfg(data)))

    with pytest.raises(ValueError, match="type_pos"):
        load_encoder(RawHierNet(model_cfg(data, type_pos=True)), path)
