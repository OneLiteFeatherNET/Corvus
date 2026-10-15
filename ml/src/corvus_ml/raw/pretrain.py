"""Masked pretraining of the packet hierarchy on unlabelled windows, and its checkpoint.

Objectives (see :class:`~corvus_ml.raw.model.RawAnomalyNet`): masked byte-token prediction inside
packets and masked packet prediction (packet type and the first payload tokens). The checkpoint
holds the architecture and all weights; a classifier is finetuned from the encoder part (``micro``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import fields
from pathlib import Path

import numpy as np
import torch

from corvus_ml.eval.training import FitHistory, fit
from corvus_ml.raw.model import ModelConfig, RawAnomalyNet

# Settings that define the encoder weights' shapes and meaning; the rest may differ on finetuning.
ARCHITECTURE_KEYS = (
    "vocab_size", "max_tokens", "n_special", "local_dim", "local_layers", "local_type",
    "local_heads", "d_model", "layers", "heads", "ffn_mult", "time_freqs", "time_min_s",
    "time_max_s",
)  # fmt: skip


def flatten_windows(ids: np.ndarray, time_s: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Collapse the macro axis and drop windows without packets: ``(W, P, T)``, ``(W, P)``."""
    flat_ids = ids.reshape(-1, *ids.shape[-2:])
    flat_t = time_s.reshape(-1, time_s.shape[-1])
    keep = (flat_ids[:, :, 0] != 0).any(axis=1)
    return flat_ids[keep], flat_t[keep]


def save_checkpoint(path: Path, model: RawAnomalyNet, extra: dict | None = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {"config": model.cfg.to_dict(), "state": model.state_dict(), "extra": extra or {}}, path
    )
    return path


def _architecture_differences(saved: dict, wanted: ModelConfig) -> list[str]:
    return [
        f"{k}: checkpoint has {saved.get(k)!r}, model wants {getattr(wanted, k)!r}"
        for k in ARCHITECTURE_KEYS
        if saved.get(k) != getattr(wanted, k)
    ]


def load_encoder(model: torch.nn.Module, path: Path) -> dict:
    """Copy the pretrained ``micro`` weights into ``model``; the architectures must agree."""
    blob = torch.load(Path(path), map_location="cpu", weights_only=True)
    known = {f.name for f in fields(ModelConfig)}
    diffs = _architecture_differences(
        {k: v for k, v in blob["config"].items() if k in known}, model.cfg
    )
    if diffs:
        raise ValueError(
            "pretrained checkpoint does not fit the model:\n  - " + "\n  - ".join(diffs)
        )
    micro = {
        k.removeprefix("micro."): v for k, v in blob["state"].items() if k.startswith("micro.")
    }
    model.micro.load_state_dict(micro)
    return blob.get("extra", {})


def pretrain(
    model: RawAnomalyNet,
    ids: np.ndarray,
    time_s: np.ndarray,
    *,
    epochs: int,
    batch_size: int,
    lr: float,
    weight_decay: float = 0.01,
    patience: int = 5,
    seed: int = 0,
    val_fraction: float = 0.1,
    on_epoch: Callable[[int, float], None] | None = None,
) -> FitHistory:
    """AdamW + warmup + cosine + early stopping on the masked-prediction loss.

    ``ids`` / ``time_s`` are flat windows (:func:`flatten_windows`). A fixed share of them is held
    out for early stopping; the split depends only on ``seed``.
    """
    n = ids.shape[0]
    order = np.random.default_rng(seed).permutation(n)
    n_val = int(n * val_fraction) if n >= 10 else 0
    val_idx, train_idx = order[:n_val], order[n_val:]
    ids_t = torch.from_numpy(ids.astype(np.int16))
    time_t = torch.from_numpy(time_s.astype(np.float32))

    def loss_fn(m: RawAnomalyNet, batch: tuple[torch.Tensor, ...]) -> torch.Tensor:
        return m.pretrain_loss(batch[0].long(), batch[1])

    train = (ids_t[train_idx], time_t[train_idx])
    val = (ids_t[val_idx], time_t[val_idx]) if n_val else None
    return fit(
        model, loss_fn, train, val, epochs=epochs, batch_size=batch_size, lr=lr,
        weight_decay=weight_decay, patience=patience, seed=seed, on_epoch=on_epoch,
    )  # fmt: skip
