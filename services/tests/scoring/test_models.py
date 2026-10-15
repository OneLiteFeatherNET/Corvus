from __future__ import annotations

import dataclasses

import numpy as np
import pytest
from corvus_ml.features.spec import FeatureSpec
from score_rig import SMALL, Rig, entry

from corvus_services.scoring.models import (
    ModelRejectedError,
    input_kind_of,
    plan_threads,
    scores_from_outputs,
)


async def test_models_in_scored_stages_are_downloaded_validated_and_loaded(tmp_path):
    rig = Rig(tmp_path)
    rig.add_model(entry("m1", stage="shadow"))
    rig.add_model(entry("m2", name="cnn", stage="enforce", ctx="survival"))

    result = await rig.manager.sync()

    assert sorted(result.loaded) == ["m1", "m2"]
    assert [m.entry.id for m in rig.manager.current.for_context("pvp")] == ["m1"]
    assert rig.manager.current.configs_for("survival") == {SMALL}
    assert (tmp_path / "models" / "m1" / "model.onnx").read_bytes() == b"onnx"
    assert rig.metric("corvus_score_models_loaded", stage="enforce") == 1


async def test_a_feature_spec_mismatch_keeps_the_model_out_and_the_others_running(tmp_path):
    rig = Rig(tmp_path)
    rig.add_model(entry("good"))
    stale = dataclasses.replace(FeatureSpec.current(SMALL), version=0)
    rig.add_model(entry("stale", name="old"), spec=stale)

    result = await rig.manager.sync()

    assert result.loaded == ["good"]
    assert "stale" in result.failed
    assert [m.id for m in rig.manager.current.models] == ["good"]
    assert rig.metric("corvus_score_model_load_failures_total", reason="rejected") == 1


async def test_a_rejected_model_is_not_retried_on_every_poll(tmp_path):
    rig = Rig(tmp_path)
    bad = dataclasses.replace(FeatureSpec.current(SMALL), version=0)
    rig.add_model(entry("stale"), spec=bad)
    await rig.manager.sync()

    await rig.manager.sync()

    assert rig.metric("corvus_score_model_load_failures_total", reason="rejected") == 1


async def test_a_missing_spec_file_is_a_failed_load_that_is_retried(tmp_path):
    rig = Rig(tmp_path)
    e = entry("m1")
    rig.catalog.models.append(e)
    rig.files.objects[e.onnx_key] = b"onnx"  # no feature_spec.json yet
    await rig.manager.sync()
    assert len(rig.manager.current) == 0

    rig.files.put_model(e)
    await rig.manager.sync()

    assert len(rig.manager.current) == 1
    assert rig.metric("corvus_score_model_load_failures_total", reason="load") == 1


async def test_an_onnx_file_the_runtime_cannot_open_is_rejected(tmp_path):
    rig = Rig(tmp_path)
    rig.add_model(entry("m1"))
    rig.factory.fail_for.add("m1")

    result = await rig.manager.sync()

    assert "invalid onnx" in result.failed["m1"]
    assert len(rig.manager.current) == 0


async def test_hot_swap_replaces_the_whole_set_in_one_assignment(tmp_path):
    rig = Rig(tmp_path)
    rig.add_model(entry("v1", version="1", stage="enforce"))
    await rig.manager.sync()
    before = rig.manager.current

    rig.catalog.models = [entry("v2", version="2", stage="enforce")]
    rig.files.put_model(rig.catalog.models[0])
    await rig.manager.sync()

    assert [m.entry.version for m in before.models] == ["1"]  # old snapshot is untouched
    assert [m.entry.version for m in rig.manager.current.models] == ["2"]
    assert not (tmp_path / "models" / "v1").exists()  # cache of the retired model is pruned


