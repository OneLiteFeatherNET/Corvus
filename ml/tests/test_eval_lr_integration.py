"""``lr = "auto"`` through the raw-byte candidates, the report, Optuna, the CLI and the jobs."""

import json

import pytest

from corvus_ml.eval.cli import main as eval_main
from corvus_ml.eval.report import to_markdown
from corvus_ml.eval.runner import pretrain_checkpoints, run
from test_raw_eval import FAST, SMALL, config

AUTO = FAST | SMALL | {"lr": "auto", "lr_steps": 12, "pretrain_epochs": 1, "pretrain_lr": "auto"}
AUTO = AUTO | {"pretrain_lr_steps": 10}


@pytest.fixture(scope="module")
def auto_report(tmp_path_factory):
    out = tmp_path_factory.mktemp("lr-auto")
    cfg = config({"raw_hier_classifier": AUTO}, benchmark={"enabled": False})
    return run(cfg, out)


def training(report):
    return report["contexts"]["pvp"]["candidates"]["raw_hier_classifier"]["training"]


def test_report_names_the_found_lr_the_rule_the_schedule_and_the_curve(auto_report):
    plan = training(auto_report)["lr_plan"]

    assert plan["source"] == "auto"
    assert training(auto_report)["lr"] == pytest.approx(plan["lr"])
    assert plan["rule"] in {"steepest", "valley", "fallback"}
    assert plan["lr_find"]["suggestions"].keys() == {"steepest", "valley"}
    assert plan["schedule"]["warmup_steps"] >= 1
    assert plan["schedule"]["floor_lr"] == pytest.approx(plan["lr"] * 0.1)
    assert len(plan["lr_find"]["curve"]) >= 3


def test_report_also_describes_the_pretraining_lr(auto_report):
    assert training(auto_report)["pretrain_lr_plan"]["source"] == "auto"


def test_markdown_report_has_a_learning_rate_section(auto_report):
    md = to_markdown(auto_report)

    assert "### Learning rate" in md
    assert "Range-test curve" in md
    assert "pretraining" in md


def test_numeric_lr_is_reported_as_fixed_and_unchanged(tmp_path):
    cfg = config({"raw_hier_classifier": FAST | SMALL}, benchmark={"enabled": False})
    res = run(cfg, tmp_path)["contexts"]["pvp"]["candidates"]["raw_hier_classifier"]["training"]

    assert res["lr"] == 0.003
    assert res["lr_plan"]["source"] == "fixed"
    assert res["lr_range_suggested"] is None


def test_optuna_tunes_a_multiplier_instead_of_the_lr_when_auto(tmp_path):
    cfg = config(
        {"raw_hier_classifier": FAST | SMALL | {"lr": "auto", "lr_steps": 10}},
        tune={"trials": 2, "storage": "memory", "tune_windows": False},
        benchmark={"enabled": False},
    )
    study = run(cfg, tmp_path, tune=True)["contexts"]["pvp"]["tune"]["raw_hier_classifier"]

    assert "lr" not in study["best_params"]
    assert 0.3 <= study["best_params"]["lr_mult"] <= 3.0
    assert study["search_space"]["lr_mult"][:1] == ["log"]


def test_pretrain_checkpoints_report_the_lr_decision(tmp_path):
    cfg = config(
        {"raw_hier_classifier": SMALL | {"pretrain_epochs": 1, "pretrain_lr": "auto",
                                         "pretrain_lr_steps": 10, "batch_size": 16}},
        benchmark={"enabled": False},
    )  # fmt: skip
    out = pretrain_checkpoints(cfg, tmp_path)

    assert out["pvp"]["lr"]["source"] == "auto"


def test_cli_lr_find_prints_the_suggestion_and_writes_curves(tmp_path, capsys):
    toml = tmp_path / "eval.toml"
    toml.write_text(
        """
name = "cli"
[run]
seed = 3
fpr_targets = [0.05]
[raw]
window_seconds = 8.0
stride_seconds = 8.0
max_packets = 24
min_packets = 6
[raw.packet]
max_tokens_per_packet = 16
[raw.tokenizer]
vocab_size = 600
[raw.tokenizer.layout]
packet_type_max = 64
[raw.synthetic]
n_legit = 8
n_cheat = 6
sessions_per_player = 1
seconds_per_session = 25.0
[candidates.raw_hier_classifier]
epochs = 4
batch_size = 8
lr = 0.003
d_model = 32
heads = 2
layers = 1
local_dim = 16
local_heads = 2
"""
    )
    out = tmp_path / "out"

    code = eval_main(["lr-find", "--config", str(toml), "--out", str(out), "--steps", "10"])

    assert code == 0
    printed = capsys.readouterr().out
    assert "raw_hier_classifier" in printed
    assert "schedule cosine" in printed
    found = json.loads((out / "lr/pvp__raw_hier_classifier.json").read_text())
    assert found["schedule"]["total_steps"] > 0
    assert found["lr"] == pytest.approx(found["lr_find"]["suggested_lr"])
    rows = (out / "lr/pvp__raw_hier_classifier.csv").read_text().splitlines()
    assert rows[0] == "lr,smoothed_loss"
    assert len(rows) > 3


def test_the_range_test_runs_once_per_study_not_once_per_trial(tmp_path, monkeypatch):
    from corvus_ml.eval import lr as lr_module

    calls = []
    real = lr_module.find_lr

    def counting(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(lr_module, "find_lr", counting)
    cfg = config(
        {"raw_hier_classifier": FAST | SMALL | {"lr": "auto", "lr_steps": 10}},
        tune={"trials": 3, "storage": "memory", "tune_windows": False},
        benchmark={"enabled": False},
    )

    info = run(cfg, tmp_path, tune=True)["contexts"]["pvp"]

    study = info["tune"]["raw_hier_classifier"]
    assert study["n_trials"] == 3
    # one for the study, one for the final fit with the best parameters
    assert len(calls) == 2
    assert study["lr_find"]["source"] == "auto"
    assert "Range test run once for all trials" in to_markdown({**_stub_report(info)})


def _stub_report(info):
    return {
        "name": "x", "synthetic": False, "seed": 0, "threads": 1, "unlabelled": "exclude",
        "assumed_legit_windows": 0, "fpr_targets": [0.05], "contexts": {"pvp": info},
    }  # fmt: skip


def test_the_report_warns_when_the_range_test_was_inconclusive(auto_report):
    plan = training(auto_report)["lr_plan"]
    flat = {
        **plan,
        "lr_find": {**plan["lr_find"], "flat": True, "warning": "range test inconclusive: x"},
    }
    info = auto_report["contexts"]["pvp"]
    cand = info["candidates"]["raw_hier_classifier"]
    patched = {
        **auto_report,
        "contexts": {
            "pvp": {
                **info,
                "candidates": {
                    "raw_hier_classifier": {
                        **cand,
                        "training": {**cand["training"], "lr_plan": flat},
                    }
                },
            }
        },
    }

    assert "Warning: range test inconclusive" in to_markdown(patched)
