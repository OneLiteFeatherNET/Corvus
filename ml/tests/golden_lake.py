"""Deterministic events + segments used for the golden feature fixture.

``tests/fixtures/golden/`` holds the ``windows`` / ``window_tokens`` tables that
``corvus-features build`` produced for this input with the pre-refactor implementation. The
refactored pipeline must reproduce them exactly (``test_features_golden.py``).
"""

from __future__ import annotations

import random
from pathlib import Path

import pyarrow as pa

from lake_builders import (
    Frame,
    attack,
    entity_teleport,
    events_table,
    move,
    sid,
    swing,
    tick_end,
    write_events,
)

GOLDEN_DIR = Path(__file__).parent / "fixtures" / "golden"

# (session, segment, game_context); segment 2 of session 1 is deliberately unmapped.
SEGMENTS = (
    (sid(1), 0, "lobby"),
    (sid(1), 1, "pvp"),
    (sid(2), 0, "pvp"),
)


def _segment_frames(
    rng: random.Random, segment: int, start_seq: int, t0: float, n: int, tick_from: int | None
) -> tuple[list[Frame], int, float]:
    frames: list[Frame] = []
    seq, t = start_seq, t0
    x, y, z, yaw = 0.0, 64.0, 0.0, 0.0
    entities = (7, 8, 9)
    for e in entities:
        frames.append(entity_teleport(seq, t, e, (e * 2.0, 64.0, 3.0), action=4, segment=segment))
        seq += 1
    for i in range(n):
        t += rng.choice((5.0, 20.0, 20.0, 50.0, 120.0, 300.0, 2000.0))
        roll = rng.random()
        if roll < 0.45:
            x += rng.uniform(-0.3, 0.3)
            z += rng.uniform(-0.3, 0.3)
            yaw = ((yaw + rng.uniform(-40, 40) + 180) % 360) - 180
            has_pos = rng.random() < 0.8
            frames.append(
                move(
                    seq,
                    t,
                    xyz=(x, y, z) if has_pos else None,
                    yaw=yaw if rng.random() < 0.7 else None,
                    pitch=rng.uniform(-30, 30),
                    ground=rng.choice((True, False, None)),
                    segment=segment,
                )
            )
        elif roll < 0.65:
            frames.append(swing(seq, t, segment=segment))
        elif roll < 0.8:
            frames.append(attack(seq, t, rng.choice((*entities, 99)), segment=segment))
        elif roll < 0.9:
            frames.append(
                entity_teleport(
                    seq, t, rng.choice(entities), (rng.uniform(-5, 5), 64, 3), segment=segment
                )
            )
        else:
            frames.append(Frame(seq, t, segment=segment, packet_type=31, event_kind=4))
        seq += 1
        if tick_from is not None and i >= tick_from:
            frames.append(tick_end(seq, t + 1.0, segment=segment))
            seq += 1
    return frames, seq, t


def golden_tables() -> list[pa.Table]:
    """Events of two sessions; includes re-entered and unmapped segments and redelivered rows."""
    rng = random.Random(20261015)
    s1: list[Frame] = []
    seq, t = 0, 0.0
    # segment 0 (no tick end), 1 (tick end from the start), 2 (unmapped), 1 again (resumed)
    for segment, n, tick in ((0, 90, None), (1, 140, 0), (2, 40, None), (1, 70, 30)):
        part, seq, t = _segment_frames(rng, segment, seq, t + 100.0, n, tick)
        s1.extend(part)
    s2, _, _ = _segment_frames(rng, 0, 0, 0.0, 120, 50)  # tick end only from frame 50 on
    redelivered = s1[10:20]  # same seq again, different file: first row wins
    return [
        events_table(sid(1), s1),
        events_table(sid(2), s2),
        events_table(sid(1), redelivered),
    ]


def write_golden_events(directory: Path) -> Path:
    return write_events(directory / "events.parquet", *golden_tables())
