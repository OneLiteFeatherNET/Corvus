"""Score calibration: raw model output -> probability-like score in [0, 1].

Every exported model outputs a calibrated score:

* supervised candidates: isotonic regression (enough validation data) or Platt scaling on the
  validation split, so the score approximates P(cheat | window);
* unsupervised candidates: the empirical CDF of the raw scores of legit (or assumed-legit)
  validation windows, so 0.99 means "scores higher than 99 % of legit windows".

A :class:`Calibration` is either piecewise linear (isotonic, empirical CDF, identity) or a sigmoid
(Platt). Both are monotone non-decreasing, so the ranking of the raw score is preserved (isotonic
and the CDF can merge neighbouring values into ties). The same object is applied in Python
(:meth:`Calibration.apply`) and embedded into the ONNX graph
(:mod:`corvus_ml.calibration_onnx`); a test pins the two to each other.

Importing this module needs only numpy; the fit helpers import scikit-learn lazily.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

METHODS = ("auto", "isotonic", "platt", "ecdf", "none")
# Knots kept for a piecewise-linear calibration: bounds the size of the embedded ONNX constants.
MAX_KNOTS = 256
# Isotonic regression needs enough points of both classes; below this Platt scaling is used.
MIN_ISOTONIC_PER_CLASS = 20
MIN_ISOTONIC_TOTAL = 200
# Quantile levels of the empirical CDF knots: uniform plus a dense upper tail.
_ECDF_LEVELS = np.unique(
    np.concatenate(
        [
            np.linspace(0.0, 1.0, 193),
            [0.9, 0.95, 0.97, 0.98, 0.99, 0.995, 0.997, 0.999, 0.9995, 0.9999],
        ]
    )
)


@dataclass(frozen=True)
class Calibration:
    """Monotone map from a raw score to [0, 1].

    ``kind`` is ``"piecewise_linear"`` (``xs`` strictly increasing, ``ys`` non-decreasing in
    [0, 1]; constant outside ``[xs[0], xs[-1]]``) or ``"sigmoid"`` (``sigmoid(a * s + b)`` with
    ``a >= 0``). ``method`` names how it was fitted, ``fit_on`` which data it was fitted on.
    """

    method: str
    kind: str
    xs: tuple[float, ...] = ()
    ys: tuple[float, ...] = ()
    a: float = 0.0
    b: float = 0.0
    n_fit: int = 0
    fit_on: str = "validation"

    def __post_init__(self) -> None:
        if self.kind == "piecewise_linear":
            if len(self.xs) < 1 or len(self.xs) != len(self.ys):
                raise ValueError("piecewise_linear needs matching, non-empty xs and ys")
            if np.any(np.diff(self.xs) <= 0):
                raise ValueError("xs must be strictly increasing")
            if np.any(np.diff(self.ys) < 0) or min(self.ys) < 0.0 or max(self.ys) > 1.0:
                raise ValueError("ys must be non-decreasing and within [0, 1]")
        elif self.kind == "sigmoid":
            if self.a < 0:
                raise ValueError("a sigmoid calibration must not be decreasing (a >= 0)")
        else:
            raise ValueError(f"unknown calibration kind {self.kind!r}")

    def apply(self, raw: np.ndarray) -> np.ndarray:
        """Float64 calibrated scores, same shape as ``raw``."""
        s = np.asarray(raw, dtype=np.float64)
        if self.kind == "sigmoid":
            z = np.clip(self.a * s + self.b, -60.0, 60.0)
            return 1.0 / (1.0 + np.exp(-z))
        return np.interp(s, self.xs, self.ys)

    # --- serialisation -----------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "method": self.method,
            "kind": self.kind,
            "n_fit": self.n_fit,
            "fit_on": self.fit_on,
        }
        if self.kind == "sigmoid":
            out.update(a=self.a, b=self.b)
        else:
            out.update(xs=list(self.xs), ys=list(self.ys))
        return out

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Calibration:
        return cls(
            method=str(raw["method"]),
            kind=str(raw["kind"]),
            xs=tuple(float(v) for v in raw.get("xs", ())),
            ys=tuple(float(v) for v in raw.get("ys", ())),
            a=float(raw.get("a", 0.0)),
            b=float(raw.get("b", 0.0)),
            n_fit=int(raw.get("n_fit", 0)),
            fit_on=str(raw.get("fit_on", "validation")),
        )


def identity(n_fit: int = 0, fit_on: str = "none") -> Calibration:
    """Clip a score that already lives in [0, 1] (probability outputs without usable data)."""
    return Calibration(
        "identity", "piecewise_linear", (0.0, 1.0), (0.0, 1.0), n_fit=n_fit, fit_on=fit_on
    )


def _thin(xs: np.ndarray, ys: np.ndarray, limit: int) -> tuple[np.ndarray, np.ndarray]:
    """At most ``limit`` knots, evenly spread over the knot index; ends are kept."""
    if len(xs) <= limit:
        return xs, ys
    keep = np.unique(np.linspace(0, len(xs) - 1, limit).round().astype(int))
    return xs[keep], ys[keep]


def _strictly_increasing(xs: np.ndarray, ys: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Collapse equal ``xs`` (keeping the highest ``ys``) and drop non-finite knots."""
    ok = np.isfinite(xs) & np.isfinite(ys)
    xs, ys = xs[ok], ys[ok]
    order = np.argsort(xs, kind="stable")
    xs, ys = xs[order], ys[order]
    last = np.concatenate((xs[1:] != xs[:-1], [True]))
    return xs[last], np.maximum.accumulate(ys)[last]


