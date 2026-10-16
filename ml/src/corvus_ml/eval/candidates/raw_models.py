"""Candidates on tokenized raw packet bytes (ADR-CORV-0008): ``raw_hier_classifier`` (supervised)
and ``raw_hier_anomaly`` (self-supervised, masked-reconstruction score).

Both take the float32 tensor ``(batch, [macro,] packets, tokens + 1)`` built by
:func:`corvus_ml.raw.windows.to_input`; the model, the pretraining objectives and the checkpoint
format are in :mod:`corvus_ml.raw`.
"""

from __future__ import annotations

import warnings
from abc import abstractmethod
from pathlib import Path

import numpy as np
import torch
from torch import nn

from corvus_ml.eval.candidates.base import Candidate, register
from corvus_ml.eval.data import LABEL_CHEAT, EvalData
from corvus_ml.eval.lr import LR_PARAM_KEYS, PRETRAIN_LR_PARAM_KEYS, lr_kwargs
from corvus_ml.eval.training import fit, seed_everything
from corvus_ml.membudget import MicroBatchPlan, plan_micro_batch
from corvus_ml.raw.entropy import byte_entropy_weights
from corvus_ml.raw.model import ModelConfig, RawAnomalyNet, RawHierNet
from corvus_ml.raw.pretrain import flatten_windows, load_encoder, pretrain, save_checkpoint
from corvus_ml.raw.spec import RawBytesSpec
from corvus_ml.raw.windows import to_input

_OPSET = 17
_MODEL_KEYS = (
    "d_model", "layers", "heads", "ffn_mult", "dropout", "local_dim", "local_layers", "local_type",
    "local_heads", "macro_layers", "macro_pool", "p_token", "p_packet", "first_tokens",
    "max_masked", "score_period", "score_passes", "time_freqs", "time_min_s", "time_max_s",
    # variants of the tokenizer ablation (docs/reference/raw-byte-tokenizer.md, section 12)
    "type_pos", "type_buckets", "span_mean", "packet_mask", "entropy_weight", "entropy_score",
    "objective", "jepa_loss", "ema_momentum", "combine", "knn_bank", "knn_k", "knn_weight",
)  # fmt: skip
_TRAIN_KEYS = (
    "epochs", "batch_size", "weight_decay", "patience", "pretrain_epochs",
    "init_from", "checkpoint_out", "score_batch", "memory_budget_mb",
    *LR_PARAM_KEYS, *PRETRAIN_LR_PARAM_KEYS,
)  # fmt: skip


