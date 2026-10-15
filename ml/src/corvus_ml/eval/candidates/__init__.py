"""Candidate implementations. Importing this package registers all of them."""

from corvus_ml.eval.candidates import raw_models, sklearn_models, torch_models
from corvus_ml.eval.candidates.base import REGISTRY, Candidate, create, register

__all__ = [
    "REGISTRY", "Candidate", "create", "raw_models", "register", "sklearn_models",
    "torch_models",
]  # fmt: skip
