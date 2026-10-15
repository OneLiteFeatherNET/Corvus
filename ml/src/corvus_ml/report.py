"""Turn a merged FileResult into a JSON-serialisable report and a text rendering."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import asdict

from corvus_ml.model import Agg, ContextAgg, FileResult

TOP_PACKETS = 15


def rate(count: float, seconds: float) -> float | None:
    return count / seconds if seconds > 0 else None


def drop_rate(agg: Agg) -> float | None:
    """Share of frames lost to a full queue: dropped / (captured + dropped)."""
    total = agg.frames + agg.counters.queue_full
    return agg.counters.queue_full / total if total else None


def percentile(sorted_values: list[float], q: float) -> float | None:
    """Nearest-rank percentile of an ascending list."""
    if not sorted_values:
        return None
    index = max(0, math.ceil(q * len(sorted_values)) - 1)
    return sorted_values[index]


def _direction_rows(agg: Agg, seconds: float) -> dict[str, dict]:
    frames = agg.by_direction(agg.frames_ds)
    sizes = agg.by_direction(agg.bytes_ds)
    return {
        d: {
            "frames": frames[d],
            "bytes": sizes.get(d, 0),
            "frames_per_s": rate(frames[d], seconds),
            "bytes_per_s": rate(sizes.get(d, 0), seconds),
        }
        for d in sorted(frames)
    }


def _state_rows(agg: Agg, seconds: float) -> list[dict]:
    return [
        {
            "direction": d,
            "state": s,
            "frames": n,
            "bytes": agg.bytes_ds[d, s],
            "frames_per_s": rate(n, seconds),
            "bytes_per_s": rate(agg.bytes_ds[d, s], seconds),
        }
        for (d, s), n in sorted(agg.frames_ds.items())
    ]


def _top_packets(agg: Agg, seconds: float) -> list[dict]:
    rows = []
    for (d, s, ptype, wire), n in agg.packets.most_common(TOP_PACKETS):
        rows.append(
            {
                "direction": d,
                "state": s,
                "packet_type": ptype,
                "wire_packet_id": None if ptype else wire,
                "frames": n,
                "frames_per_s": rate(n, seconds),
                "bytes_per_s": rate(agg.packet_bytes[d, s, ptype, wire], seconds),
            }
        )
    return rows


def context_report(ctx: ContextAgg) -> dict:
    agg, seconds = ctx.agg, ctx.player_seconds
    per_player = sorted(
        r for seg in ctx.segments if (r := rate(seg.frames, seg.duration_s)) is not None
    )
    return {
        "segments": len(ctx.segments),
        "player_seconds": seconds,
        "frames": agg.frames,
        "bytes": agg.bytes,
        "frames_per_s_per_player": rate(agg.frames, seconds),
        "bytes_per_s_per_player": rate(agg.bytes, seconds),
        "per_player_frames_per_s": {
            "min": per_player[0] if per_player else None,
            "median": percentile(per_player, 0.5),
            "p95": percentile(per_player, 0.95),
            "max": per_player[-1] if per_player else None,
        },
        "by_direction": _direction_rows(agg, seconds),
        "by_direction_state": _state_rows(agg, seconds),
        "top_packets": _top_packets(agg, seconds),
        "unmapped_frames": sum(n for (_, _, t, _), n in agg.packets.items() if t == 0),
        "events": {
            name: {"frames": n, "frames_per_s": rate(n, seconds)}
            for name, n in sorted(agg.events.items())
        },
        "counters": asdict(agg.counters),
        "drop_rate": drop_rate(agg),
        "gaps": asdict(agg.gaps),
    }


def _session_report(s) -> dict:
    return {
        "session_id": s.session_id,
        "file": s.file,
        "client_protocol": s.client_protocol,
        "observed_protocol": s.observed_protocol,
        "translated": s.client_protocol != s.observed_protocol,
        "plugin_version": s.plugin_version,
        "contexts": s.contexts,
        "client_brands": s.brands,
        "duration_s": s.duration_s,
        "complete": s.complete,
        "frames": s.agg.frames,
        "bytes": s.agg.bytes,
        "by_direction": _direction_rows(s.agg, s.duration_s),
        "by_state": _state_rows(s.agg, s.duration_s),
        "counters": asdict(s.agg.counters),
        "end_counters": asdict(s.end_counters) if s.end_counters else None,
        "drop_rate": drop_rate(s.agg),
        "gaps": asdict(s.agg.gaps),
    }


def build_report(result: FileResult) -> dict:
    overall = ContextAgg()
    for ctx in result.contexts.values():
        overall.merge(ctx)
    versions: Counter = result.versions
    return {
        "files": {
            "count": result.files,
            "envelopes": result.envelopes,
            "truncated": result.truncated_files,
            "corrupt": result.corrupt_files,
        },
        "sessions": [
            _session_report(s) for s in sorted(result.sessions, key=lambda s: s.session_id)
        ],
        "contexts": {name: context_report(c) for name, c in sorted(result.contexts.items())},
        "overall": context_report(overall),
        "protocol_versions": [
            {
                "client_protocol": c,
                "observed_protocol": o,
                "translated": c != o,
                "sessions": n,
            }
            for (c, o), n in sorted(versions.items())
        ],
        "privacy": {
            "passed": result.privacy.passed,
            "denylist_active": result.denylist_active,
            "violations": dict(result.privacy.counts),
            "samples": result.privacy.samples,
        },
    }


def _fmt(value: float | None, digits: int = 1) -> str:
    return "-" if value is None else f"{value:,.{digits}f}"


def render_text(report: dict) -> str:
    lines: list[str] = []
    f = report["files"]
    lines.append(
        f"files={f['count']} envelopes={f['envelopes']} truncated={f['truncated']} "
        f"corrupt={f['corrupt']} sessions={len(report['sessions'])}"
    )
    for name, c in [*report["contexts"].items(), ("ALL", report["overall"])]:
        lines.append("")
        lines.append(
            f"== context {name}: {c['segments']} player-segments, "
            f"{_fmt(c['player_seconds'])} player-s, {c['frames']:,} frames, {c['bytes']:,} B"
        )
        pp = c["per_player_frames_per_s"]
        lines.append(
            f"   per player: {_fmt(c['frames_per_s_per_player'])} frames/s "
            f"{_fmt(c['bytes_per_s_per_player'])} B/s "
            f"(median {_fmt(pp['median'])}, p95 {_fmt(pp['p95'])}, max {_fmt(pp['max'])} frames/s)"
        )
        for d, row in c["by_direction"].items():
            lines.append(
                f"   {d:<12} {_fmt(row['frames_per_s'])} frames/s {_fmt(row['bytes_per_s'])} B/s"
            )
        for row in c["by_direction_state"]:
            lines.append(
                f"     {row['direction']}/{row['state']:<14} "
                f"{_fmt(row['frames_per_s'])} frames/s {_fmt(row['bytes_per_s'])} B/s"
            )
        lines.append("   top packet types (frames/s):")
        for row in c["top_packets"][:10]:
            ident = (
                f"type {row['packet_type']}"
                if row["packet_type"]
                else f"unmapped wire {row['wire_packet_id']}"
            )
            lines.append(
                f"     {row['direction']}/{row['state']} {ident}: {_fmt(row['frames_per_s'], 2)}"
            )
        if c["events"]:
            evs = ", ".join(f"{k} {_fmt(v['frames_per_s'], 2)}/s" for k, v in c["events"].items())
            lines.append(f"   events: {evs}")
        ct = c["counters"]
        dr = c["drop_rate"]
        lines.append(
            f"   drops: queue_full={ct['queue_full']} privacy={ct['privacy']} "
            f"listener_errors={ct['listener_errors']} drop_rate="
            f"{'-' if dr is None else f'{dr * 100:.4f}%'}"
        )
        g = c["gaps"]
        lines.append(
            f"   seq gaps: events={g['events']} missing={g['missing_frames']} "
            f"out_of_order={g['out_of_order']}"
        )
    lines.append("")
    lines.append("== protocol versions (client -> observed)")
    for v in report["protocol_versions"]:
        tag = "translated" if v["translated"] else "native"
        lines.append(
            f"   {v['client_protocol']} -> {v['observed_protocol']} ({tag}): "
            f"{v['sessions']} sessions"
        )
    lines.append("")
    lines.append("== sessions")
    for s in report["sessions"]:
        frames_s = sum(r["frames_per_s"] or 0 for r in s["by_direction"].values())
        lines.append(
            f"   {s['session_id'][:8]} {','.join(s['contexts'])} {_fmt(s['duration_s'])}s "
            f"{_fmt(frames_s)} frames/s drops={s['counters']['queue_full']} "
            f"gaps={s['gaps']['events']}{'' if s['complete'] else ' (no session_end)'}"
        )
    p = report["privacy"]
    lines.append("")
    lines.append(f"== privacy: {'PASS' if p['passed'] else 'FAIL'}")
    if not p["denylist_active"]:
        lines.append("   WARNING: no denylist given; chat/command packet check is inactive")
    for reason, n in p["violations"].items():
        lines.append(f"   {reason}: {n}")
    for sample in p["samples"]:
        lines.append(f"     {sample}")
    return "\n".join(lines)
