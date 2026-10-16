"""Empirical byte entropy per (packet type bucket, byte offset): weights of the masked-byte loss.

Positions that are random also for legit players (keepalive ids, counters, timestamps) cannot be
predicted by any model; with a plain mean they dominate the masked-byte loss and the anomaly score.
The weight of a cell is the inverse of its entropy, so predictable positions (flags, constants,
small enums) count more and noise positions less. Pure numpy, deterministic, computed once on the
training windows and stored with the model; the exported graph only carries the finished table.
"""

from __future__ import annotations

import numpy as np
import torch

from corvus_ml.raw.model import ModelConfig, type_bucket

MIN_COUNT = 8  # cells with fewer byte tokens than this keep the neutral weight 1
ENTROPY_FLOOR = 0.25  # nats added to the entropy: bounds the weight of constant positions


def byte_entropy_weights(
    ids: np.ndarray, cfg: ModelConfig, *, min_count: int = MIN_COUNT, floor: float = ENTROPY_FLOOR
) -> np.ndarray:
    """``(type_buckets, max_tokens)`` weights ``1 / (H + floor)`` with mean 1 over the seen cells.

    ``ids`` are windows ``(W, P, T)`` of token ids (padding packets are ignored). The entropy is
    the plug-in estimate in nats of the token id distribution of a cell; cells that were not seen
    (or seen fewer than ``min_count`` times) get weight 1.
    """
    flat = np.asarray(ids).reshape(-1, ids.shape[-1])
    flat = flat[flat[:, 0] != 0].astype(np.int64)
    out = np.ones((cfg.type_buckets, cfg.max_tokens), dtype=np.float32)
    if len(flat) == 0:
        return out
    length = min(flat.shape[1], cfg.max_tokens)
    bucket = type_bucket(torch.from_numpy(flat[:, None, :length]), cfg).numpy()[:, 0]
    cell = bucket[:, None] * cfg.max_tokens + np.arange(length)[None, :]
    is_byte = flat[:, :length] >= cfg.n_special
    cells, tokens = cell[is_byte], flat[:, :length][is_byte]
    pair, count = np.unique(cells * cfg.vocab_size + tokens, return_counts=True)
    pair_cell = pair // cfg.vocab_size
    n_cells = cfg.type_buckets * cfg.max_tokens
    total = np.bincount(pair_cell, weights=count, minlength=n_cells)
    p = count / total[pair_cell]
    entropy = np.bincount(pair_cell, weights=-p * np.log(p), minlength=n_cells)
    seen = total >= min_count
    if not seen.any():
        return out
    inverse = 1.0 / (entropy + floor)
    inverse = inverse / inverse[seen].mean()
    return np.where(seen, inverse, 1.0).astype(np.float32).reshape(cfg.type_buckets, cfg.max_tokens)
