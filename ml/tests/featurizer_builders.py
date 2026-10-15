"""Frame builders for featurizer tests (core records, not lake rows)."""

import random

from corvus_ml.features.frames import Frame

SID = bytes([1]) * 16
MS = 1_000_000
TICK_END = 27


def frame(seq, t_ms, segment=0, **kw) -> Frame:
    defaults = {"direction": 1, "packet_type": 14, "event_kind": 0}
    return Frame(segment=segment, seq=seq, offset_nanos=int(t_ms * MS), **(defaults | kw))


def random_run(rng: random.Random, n: int, segment: int = 0, start_seq: int = 0) -> list[Frame]:
    frames, t, yaw = [], 0.0, 0.0
    for i in range(n):
        t += rng.choice((1.0, 5.0, 20.0, 50.0, 400.0, 3000.0))
        kind = rng.choice((1, 1, 2, 3, 4, 0))
        yaw += rng.uniform(-30, 30)
        frames.append(
            frame(
                start_seq + i,
                t,
                segment,
                packet_type=TICK_END if kind == 0 and rng.random() < 0.5 else 14,
                event_kind=kind,
                has_position=kind == 1,
                has_rotation=kind == 1,
                on_ground=rng.choice((True, False, None)) if kind == 1 else None,
                x=rng.uniform(0, 5),
                y=64.0,
                z=rng.uniform(0, 5),
                yaw=((yaw + 180) % 360) - 180,
                pitch=rng.uniform(-40, 40),
                action=2 if kind == 2 else 0,
                entity_id=-1,
            )
        )
    return frames