async def test_a_stage_change_reuses_the_loaded_session(tmp_path):
    rig = Rig(tmp_path)
    e = entry("m1", stage="shadow")
    rig.add_model(e)
    await rig.manager.sync()
    session = rig.manager.current.models[0].session

    rig.catalog.models = [dataclasses.replace(e, stage="canary")]
    await rig.manager.sync()

    loaded = rig.manager.current.models[0]
    assert loaded.entry.stage == "canary"
    assert loaded.session is session
    assert len(rig.factory.created) == 1


async def test_a_registry_outage_keeps_the_loaded_models(tmp_path):
    rig = Rig(tmp_path)
    rig.add_model(entry("m1"))
    await rig.manager.sync()

    rig.catalog.fail_models = True
    await rig.manager.sync()

    assert len(rig.manager.current) == 1
    assert not rig.manager.last_sync_ok
    assert rig.metric("corvus_score_registry_polls_total", result="error") == 1


async def test_a_failing_download_keeps_the_previous_session_of_that_model(tmp_path):
    rig = Rig(tmp_path)
    e = entry("m1")
    rig.add_model(e)
    await rig.manager.sync()

    rig.catalog.models = [dataclasses.replace(e, onnx_key="models/m1/v2/model.onnx")]
    await rig.manager.sync()

    assert len(rig.manager.current) == 1  # previous files keep serving


async def test_models_are_dropped_when_they_leave_the_scored_stages(tmp_path):
    rig = Rig(tmp_path)
    rig.add_model(entry("m1"))
    await rig.manager.sync()

    rig.catalog.models = []
    result = await rig.manager.sync()

    assert result.unloaded == ["m1"]
    assert len(rig.manager.current) == 0


@pytest.mark.parametrize(
    ("cpus", "models", "expected"),
    [
        (1, 3, (1, 1)),
        (4, 1, (4, 1)),
        (4, 2, (2, 2)),
        (4, 8, (1, 4)),
        (6, 4, (1, 4)),
        (0, 0, (1, 1)),
    ],
)
def test_thread_budget_never_exceeds_the_cpus(cpus, models, expected):
    intra, workers = plan_threads(cpus, models)

    assert (intra, workers) == expected
    assert intra * workers <= max(1, cpus)


async def test_sessions_get_the_intra_op_threads_of_the_plan(tmp_path):
    rig = Rig(tmp_path)  # 2 CPUs
    rig.add_model(entry("a", name="a"))
    rig.add_model(entry("b", name="b"))
    await rig.manager.sync()
    assert {m.intra_op_threads for m in rig.manager.current.models} == {1}

    rig.catalog.models = rig.catalog.models[:1]
    await rig.manager.sync()  # one model left: it may use both CPUs

    assert rig.manager.current.models[0].intra_op_threads == 2


def test_input_kind_follows_the_onnx_input_rank():
    spec = FeatureSpec.current(SMALL)

    assert input_kind_of((None, spec.n_agg_inputs), spec) == "agg"
    assert input_kind_of((None, spec.seq_len, spec.n_seq_channels), spec) == "seq"


@pytest.mark.parametrize("shape", [(None, 3), (None, 99, 20), (None,), ("n", 1, 2, 3)])
def test_input_shapes_that_do_not_fit_the_spec_are_rejected(shape):
    with pytest.raises(ModelRejectedError):
        input_kind_of(shape, FeatureSpec.current(SMALL))


def test_sklearn_outputs_are_decoded_like_the_eval_harness():
    label = np.zeros(2)
    probs = np.array([[0.9, 0.1], [0.2, 0.8]])
    decision = np.array([[-0.25], [0.4]])

    assert scores_from_outputs("gradient_boosting", [label, probs]).tolist() == [0.1, 0.8]
    assert scores_from_outputs("isolation_forest", [label, decision]).tolist() == [0.25, 0.0]


def test_other_models_use_the_first_output_clipped_to_the_unit_interval():
    out = np.array([[1.7], [0.3], [-2.0]], dtype=np.float32)

    assert scores_from_outputs("cnn1d", [out]).tolist() == pytest.approx([1.0, 0.3, 0.0])
