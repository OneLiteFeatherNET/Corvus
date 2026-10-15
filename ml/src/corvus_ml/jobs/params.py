"""Strict reading of the free-form ``CORVUS_JOB_PARAMS`` object."""

from __future__ import annotations

from typing import Any


class ParamError(ValueError):
    """A job parameter is unknown or has the wrong type or range."""


class Params:
    """Typed accessors over the params dict; ``done()`` rejects keys nobody asked for."""

    def __init__(self, raw: dict[str, Any]) -> None:
        self._raw = dict(raw)
        self._seen: set[str] = set()

    def has(self, name: str) -> bool:
        return name in self._raw

    def raw(self, name: str, default: Any = None) -> Any:
        self._seen.add(name)
        return self._raw.get(name, default)

    def string(self, name: str, default: str | None = None) -> str | None:
        value = self.raw(name, default)
        if value is not None and not isinstance(value, str):
            raise ParamError(f"{name} must be a string")
        return value

    def integer(self, name: str, default: int, low: int = 1, high: int = 1_000_000) -> int:
        value = self.raw(name, default)
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ParamError(f"{name} must be an integer in [{low}, {high}]")
        return value

    def number(self, name: str, default: float, low: float, high: float) -> float:
        value = self.raw(name, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ParamError(f"{name} must be a number")
        if not low <= value <= high:
            raise ParamError(f"{name} must be in [{low}, {high}]")
        return float(value)

    def boolean(self, name: str, default: bool) -> bool:
        value = self.raw(name, default)
        if not isinstance(value, bool):
            raise ParamError(f"{name} must be true or false")
        return value

    def strings(self, name: str, default: list[str] | None = None) -> list[str] | None:
        value = self.raw(name, default)
        if value is None:
            return None
        if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
            raise ParamError(f"{name} must be a list of non-empty strings")
        return value

    def numbers(self, name: str, default: list[float] | None = None) -> list[float] | None:
        value = self.raw(name, default)
        if value is None:
            return None
        ok = isinstance(value, list) and all(
            isinstance(v, (int, float)) and not isinstance(v, bool) for v in value
        )
        if not ok:
            raise ParamError(f"{name} must be a list of numbers")
        return [float(v) for v in value]

    def mapping(self, name: str) -> dict[str, Any]:
        value = self.raw(name, {})
        if not isinstance(value, dict):
            raise ParamError(f"{name} must be an object")
        return value

    def done(self) -> None:
        extra = sorted(set(self._raw) - self._seen)
        if extra:
            raise ParamError(f"unknown params: {extra}")

    def game_contexts(self) -> list[str] | None:
        """``game_contexts`` (list) or ``game_context`` (one name); None = every context."""
        many, one = self.strings("game_contexts"), self.string("game_context")
        if many and one:
            raise ParamError("use either game_context or game_contexts")
        return many or ([one] if one else None)
