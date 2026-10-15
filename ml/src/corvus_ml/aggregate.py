"""Per-file aggregation of capture envelopes into a mergeable FileResult."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from google.protobuf.descriptor import Descriptor, FieldDescriptor

from corvus_ml.model import (
    UNASSIGNED,
    Agg,
    ContextAgg,
    Counters,
    FileResult,
    SegmentSummary,
    SessionSummary,
)
from corvus_ml.privacy import Denylist, PrivacyFindings, identifier_kind, iter_string_fields
from corvus_ml.proto import pb
from corvus_ml.reader import ReadStats, iter_envelopes, read_file


def _enum_names(enum_descriptor, prefix: str) -> dict[int, str]:
    return {v.number: v.name.removeprefix(prefix).lower() for v in enum_descriptor.values}


_DIRECTIONS = _enum_names(pb.Direction.DESCRIPTOR, "DIRECTION_")
_STATES = _enum_names(pb.ConnectionState.DESCRIPTOR, "CONNECTION_STATE_")


def _name(table: dict[int, str], number: int) -> str:
    return table.get(number) or f"unknown({number})"


def _has_strings(descriptor: Descriptor, seen: frozenset[str] = frozenset()) -> bool:
    if descriptor.full_name in seen:
        return False
    seen = seen | {descriptor.full_name}
    for f in descriptor.fields:
        if f.type == FieldDescriptor.TYPE_STRING:
            return True
        if f.type == FieldDescriptor.TYPE_MESSAGE and _has_strings(f.message_type, seen):
            return True
    return False


_FRAME_HAS_STRINGS = _has_strings(pb.Frame.DESCRIPTOR)


@dataclass
class _Segment:
    context: str = UNASSIGNED
    brand: str = ""
    started_ms: int = 0
    agg: Agg = field(default_factory=Agg)
    min_offset: int | None = None
    max_offset: int | None = None
    batch_counters: Counters = field(default_factory=Counters)


@dataclass
class _Session:
    file: str
    session_id: str
    client_protocol: int = 0
    observed_protocol: int = 0
    plugin_version: str = ""
    started_ms: int = 0
    ended_ms: int = 0
    has_start: bool = False
    has_end: bool = False
    end_counters: Counters | None = None
    last_seq: int | None = None
    max_offset: int = 0
    segments: dict[int, _Segment] = field(default_factory=dict)

    def segment(self, number: int) -> _Segment:
        return self.segments.setdefault(number, _Segment())


def _counters(message) -> Counters:
    return Counters(message.dropped_queue_full, message.dropped_privacy, message.listener_errors)


def _scan_strings(message, where: str, findings: PrivacyFindings) -> None:
    for name, value in iter_string_fields(message):
        kind = identifier_kind(value)
        if kind:
            findings.add(f"{kind}_in_string_field", f"{where}.{name}")


def _record_frames(
    session: _Session,
    batch,
    denylist: Denylist,
    findings: PrivacyFindings,
    file_name: str,
) -> None:
    seg = session.segment(batch.segment)
    agg = seg.agg
    agg.counters.add(_counters(batch.counters))
    seg.batch_counters.add(_counters(batch.counters))
    for frame in batch.frames:
        direction = _name(_DIRECTIONS, frame.direction)
        state = _name(_STATES, frame.state)
        size = len(frame.payload)

        agg.frames_ds[direction, state] += 1
        agg.bytes_ds[direction, state] += size
        key = (
            direction,
            state,
            frame.packet_type,
            frame.wire_packet_id if not frame.packet_type else -1,
        )
        agg.packets[key] += 1
        agg.packet_bytes[key] += size
        if size or frame.payload_original_size:
            agg.payload_frames += 1
            if frame.payload_original_size > size:
                agg.payload_truncated += 1
        event = frame.WhichOneof("event")
        if event:
            agg.events[event] += 1

        # Sequence gaps: seq must increase by exactly one within a session.
        if session.last_seq is None:
            session.last_seq = frame.seq
        else:
            expected = session.last_seq + 1
            if frame.seq > expected:
                agg.gaps.events += 1
                agg.gaps.missing_frames += frame.seq - expected
            if frame.seq >= expected:
                session.last_seq = frame.seq
            else:
                agg.gaps.out_of_order += 1

        if seg.min_offset is None or frame.offset_nanos < seg.min_offset:
            seg.min_offset = frame.offset_nanos
        if seg.max_offset is None or frame.offset_nanos > seg.max_offset:
            seg.max_offset = frame.offset_nanos
        session.max_offset = max(session.max_offset, frame.offset_nanos)

        reason = denylist.hit(direction, state, frame.packet_type, frame.wire_packet_id)
        if reason:
            findings.add(reason, f"{file_name} seq={frame.seq} {direction}/{state}")
        if _FRAME_HAS_STRINGS:
            _scan_strings(frame, f"{file_name}.frame", findings)


def _segment_durations(session: _Session) -> dict[int, float]:
    """Seconds spent per segment: wall-clock boundaries, falling back to frame offsets."""
    ordered = sorted(session.segments)
    session_end_s = (
        (session.ended_ms - session.started_ms) / 1000
        if session.has_end and session.started_ms and session.ended_ms
        else session.max_offset / 1e9
    )
    out: dict[int, float] = {}
    for i, number in enumerate(ordered):
        seg = session.segments[number]
        duration = 0.0
        if seg.started_ms and session.started_ms:
            start_s = (seg.started_ms - session.started_ms) / 1000
            if i + 1 < len(ordered):
                nxt = session.segments[ordered[i + 1]]
                end_s = (
                    (nxt.started_ms - session.started_ms) / 1000
                    if nxt.started_ms
                    else session_end_s
                )
            else:
                end_s = session_end_s
            duration = max(0.0, end_s - start_s)
        if duration <= 0 and seg.min_offset is not None and seg.max_offset is not None:
            duration = (seg.max_offset - seg.min_offset) / 1e9
        out[number] = duration
    return out


def _finalize(session: _Session, result: FileResult) -> None:
    durations = _segment_durations(session)
    session_agg = Agg()
    contexts: list[str] = []
    brands: list[str] = []

    batch_total = Counters()
    for seg in session.segments.values():
        batch_total.add(seg.batch_counters)
    effective = batch_total.maxed(session.end_counters) if session.end_counters else batch_total
    remainder = effective.minus(batch_total)

    ordered = sorted(session.segments)
    for i, number in enumerate(ordered):
        seg = session.segments[number]
        if i == len(ordered) - 1:
            seg.agg.counters.add(remainder)  # unattributed end-of-session counters
        duration = durations[number]
        ctx = result.contexts.setdefault(seg.context, ContextAgg())
        ctx.agg.merge(seg.agg)
        ctx.player_seconds += duration
        ctx.segments.append(
            SegmentSummary(
                session.session_id,
                number,
                seg.context,
                duration,
                seg.agg.frames,
                seg.agg.bytes,
            )
        )
        session_agg.merge(seg.agg)
        if seg.context not in contexts:
            contexts.append(seg.context)
        if seg.brand and seg.brand not in brands:
            brands.append(seg.brand)

    if session.has_start and session.started_ms and session.has_end and session.ended_ms:
        duration_s = (session.ended_ms - session.started_ms) / 1000
    else:
        duration_s = 0.0
    duration_s = max(duration_s, session.max_offset / 1e9)

    result.sessions.append(
        SessionSummary(
            session_id=session.session_id,
            file=session.file,
            client_protocol=session.client_protocol,
            observed_protocol=session.observed_protocol,
            plugin_version=session.plugin_version,
            contexts=contexts,
            brands=brands,
            duration_s=duration_s,
            complete=session.has_end,
            agg=session_agg,
            end_counters=session.end_counters,
        )
    )
    if session.has_start:
        result.versions[session.client_protocol, session.observed_protocol] += 1


def aggregate_stream(envelopes, name: str, denylist: Denylist) -> FileResult:
    """Aggregate an iterable of envelopes (one logical capture file)."""
    result = FileResult(files=1, denylist_active=denylist.active)
    sessions: dict[bytes, _Session] = {}

    def get(session_id: bytes) -> _Session:
        if session_id not in sessions:
            sessions[session_id] = _Session(file=name, session_id=session_id.hex())
        return sessions[session_id]

    for env in envelopes:
        kind = env.WhichOneof("body")
        if kind == "session_start":
            m = env.session_start
            s = get(m.session_id)
            s.has_start = True
            s.client_protocol = m.client_protocol_version
            s.observed_protocol = m.observed_protocol_version
            s.plugin_version = m.plugin_version
            s.started_ms = m.started_at_unix_millis
            _scan_strings(m, f"{name}.session_start", result.privacy)
        elif kind == "segment_start":
            m = env.segment_start
            seg = get(m.session_id).segment(m.segment)
            seg.context = m.game_context or UNASSIGNED
            seg.brand = m.client_brand
            seg.started_ms = m.started_at_unix_millis
            _scan_strings(m, f"{name}.segment_start", result.privacy)
        elif kind == "batch":
            m = env.batch
            _record_frames(get(m.session_id), m, denylist, result.privacy, name)
        elif kind == "session_end":
            m = env.session_end
            s = get(m.session_id)
            s.has_end = True
            s.ended_ms = m.ended_at_unix_millis
            s.end_counters = _counters(m.counters)
    for session in sessions.values():
        _finalize(session, result)
    return result


def analyze_file(path: Path, denylist: Denylist) -> FileResult:
    """Aggregate one capture file (top-level so it can run in a worker process)."""
    stats = ReadStats()
    result = aggregate_stream(read_file(path, stats), path.name, denylist)
    result.envelopes = stats.envelopes
    result.truncated_files = int(stats.truncated)
    result.corrupt_files = int(stats.corrupt)
    return result


__all__ = ["aggregate_stream", "analyze_file", "iter_envelopes"]
