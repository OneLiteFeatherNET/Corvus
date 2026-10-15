"""Model registry: stage machine, promotion quality gate and the transition use case."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from .ports import ModelRecord, ModelRepo, TransitionRecord

RECALL_METRIC = "recall_at_fpr_0_1pct"
LATENCY_METRIC = "p99_latency_ms"


class Stage(StrEnum):
    REGISTERED = "registered"
    SHADOW = "shadow"
    CANARY = "canary"
    ENFORCE = "enforce"
    ARCHIVED = "archived"


_FORWARD = {
    Stage.REGISTERED: Stage.SHADOW,
    Stage.SHADOW: Stage.CANARY,
    Stage.CANARY: Stage.ENFORCE,
}


def is_allowed_transition(current: Stage, target: Stage) -> bool:
    """registered -> shadow -> canary -> enforce; any non-archived stage -> archived."""
    if current == Stage.ARCHIVED:
        return False
    return target == Stage.ARCHIVED or _FORWARD.get(current) == target


@dataclass(frozen=True)
class GateResult:
    passed: bool
    reasons: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


def _number(metrics: dict[str, Any], name: str) -> float | None:
    v = metrics.get(name)
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None


def evaluate_gate(
    candidate: ModelRecord, incumbents: list[ModelRecord], max_p99_latency_ms: float
) -> GateResult:
    """Promotion to ``enforce``.

    - ``p99_latency_ms`` must be below the configured limit.
    - ``recall_at_fpr_0_1pct`` must be at least that of every model currently enforced on the same
      snapshot and game context (no incumbent: no recall comparison).
    """
    reasons: list[str] = []
    details: dict[str, Any] = {"max_p99_latency_ms": max_p99_latency_ms}

    p99 = _number(candidate.metrics, LATENCY_METRIC)
    details[LATENCY_METRIC] = p99
    if p99 is None:
        reasons.append(f"metric {LATENCY_METRIC} is missing")
    elif p99 >= max_p99_latency_ms:
        reasons.append(f"{LATENCY_METRIC} {p99} must be below {max_p99_latency_ms}")

    recall = _number(candidate.metrics, RECALL_METRIC)
    details[RECALL_METRIC] = recall
    if incumbents and recall is None:
        reasons.append(f"metric {RECALL_METRIC} is missing")
    for inc in incumbents:
        base = _number(inc.metrics, RECALL_METRIC)
        details["incumbent"] = {"id": str(inc.id), RECALL_METRIC: base}
        if base is None:
            reasons.append(f"current enforce model {inc.id} has no {RECALL_METRIC} to compare with")
        elif recall is not None and recall < base:
            reasons.append(f"{RECALL_METRIC} {recall} is below the current enforce model's {base}")
    return GateResult(not reasons, reasons, details)


class TransitionError(Exception):
    def __init__(self, message: str, reasons: list[str] | None = None) -> None:
        super().__init__(message)
        self.reasons = reasons or []


class ModelNotFoundError(Exception):
    pass


async def transition_model(
    repo: ModelRepo,
    model_id: UUID,
    target: Stage,
    *,
    actor: str,
    reason: str,
    now: datetime,
    max_p99_latency_ms: float,
    new_id: Callable[[], UUID] = uuid4,
) -> ModelRecord:
    """Validates the transition and the gate, then applies it (and audits it) atomically.

    Promoting to ``enforce`` archives the models it replaces in the same transaction.
    """
    model = await repo.get(model_id)
    if model is None:
        raise ModelNotFoundError(str(model_id))
    current = Stage(model.stage)
    if not is_allowed_transition(current, target):
        raise TransitionError(f"transition {current.value} -> {target.value} is not allowed")

    gate: GateResult | None = None
    incumbents: list[ModelRecord] = []
    if target == Stage.ENFORCE:
        incumbents = await repo.enforced_in_scope(model.snapshot_id, model.game_context, model.id)
        gate = evaluate_gate(model, incumbents, max_p99_latency_ms)
        if not gate.passed:
            raise TransitionError("quality gate failed", gate.reasons)

    gate_json = (
        {"passed": gate.passed, "reasons": gate.reasons, **gate.details}
        if gate is not None
        else None
    )
    transitions = [
        TransitionRecord(
            new_id(), model.id, current.value, target.value, actor, reason, gate_json, now
        )
    ]
    transitions += [
        TransitionRecord(
            new_id(),
            inc.id,
            inc.stage,
            Stage.ARCHIVED.value,
            actor,
            f"replaced by model {model.id}",
            None,
            now,
        )
        for inc in incumbents
    ]
    return await repo.apply_transition(model.id, current.value, transitions)
