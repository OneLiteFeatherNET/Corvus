"""Candidate implementations. Importing this package registers all of them."""

from corvus_ml.eval.candidates import sklearn_models, torch_models
from corvus_ml.eval.candidates.base import REGISTRY, Candidate, create, register

__all__ = ["REGISTRY", "Candidate", "create", "register", "sklearn_models", "torch_models"]