class RawCandidate(Candidate):
    input_kind = "raw_bytes"
    quantizable = False
    pretrains = True
    tunable = True
    parity_rows = 64  # whole test sets are large; the parity check needs only a sample

    def __init__(self, seed: int = 0, threads: int = 1, **params):
        unknown = sorted(set(params) - set(_MODEL_KEYS) - set(_TRAIN_KEYS))
        if unknown:
            raise ValueError(f"unknown parameters for {self.name}: {unknown}")
        super().__init__(seed, threads, **params)

    # --- data ----------------------------------------------------------------------------------

    @staticmethod
    def spec_of(data: EvalData) -> RawBytesSpec:
        spec = data.meta.get("raw_spec")
        if spec is None or data.raw_ids is None:
            raise ValueError("raw-byte candidates need raw windows (configure [raw])")
        return spec

    def inputs(self, data: EvalData) -> np.ndarray:
        spec = self.spec_of(data)
        assert data.raw_ids is not None
        assert data.raw_time is not None
        return to_input(data.raw_ids, data.raw_time, spec.window.macro_windows)

    def _tensors(self, data: EvalData) -> tuple[torch.Tensor, torch.Tensor]:
        spec = self.spec_of(data)
        assert data.raw_ids is not None
        assert data.raw_time is not None
        ids, t = data.raw_ids, data.raw_time
        if spec.window.macro_windows == 1:
            ids, t = ids[:, 0], t[:, 0]
        return torch.from_numpy(np.ascontiguousarray(ids)), torch.from_numpy(
            np.ascontiguousarray(t)
        )

    def _plan(
        self, cfg: ModelConfig, packets: int, batch: int, *, training: bool, what: str
    ) -> MicroBatchPlan:
        """Rows per forward pass that fit the memory budget (parameter ``memory_budget_mb``,
        else ``CORVUS_ML_MEMORY_BUDGET_MB``, else a share of the container's memory limit)."""
        budget = self.params.get("memory_budget_mb")
        return plan_micro_batch(
            cfg, packets, batch, training=training, what=what,
            budget_mb=None if budget is None else float(budget),
        )  # fmt: skip

    def inference_rows(self, x: np.ndarray) -> int | None:
        if not hasattr(self, "model"):
            return None
        plan = self._plan(
            self.model.cfg, int(x.shape[-2]), len(x), training=False, what="ONNX parity"
        )
        return plan.micro

    def model_config(self, data: EvalData) -> ModelConfig:
        spec = self.spec_of(data)
        kw = {k: self.params[k] for k in _MODEL_KEYS if k in self.params}
        return ModelConfig.for_layout(
            spec.layout,
            spec.vocab_size,
            spec.packet.max_tokens_per_packet,
            macro_windows=spec.window.macro_windows,
            **kw,
        )

    # --- training ------------------------------------------------------------------------------

    @abstractmethod
    def build(self, cfg: ModelConfig) -> nn.Module: ...

    def _pretrain(
        self, cfg: ModelConfig, pool: EvalData, epochs: int | None = None
    ) -> RawAnomalyNet:
        """Masked pretraining on every window of ``pool`` (labels are not used).

        ``epochs=0`` only resolves the learning rate (``corvus-eval lr-find``): nothing is trained
        or saved."""
        micro_cfg = ModelConfig(**{**cfg.to_dict(), "macro_windows": 1, "macro_layers": 1})
        net = RawAnomalyNet(micro_cfg)
        ids, t = flatten_windows(pool.raw_ids, pool.raw_time)  # type: ignore[arg-type]
        if len(ids) == 0:
            return net
        self._fit_statistics(net, ids)
        probe = epochs is not None
        epochs = int(self.params.get("pretrain_epochs", 0)) if epochs is None else epochs
        batch = int(self.params.get("batch_size", 16))
        wd = float(self.params.get("weight_decay", 0.01))
        self.pretrain_history = pretrain(
            net, ids, t, epochs=epochs, batch_size=batch, weight_decay=wd,
            patience=int(self.params.get("patience", 3)), seed=self.seed,
            memory_budget_mb=self.params.get("memory_budget_mb"),
            **lr_kwargs(self.params, prefix="pretrain_", default_lr=1e-3, batch_size=batch,
                        seed=self.seed, weight_decay=wd),
        )  # fmt: skip
        if "checkpoint_out" in self.params and not probe:
            save_checkpoint(Path(self.params["checkpoint_out"]), net)
        return net

    def probe_pretrain_lr(self, data: EvalData, pool: EvalData | None = None) -> dict | None:
        """Learning-rate plan of the pretraining without training (None if not configured)."""
        if int(self.params.get("pretrain_epochs", 0)) <= 0 or "init_from" in self.params:
            return None
        self._pretrain(self.model_config(data), pool if pool is not None else data, epochs=0)
        hist = getattr(self, "pretrain_history", None)
        return hist.lr_plan if hist is not None else None

    @staticmethod
    def _fit_statistics(net: RawAnomalyNet, ids: np.ndarray) -> None:
        """Statistics of the training windows the objectives need (deterministic, no sampling)."""
        if net.cfg.entropy_weight:
            net.set_byte_weights(byte_entropy_weights(ids, net.cfg))

    def _init_encoder(self, model: nn.Module, cfg: ModelConfig, pool: EvalData | None) -> None:
        if "init_from" in self.params:
            load_encoder(model, Path(self.params["init_from"]))
        elif int(self.params.get("pretrain_epochs", 0)) > 0 and pool is not None:
            net = self._pretrain(cfg, pool)
            model.micro.load_state_dict(net.micro.state_dict())  # type: ignore[union-attr]

    def _run_fit(self, tr, va, loss_fn) -> None:
        batch = int(self.params.get("batch_size", 16))
        wd = float(self.params.get("weight_decay", 0.01))
        cfg, packets = self.model.cfg, int(tr[0].shape[-2])
        micro = self._plan(cfg, packets, batch, training=True, what="training").micro
        evaluate = self._plan(cfg, packets, 1024, training=False, what="validation").micro
        self.history = fit(
            self.model, loss_fn, tr, va, epochs=int(self.params.get("epochs", 10)),
            batch_size=batch, weight_decay=wd,
            patience=int(self.params.get("patience", 3)), seed=self.seed, on_epoch=self.on_epoch,
            **lr_kwargs(self.params, default_lr=2e-3, batch_size=batch, seed=self.seed,
                        weight_decay=wd, micro_batch=micro, eval_batch=evaluate),
        )  # fmt: skip
        self.lr_used = float(self.history.lr_used)  # type: ignore[arg-type]
        self.lr_range = self.history.lr_find

    # --- scoring and export ----------------------------------------------------------------------

    def score(self, data: EvalData) -> np.ndarray:
        ids, t = self._tensors(data)
        self.model.eval()
        step = int(self.params.get("score_batch", 16))
        step = self._plan(self.model.cfg, int(ids.shape[-2]), step, training=False,
                          what="scoring").micro  # fmt: skip
        out = []
        with torch.no_grad():
            for i in range(0, ids.shape[0], step):
                out.append(self._score_batch(ids[i : i + step].long(), t[i : i + step]))
        return np.concatenate(out).astype(np.float64) if out else np.zeros(0)

    @abstractmethod
    def _score_batch(self, ids: torch.Tensor, t: torch.Tensor) -> np.ndarray: ...

    def export_onnx(self, path: Path, example: np.ndarray) -> Path:
        self.model.eval()
        packets_axis = example.ndim - 2
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            torch.onnx.export(
                self.model,
                (torch.from_numpy(np.ascontiguousarray(example[:2], dtype=np.float32)),),
                str(path),
                input_names=["x"],
                output_names=["score"],
                dynamic_axes={"x": {0: "batch", packets_axis: "packets"}, "score": {0: "batch"}},
                opset_version=_OPSET,
                dynamo=False,
            )
        return path

    @property
    def n_params(self) -> int:
        return int(sum(p.numel() for p in self.model.parameters()))