def _piecewise(method: str, xs: np.ndarray, ys: np.ndarray, n_fit: int, fit_on: str) -> Calibration:
    xs, ys = _strictly_increasing(xs, np.clip(ys, 0.0, 1.0))
    xs, ys = _thin(xs, ys, MAX_KNOTS)
    return Calibration(
        method,
        "piecewise_linear",
        tuple(xs.tolist()),
        tuple(ys.tolist()),
        n_fit=n_fit,
        fit_on=fit_on,
    )


def fit_isotonic(raw: np.ndarray, y: np.ndarray, fit_on: str = "validation") -> Calibration:
    """Isotonic regression of the cheat label on the raw score (linear between its thresholds)."""
    from sklearn.isotonic import IsotonicRegression

    s = np.asarray(raw, dtype=np.float64)
    iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip").fit(s, y)
    return _piecewise(
        "isotonic",
        np.asarray(iso.X_thresholds_, dtype=np.float64),
        np.asarray(iso.y_thresholds_, dtype=np.float64),
        len(s),
        fit_on,
    )


def fit_platt(raw: np.ndarray, y: np.ndarray, fit_on: str = "validation") -> Calibration | None:
    """Platt scaling ``sigmoid(a * s + b)``; None if the fit is decreasing (not a calibration)."""
    from sklearn.linear_model import LogisticRegression

    s = np.asarray(raw, dtype=np.float64)
    mean, std = float(s.mean()), float(s.std())
    if not np.isfinite(std) or std < 1e-12:
        std = 1.0
    z = ((s - mean) / std).reshape(-1, 1)
    lr = LogisticRegression(C=1e4, max_iter=1000).fit(z, y)
    slope = float(lr.coef_[0, 0]) / std
    if slope < 0:
        return None
    intercept = float(lr.intercept_[0]) - float(lr.coef_[0, 0]) * mean / std
    return Calibration("platt", "sigmoid", a=slope, b=intercept, n_fit=len(s), fit_on=fit_on)


def fit_ecdf(legit_raw: np.ndarray, fit_on: str = "validation") -> Calibration:
    """Empirical CDF of the raw scores of legit windows: ``P(legit score <= s)``."""
    s = np.asarray(legit_raw, dtype=np.float64)
    s = s[np.isfinite(s)]
    if s.size == 0:
        raise ValueError("an empirical CDF needs at least one legit score")
    return _piecewise("ecdf", np.quantile(s, _ECDF_LEVELS), _ECDF_LEVELS.copy(), s.size, fit_on)


def fit_calibration(
    method: str,
    supervised: bool,
    val_raw: np.ndarray,
    val_cheat: np.ndarray | None,
    fallback_legit_raw: np.ndarray | None = None,
    bounded: bool = False,
) -> Calibration:
    """Fit the calibration for one candidate.

    ``val_raw`` are the raw scores of the validation windows the candidate may be calibrated on
    (labelled windows for supervised, legit windows for unsupervised candidates) and
    ``val_cheat`` their 0/1 cheat label (supervised only). ``fallback_legit_raw`` (unsupervised)
    is used when the validation split has no legit window. ``bounded`` says that the raw score
    is already a probability in [0, 1]: it is then passed through (clipped) when there is no
    usable calibration data. ``method`` is one of :data:`METHODS`; ``auto`` picks as described in
    the module docstring.
    """
    if method not in METHODS:
        raise ValueError(f"calibration must be one of {METHODS}, got {method!r}")
    if method == "none":
        raise ValueError("calibration 'none' means: do not call fit_calibration")
    if not supervised:
        if method not in ("auto", "ecdf"):
            raise ValueError(f"unsupervised candidates are calibrated with 'ecdf', not {method!r}")
        if len(val_raw):
            return fit_ecdf(val_raw)
        if fallback_legit_raw is not None and len(fallback_legit_raw):
            log.warning("no legit validation windows; calibrating on the training windows")
            return fit_ecdf(fallback_legit_raw, fit_on="train")
        raise ValueError("no legit windows to calibrate an unsupervised candidate on")
    if method == "ecdf":
        raise ValueError("supervised candidates are calibrated with 'isotonic' or 'platt'")
    y = np.asarray(val_cheat if val_cheat is not None else [], dtype=np.int64)
    pos, neg = int((y == 1).sum()), int((y == 0).sum())
    if pos == 0 or neg == 0:
        if bounded:
            log.warning("validation split has a single class; passing the probability through")
            return identity(len(y))
        raise ValueError("calibration needs validation windows of both classes")
    enough = pos >= MIN_ISOTONIC_PER_CLASS and neg >= MIN_ISOTONIC_PER_CLASS
    enough = enough and len(y) >= MIN_ISOTONIC_TOTAL
    if method == "isotonic" or (method == "auto" and enough):
        return fit_isotonic(val_raw, y)
    platt = fit_platt(val_raw, y)
    if platt is not None:
        return platt
    log.warning("Platt scaling was decreasing; falling back to isotonic regression")
    return fit_isotonic(val_raw, y)
