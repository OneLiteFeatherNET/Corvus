"""A dataset with too few players leaves the validation / test split empty: nothing may be
called on an empty split, and the report says why the held-out metrics are missing."""

from corvus_ml.eval.config import BenchConfig, EvalConfig
from corvus_ml.eval.report import write_report
from corvus_ml.eval.runner import run
from corvus_ml.eval.synthetic import SyntheticConfig

FAST_BENCH = BenchConfig(warmup=1, iters=3, batch=4, isolated=False, quantize=True)


def one_player_cfg(**kw) -> EvalConfig:
    return EvalConfig(
        seed=1,
        seq_len=16,
        unlabelled="assume_legit",
        synthetic=SyntheticConfig(
            seed=5,
            n_legit=0,
            n_cheat=0,
            n_unlabelled=1,
            sessions_per_player=3,
            windows_per_session=5,
            frames_per_window=16,
        ),  # fmt: skip
        candidates={"isolation_forest": {"n_estimators": 20}},
        bench=FAST_BENCH,
        **kw,
    )


def test_a_single_player_gives_no_test_metrics_but_a_checked_onnx_graph(tmp_path):
    report = run(one_player_cfg(), tmp_path)

    cand = report["contexts"]["pvp"]["candidates"]["isolation_forest"]
    assert cand["status"] == "ok"
    assert cand["test"] is None
    assert cand["test_skipped"] == "test split empty: needs at least 2 players per label stratum"
    assert cand["onnx"]["parity_ok"], "the graph must still be checked on real windows"
    assert cand["onnx"]["parity_on"] == "train"
    assert cand["confusion_at_val_threshold"] == {}


def test_the_report_of_a_single_player_run_renders_and_names_the_reason(tmp_path):
    report = run(one_player_cfg(), tmp_path)

    _, md = write_report(report, tmp_path)

    assert "No test metrics:" in md.read_text()
    assert "test split empty" in md.read_text()


def test_the_learning_curve_is_left_out_without_a_test_split(tmp_path):
    report = run(one_player_cfg(learning_curve=(0.5, 1.0)), tmp_path)

    assert "learning_curve" not in report["contexts"]["pvp"]["candidates"]["isolation_forest"]


def test_a_session_split_gives_a_single_player_a_test_split(tmp_path):
    report = run(one_player_cfg(split_by="session"), tmp_path)

    info = report["contexts"]["pvp"]
    assert report["split_by"] == "session"
    assert info["split"]["test"]["windows"] > 0
    assert info["candidates"]["isolation_forest"]["test"] is not None


def test_the_report_of_a_session_split_warns_about_leakage(tmp_path):
    report = run(one_player_cfg(split_by="session"), tmp_path)

    _, md = write_report(report, tmp_path)

    assert "Split by session, not by player" in md.read_text()


def test_split_by_defaults_to_player_and_rejects_other_values():
    import pytest

    base = {"synthetic": {}, "candidates": {"isolation_forest": {}}}
    assert EvalConfig.from_dict(base).split_by == "player"
    with pytest.raises(ValueError, match="split_by"):
        EvalConfig.from_dict({**base, "run": {"split_by": "window"}})