@register
class RawHierClassifier(RawCandidate):
    name = "raw_hier_classifier"
    supervised = True
    bounded = True

    def build(self, cfg: ModelConfig) -> nn.Module:
        return RawHierNet(cfg)

    def fit(
        self, train: EvalData, val: EvalData | None = None, pool: EvalData | None = None
    ) -> None:
        seed_everything(self.seed, self.threads)
        cfg = self.model_config(train)
        pos = max(1.0, float((train.label == LABEL_CHEAT).sum()))
        neg = max(1.0, float((train.label != LABEL_CHEAT).sum()))
        pos_weight = torch.tensor(neg / pos)
        self.model = self.build(cfg)
        self._init_encoder(self.model, cfg, pool if pool is not None else train)

        def tensors(d: EvalData):
            ids, t = self._tensors(d)
            return ids, t, torch.from_numpy((d.label == LABEL_CHEAT).astype(np.float32))

        def loss_fn(model, batch):
            logits = model.logits_ids(batch[0].long(), batch[1])
            return nn.functional.binary_cross_entropy_with_logits(
                logits, batch[2], pos_weight=pos_weight
            )

        self._run_fit(
            tensors(train), tensors(val) if val is not None and len(val) else None, loss_fn
        )

    def _score_batch(self, ids, t):
        return self.model.logits_ids(ids, t).sigmoid().numpy()


@register
class RawHierAnomaly(RawCandidate):
    name = "raw_hier_anomaly"
    supervised = False

    def build(self, cfg: ModelConfig) -> nn.Module:
        return RawAnomalyNet(cfg)

    def model_config(self, data: EvalData) -> ModelConfig:
        cfg = super().model_config(data)
        if cfg.macro_windows != 1:
            raise ValueError("raw_hier_anomaly works on single windows: unset macro_window_seconds")
        return cfg

    def fit(
        self, train: EvalData, val: EvalData | None = None, pool: EvalData | None = None
    ) -> None:
        seed_everything(self.seed, self.threads)
        cfg = self.model_config(train)
        self.model = self.build(cfg)
        if "init_from" in self.params:
            load_encoder(self.model, Path(self.params["init_from"]))
            self.model.sync_target()
        ids, t = flatten_windows(train.raw_ids, train.raw_time)  # type: ignore[arg-type]
        self._fit_statistics(self.model, ids)

        def tensors(d: EvalData):
            return self._tensors(d)

        def loss_fn(model, batch):
            return model.pretrain_loss(batch[0].long(), batch[1])

        self._run_fit(
            tensors(train), tensors(val) if val is not None and len(val) else None, loss_fn
        )
        if cfg.combine != "none" and len(ids):
            chunk = self._plan(
                cfg, int(ids.shape[-2]), 32, training=False, what="memory bank"
            ).micro
            self.model.fit_memory(ids, t, seed=self.seed, chunk=chunk)
        if "checkpoint_out" in self.params:
            save_checkpoint(Path(self.params["checkpoint_out"]), self.model)

    def _score_batch(self, ids, t):
        return self.model.score_ids(ids, t).numpy()
