"""Candidates on token sequences (PyTorch, CPU): GRU autoencoder and 1D-CNN classifier.

Both take a ``(batch, L, F + 1)`` tensor whose last channel is the padding mask. Per-feature
standardisation (statistics of the real training frames) lives inside the module as buffers, so
the exported ONNX graph is self-contained: raw transformed tokens in, one score per window out.
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
from corvus_ml.eval.training import fit, lr_range_test, seed_everything

_OPSET = 17


def frame_stats(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Mean / std of the feature channels over real (unpadded) frames."""
    real = x[..., -1] > 0.5
    frames = x[..., :-1][real]
    if frames.shape[0] == 0:
        n = x.shape[-1] - 1
        return np.zeros(n, np.float32), np.ones(n, np.float32)
    return frames.mean(0).astype(np.float32), np.maximum(frames.std(0), 1e-3).astype(np.float32)


class _Normalised(nn.Module):
    def __init__(self, mean: np.ndarray, std: np.ndarray):
        super().__init__()
        self.register_buffer("mean", torch.as_tensor(mean))
        self.register_buffer("std", torch.as_tensor(std))

    def normalise(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        mask = x[..., -1:]
        return (x[..., :-1] - self.mean) / self.std * mask, mask


class GruAutoencoder(_Normalised):
    """GRU encoder -> latent -> GRU decoder; forward returns the masked reconstruction MSE."""

    def __init__(self, mean, std, hidden: int = 64, latent: int = 32):
        super().__init__(mean, std)
        f = len(mean)
        self.enc = nn.GRU(f, hidden, batch_first=True)
        self.to_latent = nn.Linear(hidden, latent)
        self.from_latent = nn.Linear(latent, hidden)
        self.dec = nn.GRU(hidden, hidden, batch_first=True)
        self.out = nn.Linear(hidden, f)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        xn, mask = self.normalise(x)
        _, h = self.enc(xn)
        z = self.to_latent(h[-1])
        seed = self.from_latent(z).unsqueeze(1).expand(-1, xn.shape[1], -1)
        y, _ = self.dec(seed)
        err = ((self.out(y) - xn) ** 2).mean(-1)
        m = mask[..., 0]
        return (err * m).sum(1) / m.sum(1).clamp(min=1.0)


class Cnn1d(_Normalised):
    """Two dilated conv layers + masked mean/max pooling + linear head."""

    def __init__(self, mean, std, channels: int = 32):
        super().__init__(mean, std)
        f = len(mean)
        self.c1 = nn.Conv1d(f, channels, 5, padding=2)
        self.c2 = nn.Conv1d(channels, channels, 5, padding=4, dilation=2)
        self.head = nn.Linear(2 * channels, 1)

    def logits(self, x: torch.Tensor) -> torch.Tensor:
        xn, mask = self.normalise(x)
        h = torch.relu(self.c1(xn.transpose(1, 2)))
        h = torch.relu(self.c2(h)).transpose(1, 2) * mask
        mean = h.sum(1) / mask.sum(1).clamp(min=1.0)
        return self.head(torch.cat([mean, h.max(1).values], dim=1)).squeeze(-1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return torch.sigmoid(self.logits(x))


class TorchCandidate(Candidate):
    quantizable = True
    input_kind = "seq"

    @abstractmethod
    def build(self, mean: np.ndarray, std: np.ndarray) -> nn.Module: ...

    @abstractmethod
    def loss_fn(self, model: nn.Module, batch: tuple[torch.Tensor, ...]) -> torch.Tensor: ...

    def tensors(self, data: EvalData) -> tuple[torch.Tensor, ...]:
        return (torch.from_numpy(self.inputs(data)),)

    def fit(self, train: EvalData, val: EvalData | None = None) -> None:
        seed_everything(self.seed, self.threads)
        tr = self.tensors(train)
        va = self.tensors(val) if val is not None and len(val) else None
        self.model = self.build(*frame_stats(tr[0].numpy()))
        lr = self.params.get("lr", 2e-3)
        batch = int(self.params.get("batch_size", 64))
        self.lr_range = None
        if lr == "auto":
            self.lr_range = lr_range_test(
                self.model, self.loss_fn, tr, batch_size=batch, seed=self.seed
            )
            lr = self.lr_range.suggested_lr
        self.lr_used = float(lr)
        self.history = fit(
            self.model,
            self.loss_fn,
            tr,
            va,
            epochs=int(self.params.get("epochs", 30)),
            batch_size=batch,
            lr=self.lr_used,
            patience=int(self.params.get("patience", 5)),
            seed=self.seed,
        )

    def score(self, data: EvalData) -> np.ndarray:
        x = torch.from_numpy(self.inputs(data))
        self.model.eval()
        out = []
        with torch.no_grad():
            for i in range(0, x.shape[0], 512):
                out.append(self.model(x[i : i + 512]).numpy())
        return np.concatenate(out).astype(np.float64) if out else np.zeros(0)

    def export_onnx(self, path: Path, example: np.ndarray) -> Path:
        self.model.eval()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            torch.onnx.export(
                self.model,
                (torch.from_numpy(np.ascontiguousarray(example[:2])),),
                str(path),
                input_names=["x"],
                output_names=["score"],
                dynamic_axes={"x": {0: "batch"}, "score": {0: "batch"}},
                opset_version=_OPSET,
                dynamo=False,
            )
        return path

    @property
    def n_params(self) -> int:
        return int(sum(p.numel() for p in self.model.parameters()))


@register
class GruAutoencoderCandidate(TorchCandidate):
    name = "gru_autoencoder"
    supervised = False

    def build(self, mean, std) -> nn.Module:
        return GruAutoencoder(
            mean, std, int(self.params.get("hidden", 64)), int(self.params.get("latent", 32))
        )

    def loss_fn(self, model, batch):
        return model(batch[0]).mean()


@register
class CnnCandidate(TorchCandidate):
    name = "cnn1d"
    supervised = True
    bounded = True

    def tensors(self, data: EvalData):
        y = torch.from_numpy((data.label == LABEL_CHEAT).astype(np.float32))
        return (torch.from_numpy(self.inputs(data)), y)

    def build(self, mean, std) -> nn.Module:
        return Cnn1d(mean, std, int(self.params.get("channels", 32)))

    def fit(self, train: EvalData, val: EvalData | None = None) -> None:
        pos = max(1.0, float((train.label == LABEL_CHEAT).sum()))
        neg = max(1.0, float((train.label != LABEL_CHEAT).sum()))
        self._pos_weight = torch.tensor(neg / pos)
        super().fit(train, val)

    def loss_fn(self, model, batch):
        return nn.functional.binary_cross_entropy_with_logits(
            model.logits(batch[0]), batch[1], pos_weight=self._pos_weight
        )
