"""Mergeable aggregation results. Everything here is small and picklable."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from corvus_ml.privacy import PrivacyFindings

UNASSIGNED = "unassigned"


@dataclass
class Counters:
    queue_full: int = 0
    privacy: int = 0
    listener_errors: int = 0

    def add(self, other: Counters) -> None:
        self.queue_full += other.queue_full
        self.privacy += other.privacy
        self.listener_errors += other.listener_errors

    def maxed(self, other: Counters) -> Counters:
        return Counters(
            max(self.queue_full, other.queue_full),
            max(self.privacy, other.privacy),
            max(self.listener_errors, other.listener_errors),
        )

    def minus(self, other: Counters) -> Counters:
        return Counters(
            self.queue_full - other.queue_full,
            self.privacy - other.privacy,
            self.listener_errors - other.listener_errors,
        )


@dataclass
class GapStats:
    """Sequence-number anomalies: gaps (events), frames missing inside them, and reordering."""

    events: int = 0
    missing_frames: int = 0
    out_of_order: int = 0

    def add(self, other: GapStats) -> None:
        self.events += other.events
        self.missing_frames += other.missing_frames
        self.out_of_order += other.out_of_order


@dataclass
class Agg:
    """Frame statistics for one segment, context or the whole run."""

    frames_ds: Counter = field(default_factory=Counter)  # (direction, state) -> frames
    bytes_ds: Counter = field(default_factory=Counter)  # (direction, state) -> payload bytes
    packets: Counter = field(default_factory=Counter)  # (direction, state, type, wire) -> frames
    packet_bytes: Counter = field(default_factory=Counter)
    events: Counter = field(default_factory=Counter)  # decoded event name -> frames
    counters: Counters = field(default_factory=Counters)
    gaps: GapStats = field(default_factory=GapStats)

    def merge(self, other: Agg) -> None:
        self.frames_ds.update(other.frames_ds)
        self.bytes_ds.update(other.bytes_ds)
        self.packets.update(other.packets)
        self.packet_bytes.update(other.packet_bytes)
        self.events.update(other.events)
        self.counters.add(other.counters)
        self.gaps.add(other.gaps)

    @property
    def frames(self) -> int:
        return sum(self.frames_ds.values())

    @property
    def bytes(self) -> int:
        return sum(self.bytes_ds.values())

    def by_direction(self, counter: Counter) -> dict[str, int]:
        out: Counter = Counter()
        for (direction, _state), n in counter.items():
            out[direction] += n
        return dict(out)


@dataclass
class SegmentSummary:
    """One player in one game context; the unit for per-player rate distributions."""

    session_id: str
    segment: int
    context: str
    duration_s: float
    frames: int
    bytes: int


@dataclass
class ContextAgg:
    agg: Agg = field(default_factory=Agg)
    player_seconds: float = 0.0
    segments: list[SegmentSummary] = field(default_factory=list)

    def merge(self, other: ContextAgg) -> None:
        self.agg.merge(other.agg)
        self.player_seconds += other.player_seconds
        self.segments.extend(other.segments)


@dataclass
class SessionSummary:
    session_id: str
    file: str
    client_protocol: int
    observed_protocol: int
    plugin_version: str
    contexts: list[str]
    brands: list[str]
    duration_s: float
    complete: bool
    agg: Agg
    end_counters: Counters | None


@dataclass
class FileResult:
    """Aggregation of one capture file; merged pairwise into the run result."""

    files: int = 0
    envelopes: int = 0
    truncated_files: int = 0
    corrupt_files: int = 0
    sessions: list[SessionSummary] = field(default_factory=list)
    contexts: dict[str, ContextAgg] = field(default_factory=dict)
    versions: Counter = field(default_factory=Counter)  # (client, observed) -> sessions
    privacy: PrivacyFindings = field(default_factory=PrivacyFindings)
    denylist_active: bool = False

    def merge(self, other: FileResult) -> None:
        self.files += other.files
        self.envelopes += other.envelopes
        self.truncated_files += other.truncated_files
        self.corrupt_files += other.corrupt_files
        self.sessions.extend(other.sessions)
        for name, ctx in other.contexts.items():
            self.contexts.setdefault(name, ContextAgg()).merge(ctx)
        self.versions.update(other.versions)
        self.privacy.merge(other.privacy)
        self.denylist_active = self.denylist_active or other.denylist_active
