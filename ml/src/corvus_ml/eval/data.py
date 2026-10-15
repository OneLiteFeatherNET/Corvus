"""In-memory evaluation dataset: window aggregates, token sequences and joined labels."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from corvus_ml.features.spec import DEFAULT_AGG_INPUTS, DEFAULT_SEQ_INPUTS

LABEL_UNLABELLED = -1
LABEL_LEGIT = 0
LABEL_CHEAT = 1

# The model input layout lives in the feature spec (shared with the scoring service). These names
# stay importable for the harness and its tests.
AGG_NAMES = DEFAULT_AGG_INPUTS
SEQ_FEATURES = DEFAULT_SEQ_INPUTS
N_SEQ_FEATURES = len(SEQ_FEATURES) + 1  # + trailing mask channel (1 = real frame, 0 = padding)


@dataclass
class EvalData:
    """One row per window. ``label`` is -1 (unlabelled), 0 (legit) or 1 (cheat)."""

    session_id: np.ndarray  # object array of bytes
    segment: np.ndarray
    window_index: np.ndarray
    game_context: np.ndarray  # object array of str
    pseudonym: np.ndarray  # object array of bytes
    label: np.ndarray  # int8
    confidence: np.ndarray  # float32
    agg: np.ndarray  # float32 (n, len(AGG_NAMES))
    seq: np.ndarray | None = None  # float32 (n, L, N_SEQ_FEATURES)
    meta: dict = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.label)

    def take(self, idx: np.ndarray) -> EvalData:
        idx = np.asarray(idx)
        return EvalData(
            self.session_id[idx],
            self.segment[idx],
            self.window_index[idx],
            self.game_context[idx],
            self.pseudonym[idx],
            self.label[idx],
            self.confidence[idx],
            self.agg[idx],
            None if self.seq is None else self.seq[idx],
            self.meta,
        )

    def group_codes(self) -> np.ndarray:
        """Integer id per pseudonym, assigned in order of first appearance."""
        codes: dict[bytes, int] = {}
        return np.fromiter(
            (codes.setdefault(p, len(codes)) for p in self.pseudonym),
            dtype=np.int64,
            count=len(self),
        )

    def labelled_mask(self) -> np.ndarray:
        return self.label != LABEL_UNLABELLED
