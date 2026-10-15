# Reference: feature and window stage (v1)

Input is the lake table `events` plus a `segments(session_id, segment, game_context)` table
(see [data-lake.md](data-lake.md)). Output is two Parquet tables written by `corvus-features build`
(`ml/src/corvus_ml/features/`). The feature logic is an incremental core
([section 6](#6-incremental-featurizer-offline-and-online)) that the online scoring service runs
unchanged; `build` also writes [`feature_spec.json`](feature-spec.md) next to the tables.

```
uv run corvus-features build --events <dir|s3://...> --segments <parquet|csv|arrow> --out <dir>
    [--context pvp] [--session <hex>]... [--window 256] [--overlap 0.5] [--max-duration 30]
    [--min-frames 16] [--dt-edges-ms ...] [--workers N] [--group-size 4]
    [--s3-endpoint URL --s3-region R --s3-scheme http|https]
```

S3 credentials come from the standard AWS environment. A CSV `session_id` is the 32-char hex string.

## 1. Pipeline

1. Sessions are selected from the segments table (`--context`, `--session`), sorted, and split into
   groups of `--group-size` sessions (default 4).
2. A worker reads one group: the dataset scan is filtered by `session_id IN (...)` and projects only
   the needed columns, in batches of at most 65 536 rows. Per session the rows are ordered by `seq`
   and de-duplicated on (`session_id`, `seq`); the first row of a duplicate wins.
3. The session is cut into runs of equal `segment`. Runs whose segment is absent from the segments
   table are skipped. **All per-run state is reset at a segment boundary** (previous frame, position,
   rotation, entity positions), and windows never cross it.
4. The frames of a run are fed one by one into a `SessionFeaturizer`: tokens are computed for the
   serverbound frames, clientbound `entity_motion` frames only feed the entity position state.
5. Windows are cut and aggregated by the featurizer as soon as their extent is known, then written. One file per group: `windows/part-<group>.parquet` and
   `window_tokens/part-<group>.parquet`. The output does not depend on the worker count.

Worker count defaults to the cgroup CPU limit (`corvus_ml.cgroup.default_workers`). Memory is
bounded by the group (sessions per task) times the largest session, not by the dataset: a
synthetic 4 x 300 000 frame run with `--group-size 1` peaked at about 650 MiB including the generation of
the test data in the same process. Lower `--group-size` for very long sessions.

## 2. Token features (table `window_tokens`)

One row per serverbound frame. Unknown values are never NaN: they are 0 with the matching `has_*`
flag, or an explicit sentinel (documented per feature).

| Feature | Type | Unit | Definition |
|---|---|---|---|
| `packet_type` | uint32 | id | Corvus vocabulary id (`vocabulary.json`), version stable |
| `direction` | uint8 | enum | proto `Direction` (always 1 here) |
| `event_kind` | uint8 | enum | as in `events` |
| `domain` | uint8 | enum | 1 movement (`event_kind` 1), 2 combat (2, 3), 3 interaction (4, 5, 6), 0 other |
| `action` | uint8 | enum | `events.action` (2 = attack for interact_entity), 0 if null |
| `dt_bucket` | int16 | index | index of the first edge greater than the gap to the previous serverbound frame (`searchsorted(edges, dt, side="right")`); edges default to 0.5, 1, 2, 5, 10, 20, 35, 50, 75, 100, 200, 500, 1000, 5000 ms, so values are `0..len(edges)`; first frame of a segment: `len(edges)+1` |
| `log_dt` | float32 | ln(1+ms) | `ln(1 + dt_ms)`, 0 for the first frame |
| `has_pos_delta` | uint8 | flag | movement frame with position and a known previous position |
| `dx`, `dy`, `dz` | float32 | blocks | position minus the last known position (any earlier movement frame with position); 0 if `has_pos_delta` = 0 |
| `has_rot_delta` | uint8 | flag | movement frame with rotation and a known previous rotation |
| `dyaw` | float32 | degrees | `((yaw - prev_yaw + 180) mod 360) - 180`, in [-180, 180); 179 to -179 gives +2 |
| `dpitch` | float32 | degrees | `pitch - prev_pitch` (no wrap) |
| `rot_speed` | float32 | deg/s | `hypot(dyaw, dpitch) / dt_rot`, `dt_rot` = seconds since the previous rotation sample; 0 if `dt_rot` <= 0 |
| `rot_accel` | float32 | deg/s^2 | `(rot_speed - prev_rot_speed) / dt_rot`; 0 if there is no previous speed |
| `on_ground` | int8 | flag | movement frames: 0 or 1; -1 for everything else or null |
| `t_since_swing` | float32 | s | attack frames only: time since the last swing frame, capped at 10; 10 if none; 0 on other frames |
| `t_since_move` | float32 | s | attack frames only: time since the last movement frame, same cap |
| `target_dist` | float32 | blocks | attack frames only: distance eye to the target aim point; -1 if the target entity or the player pose is unknown |
| `target_angle` | float32 | degrees | attack frames only: angle between the look vector and the eye to aim point direction; -1 if unknown |

Attack frame: `event_kind` = 2 and `action` = 2 (`ACTION_ATTACK`).

Geometry for `target_*`:

- Eye = last known player position + (0, 1.62, 0).
- Aim point = last known entity position + (0, 0.9, 0) (mid body of a 1.8 block entity).
- Look vector from the last known rotation, Minecraft convention:
  `(-sin(yaw) cos(pitch), -sin(pitch), cos(yaw) cos(pitch))`, angles in radians.
- `target_angle = acos(clamp(look . v / |v|))` in degrees, `v` = aim point minus eye.

Entity positions come from clientbound `entity_motion` frames (`events.action` = kind): kind 2
(teleport) and kind 4 (spawn) store the absolute position, kind 1 (relative) adds the delta to a known position and is
ignored for unknown entities, kind 3 (velocity) is ignored. The state is a bounded LRU (256 entities,
`FeatureConfig.entity_cache`), reset per segment.

## 3. Windows (table `windows`)

A window is up to N serverbound frames (`--window`, default 256) that start within `--max-duration`
(default 30 s) of its first frame. The next window starts `n - floor(n * overlap)` frames after the
start of the current one (n = frames of that window; 128 for a full 256 frame window at 50 % overlap).
The window that contains the last frame of the run is the last one. Windows with fewer than
`--min-frames` (default 16) frames are dropped.

| Column | Type | Meaning |
|---|---|---|
| `session_id`, `segment` | binary(16), uint32 | |
| `game_context` | string | from the segments table |
| `window_index` | uint32 | counter per (session, segment), from 0 |
| `start_offset_nanos`, `end_offset_nanos` | uint64 | `offset_nanos` of the first and last frame |
| `first_seq`, `last_seq` | uint64 | join keys back to `events` (and to labels later) |
| `n_frames` | uint32 | |
| `cut` | string | `full` (N frames), `duration` (max duration hit), `end` (run ended) |
| `cps` | float64 | swings per second: `count(event_kind = 3) / span` |
| `attacks_per_s` | float64 | `count(attack frames) / span` |
| `packets_per_s` | float64 | `n_frames / span` (serverbound) |
| `movement_per_s` | float64 | `count(event_kind = 1) / span` |
| `rot_var` | float64 | population variance of `dyaw` plus population variance of `dpitch` over frames with `has_rot_delta`; 0 with fewer than 2 samples |
| `rot_speed_mean`, `rot_speed_max` | float64 | over frames with `has_rot_delta`; 0 if none |
| `on_ground_frac` | float64, null | share of `on_ground` = 1 among frames with `on_ground` >= 0; null if none |
| `tick_end_rate` | float64, null | `count(CLIENT_TICK_END) / span`; null if the segment never sends it |

`span` is `(end_offset_nanos - start_offset_nanos) / 1e9`; all rates are 0 when `span` is 0.

## 4. Why a separate `window_tokens` table

Tokens are stored long (`session_id`, `segment`, `window_index`, `pos`, features) instead of
fixed-size list columns in `windows` because:

- the schema does not depend on `--window`, so runs with different N stay comparable and a model can
  change N without a schema migration;
- duration-cut and tail windows are shorter than N and need no padding or mask column;
- columnar compression works per feature column, and a consumer can read aggregates without touching
  the 10 to 20 times larger token data;
- the cost is a regroup by (`session_id`, `segment`, `window_index`) when training, ordered by `pos`.

With 50 % overlap every frame is stored about twice. Per-domain views are a filter on `domain`.

## 5. Determinism

Same input produces byte-identical files: groups are sorted, one file per group index, fixed Parquet
settings (zstd level 3), no timestamps, and `seq` order with first-wins de-duplication.

## 6. Incremental featurizer (offline and online)

The code that computes tokens, cuts windows and aggregates them is one pure, numpy-only component
(`ml/src/corvus_ml/features/featurizer.py`, no Arrow import). The offline build and the scoring
service both use it, so there is no training/serving skew by construction; `tests/
test_features_golden.py` pins the offline output to a fixture generated before the extraction.

```python
fz = SessionFeaturizer(session_id, FeaturizerConfig(window=256, overlap=0.5, ...))
fz.begin_segment(segment, tick_end=None)     # optional; feed() starts runs by itself
for frame in frames:                          # corvus_ml.features.frames.Frame, one events row
    for window in fz.feed(frame):             # CompletedWindow
        ...
for window in fz.flush():                     # tail of the current run
    ...
```

| Item | Definition |
|---|---|
| `Frame` | NamedTuple mirroring the `events` columns the featurizer reads: `segment`, `seq`, `offset_nanos`, `direction`, `packet_type`, `event_kind`, `has_position`, `has_rotation`, `on_ground` (None or -1 = unknown), `x`, `y`, `z`, `yaw`, `pitch` (NaN = null), `entity_id`, `action` |
| `FeaturizerConfig` | frozen: `window`, `overlap`, `max_duration_s`, `min_frames`, `dt_edges_ms`, `entity_cache`; the offline `FeatureConfig` adds only I/O knobs |
| `CompletedWindow` | `session_id`, `segment`, `window_index`, `start_offset_nanos`, `end_offset_nanos`, `first_seq`, `last_seq`, `n_frames`, `cut`; `aggregates` float64 vector in the order of the aggregate columns of section 3 (NaN = null); `tokens` float32 matrix `(n_frames, 21)` in the column order of section 2 (all token features, `packet_type` first); `token_seq`, `token_offset_nanos` uint64 per row |
| Emission | A window is returned from `feed` once its extent is certain: its `window`-th frame arrived, or a frame beyond `max_duration_s` arrived. `flush` emits the tail (`cut = end`) subject to `min_frames`, and resets the run. A window that already reached the last frame is not emitted twice. |
| Segment boundary | `feed` of a frame with another `segment` first flushes the run; previous frame, position, rotation, entity positions and buffered tokens are reset. `window_index` continues per segment if a segment is entered again. |
| Redelivery | A frame with `seq` not greater than the last one is ignored, so at-least-once delivery is harmless. Frames must otherwise arrive in `seq` order. |
| `tick_end_rate` | Null when the run never sends `CLIENT_TICK_END`. The offline build knows this for the whole run and passes it to `begin_segment(tick_end=...)`. Online (`tick_end=None`) it is inferred from the frames seen so far, so a window that completes before the first tick end reports null where the offline build would report 0.0 if the run sends tick ends later. |
| Memory | Consumed frames are released; the buffer holds about one window of tokens per run. |

The streaming result equals the batch definition of sections 2 and 3 for any chunking of the same
frames (`tests/test_featurizer.py` checks this against an independent whole-run reference).
Token values are stored as float32; every integer feature is far below 2^24, so the conversion back
to the Arrow types of section 2 is lossless.

The model inputs derived from windows (aggregate vector order, token tensor layout and padding) are
defined once, in `model_input.py`, and described by the [feature spec](feature-spec.md).
