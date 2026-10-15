"""Privacy checks: chat/command denylist and identifier-looking strings."""

from __future__ import annotations

import ipaddress
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

_UUID_RE = re.compile(
    r"(?<![0-9a-fA-F])(?:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12}|[0-9a-fA-F]{32})(?![0-9a-fA-F])"
)
_IPV4_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
_IPV6_RE = re.compile(r"(?<![0-9a-fA-F:])[0-9a-fA-F:.]*:[0-9a-fA-F:.]*:[0-9a-fA-F:.]*")


@dataclass(frozen=True)
class WireRule:
    """Denylist entry on the wire packet id, optionally narrowed by direction/state."""

    wire_id: int
    direction: str | None = None  # lower case: serverbound / clientbound
    state: str | None = None  # lower case: play / configuration / ...

    def matches(self, direction: str, state: str, wire_id: int) -> bool:
        return (
            wire_id == self.wire_id
            and self.direction in (None, direction)
            and self.state in (None, state)
        )


@dataclass(frozen=True)
class Denylist:
    """Packet identifiers that must never appear in a capture (chat, commands, signing)."""

    packet_types: frozenset[int] = frozenset()
    wire_rules: tuple[WireRule, ...] = ()

    @property
    def active(self) -> bool:
        return bool(self.packet_types or self.wire_rules)

    def hit(self, direction: str, state: str, packet_type: int, wire_id: int) -> str | None:
        """Reason string if the frame is denylisted, else None."""
        if packet_type and packet_type in self.packet_types:
            return "denylisted_packet_type"
        for rule in self.wire_rules:
            if rule.matches(direction, state, wire_id):
                return "denylisted_wire_id"
        return None

    @classmethod
    def from_dict(cls, data: dict) -> Denylist:
        """Build from a dict: packet_types (list of int), wire_ids (list of int or rule objects).

        A rule object is {"id": n, "state": .., "direction": ..}; state/direction are optional.

        A bare integer in ``wire_ids`` matches that id in any state and direction.
        """
        rules = []
        for entry in data.get("wire_ids", []):
            if isinstance(entry, int):
                rules.append(WireRule(entry))
            else:
                direction = entry.get("direction")
                state = entry.get("state")
                rules.append(
                    WireRule(
                        int(entry["id"]),
                        direction.lower() if direction else None,
                        state.lower() if state else None,
                    )
                )
        return cls(frozenset(int(t) for t in data.get("packet_types", [])), tuple(rules))

    @classmethod
    def from_file(cls, path: Path) -> Denylist:
        return cls.from_dict(json.loads(path.read_text()))


def identifier_kind(value: str) -> str | None:
    """Return "uuid" or "ip" if the string contains something shaped like one."""
    if _UUID_RE.search(value):
        return "uuid"
    for match in _IPV4_RE.finditer(value):
        try:
            ipaddress.IPv4Address(match.group())
        except ValueError:
            continue
        return "ip"
    for match in _IPV6_RE.finditer(value):
        try:
            ipaddress.IPv6Address(match.group())
        except ValueError:
            continue
        return "ip"
    return None


def _is_repeated(descriptor) -> bool:
    # FieldDescriptor.label is deprecated/removed in newer protobuf releases.
    flag = getattr(descriptor, "is_repeated", None)
    if flag is not None:
        return bool(flag)
    from google.protobuf.descriptor import FieldDescriptor

    return descriptor.label == FieldDescriptor.LABEL_REPEATED


def iter_string_fields(message) -> Iterator[tuple[str, str]]:
    """Yield (field path, value) for every populated string field, recursively."""
    from google.protobuf.descriptor import FieldDescriptor

    for descriptor, value in message.ListFields():
        values = value if _is_repeated(descriptor) else [value]
        if descriptor.type == FieldDescriptor.TYPE_STRING:
            for item in values:
                yield descriptor.name, item
        elif descriptor.type == FieldDescriptor.TYPE_MESSAGE:
            for item in values:
                for name, text in iter_string_fields(item):
                    yield f"{descriptor.name}.{name}", text


@dataclass
class PrivacyFindings:
    """Violation counts by reason plus a capped list of locations (never the values)."""

    counts: dict[str, int] = field(default_factory=dict)
    samples: list[str] = field(default_factory=list)
    max_samples: int = 20

    def add(self, reason: str, where: str) -> None:
        self.counts[reason] = self.counts.get(reason, 0) + 1
        if len(self.samples) < self.max_samples:
            self.samples.append(f"{reason}: {where}")

    def merge(self, other: PrivacyFindings) -> None:
        for reason, count in other.counts.items():
            self.counts[reason] = self.counts.get(reason, 0) + count
        room = self.max_samples - len(self.samples)
        if room > 0:
            self.samples.extend(other.samples[:room])

    @property
    def passed(self) -> bool:
        return not self.counts
