from __future__ import annotations

import numpy as np
import pytest

from corvus_services.scoring.models import (
    OutputSemantics,
    card_key,
    output_semantics,
    scores_from_outputs,
)

CARD = {"output_semantics": {"onnx_output": "score", "range": [0, 1], "meaning": "P(cheat)"}}


def test_the_calibrated_suffix_declares_a_calibrated_output():
    assert output_semantics("isolation_forest_calibrated", None) == OutputSemantics("score", True)


def test_a_model_card_with_the_unit_range_declares_a_calibrated_output():
    assert output_semantics("cnn1d", CARD) == OutputSemantics("score", True)


def test_the_model_card_names_the_output():
    card = {"output_semantics": {"onnx_output": "p_cheat", "range": [0, 1]}}

    assert output_semantics("x", card).output_name == "p_cheat"


@pytest.mark.parametrize("card", [None, {}, {"output_semantics": "text"}, {"output_semantics": {}}])
def test_legacy_models_stay_legacy(card):
    assert output_semantics("cnn1d", card) == OutputSemantics(None, False)


def test_a_calibrated_output_is_picked_by_name_and_not_clipped():
    label = np.array([[0], [1]], dtype=np.int64)
    score = np.array([0.2, 1.0000001], dtype=np.float32)

    out = scores_from_outputs(
        "isolation_forest_calibrated",
        [label, score],
        ("label", "score"),
        OutputSemantics("score", True),
    )

    assert out.tolist() == pytest.approx([0.2, 1.0000001])


def test_a_calibrated_single_output_is_flattened():
    out = scores_from_outputs(
        "x_calibrated", [np.array([[0.1], [0.9]], dtype=np.float32)], ("score",)
    )

    assert out.tolist() == pytest.approx([0.1, 0.9])


def test_the_suffix_alone_is_enough_without_explicit_semantics():
    out = scores_from_outputs("cnn1d_calibrated", [np.array([0.3, 0.4])], ("score",))

    assert out.tolist() == [0.3, 0.4]


def test_legacy_models_are_still_clipped():
    out = scores_from_outputs("cnn1d", [np.array([[1.5], [-0.2], [0.3]])], ("score",))

    assert out.tolist() == pytest.approx([1.0, 0.0, 0.3])


def test_legacy_isolation_forest_keeps_its_raw_score_handling():
    label = np.array([[1], [1]])
    decision = np.array([-0.25, 0.5])

    assert scores_from_outputs("isolation_forest", [label, decision]).tolist() == [0.25, 0.0]


def test_the_model_card_sits_next_to_the_onnx_file():
    assert card_key("models/m/1/model.onnx") == "models/m/1/model_card.json"
