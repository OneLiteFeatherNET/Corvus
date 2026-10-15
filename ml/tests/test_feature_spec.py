"""The versioned FeatureSpec: serialisation, validation on load, and the model export."""

import json

import pytest

from corvus_ml.eval.config import EvalConfig
from corvus_ml.eval.runner import resolve_spec, run
from corvus_ml.features.config import FeaturizerConfig
from corvus_ml.features.pipeline import build
from corvus_ml.features.spec import (
    FEATURE_SPEC_VERSION,
    SPEC_FILE,
    FeatureSpec,
    FeatureSpecError,
    FeatureSpecMismatch,
)
from golden_lake import write_golden_events
from golden_lake_cli import GOLDEN_CONFIG, golden_segmap


def written(tmp_path, spec: FeatureSpec):
    spec.write(tmp_path)
    return tmp_path / SPEC_FILE


def edit(path, change) -> None:
    raw = json.loads(path.read_text())
    change(raw)
    path.write_text(json.dumps(raw))


def test_spec_survives_a_write_read_round_trip(tmp_path):
    spec = FeatureSpec.current(
        FeaturizerConfig(window=64, overlap=0.25, dt_edges_ms=(1.0, 9.0)), 48
    )
    assert FeatureSpec.read(written(tmp_path, spec)) == spec
    assert FeatureSpec.read(tmp_path) == spec, "a directory resolves to its feature_spec.json"


def test_spec_file_is_stable_json_with_the_documented_keys(tmp_path):
    raw = json.loads(written(tmp_path, FeatureSpec.current()).read_text())
    assert list(raw) == [
        "name",
        "version",
        "window",
        "aggregate_names",
        "token_columns",
        "model_inputs",
    ]
    assert raw["name"] == "corvus-features"
    assert raw["version"] == FEATURE_SPEC_VERSION
    assert set(raw["window"]) == {
        "window",
        "overlap",
        "max_duration_s",
        "min_frames",
        "dt_edges_ms",
        "entity_cache",
    }
    assert raw["model_inputs"]["seq"]["columns"][0] == {"name": "event_kind", "transform": "scale8"}
    assert FeatureSpec.current().to_json() == FeatureSpec.current().to_json()


def test_load_accepts_a_spec_that_matches_this_checkout(tmp_path):
    spec = FeatureSpec.current(FeaturizerConfig(window=32, min_frames=4), seq_len=32)
    assert FeatureSpec.load(written(tmp_path, spec)) == spec


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        (lambda r: r.update(version=FEATURE_SPEC_VERSION + 1), "version"),
        (lambda r: r.update(name="other"), "name"),
        (lambda r: r["token_columns"].reverse(), "token_columns"),
        (lambda r: r["aggregate_names"].pop(), "aggregate_names"),
        (lambda r: r["model_inputs"]["agg"]["columns"].pop(), "model_inputs.agg"),
        (lambda r: r["model_inputs"]["seq"]["columns"].pop(), "model_inputs.seq"),
        (
            lambda r: r["model_inputs"]["seq"]["columns"][0].update(transform="slog"),
            "model_inputs.seq",
        ),
    ],
)
def test_load_rejects_a_spec_from_different_feature_definitions(tmp_path, change, fragment):
    path = written(tmp_path, FeatureSpec.current())
    edit(path, change)
    with pytest.raises(FeatureSpecMismatch, match=fragment) as err:
        FeatureSpec.load(path)
    assert "model has" in str(err.value), "the message says which side holds which value"


def test_load_takes_window_parameters_from_the_file_unless_told_otherwise(tmp_path):
    spec = FeatureSpec.current(FeaturizerConfig(window=64, overlap=0.0), seq_len=64)
    path = written(tmp_path, spec)
    assert FeatureSpec.load(path).window.window == 64
    with pytest.raises(FeatureSpecMismatch, match=r"window\.window"):
        FeatureSpec.load(path, expected=FeatureSpec.current())


def test_mismatch_lists_every_difference(tmp_path):
    path = written(tmp_path, FeatureSpec.current())
    edit(path, lambda r: (r.update(version=99), r["token_columns"].pop()))
    with pytest.raises(FeatureSpecMismatch) as err:
        FeatureSpec.load(path)
    text = str(err.value)
    assert "version" in text
    assert "token_columns" in text


def test_unreadable_files_raise_a_clear_error(tmp_path):
    with pytest.raises(FeatureSpecError, match="no feature spec"):
        FeatureSpec.read(tmp_path / "missing.json")
    bad = tmp_path / SPEC_FILE
    bad.write_text("{not json")
    with pytest.raises(FeatureSpecError, match="not valid JSON"):
        FeatureSpec.read(bad)
    bad.write_text(json.dumps({"name": "corvus-features"}))
    with pytest.raises(FeatureSpecError, match="malformed"):
        FeatureSpec.read(bad)


def test_spec_with_an_invalid_window_is_malformed_not_a_crash(tmp_path):
    path = written(tmp_path, FeatureSpec.current())
    edit(path, lambda r: r["window"].update(overlap=1.5))
    with pytest.raises(FeatureSpecError, match="malformed"):
        FeatureSpec.read(path)


def test_feature_build_writes_the_spec_of_its_window_parameters(tmp_path):
    out = tmp_path / "out"
    build(write_golden_events(tmp_path), golden_segmap(), out, GOLDEN_CONFIG, group_size=1)
    spec = FeatureSpec.load(out)
    assert spec.window == GOLDEN_CONFIG.featurizer()


def test_eval_picks_up_the_spec_next_to_the_windows_and_checks_it(tmp_path):
    out = tmp_path / "lake"
    build(write_golden_events(tmp_path), golden_segmap(), out, GOLDEN_CONFIG, group_size=1)
    cfg = EvalConfig(windows=out / "windows", sessions=tmp_path / "s", seq_len=40)
    spec = resolve_spec(cfg)
    assert spec.window == GOLDEN_CONFIG.featurizer()
    assert spec.seq_len == 40, "the sequence length is an evaluation setting"

    edit(out / SPEC_FILE, lambda r: r.update(version=FEATURE_SPEC_VERSION + 1))
    with pytest.raises(FeatureSpecMismatch):
        resolve_spec(cfg)


def test_eval_export_stores_the_spec_next_to_the_models(tmp_path):
    config = EvalConfig.from_dict(
        {
            "name": "spec",
            "run": {"seed": 3, "seq_len": 24},
            "synthetic": {
                "n_legit": 8,
                "n_cheat": 6,
                "windows_per_session": 3,
                "frames_per_window": 24,
                "contexts": ["pvp"],
            },
            "candidates": {"isolation_forest": {"n_estimators": 10}},
            "benchmark": {"enabled": False},
        }
    )
    report = run(config, tmp_path)
    assert report["contexts"]["pvp"]["candidates"]["isolation_forest"]["status"] == "ok"
    exported = tmp_path / "onnx" / "pvp"
    assert (exported / "isolation_forest.onnx").is_file()
    spec = FeatureSpec.load(exported)
    assert spec == FeatureSpec.current(seq_len=24)
