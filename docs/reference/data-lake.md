# Reference: capture transport, data lake and catalog (v1)

This is the contract between the capture plugin, the ingest service and the ML side.
Changing it requires a schema version bump or an append-only change.

## 1. Transport (NATS JetStream)

| Item | Value |
|---|---|
| Stream | `CORVUS_CAPTURE` |
| Subjects | `corvus.capture.v1.>` |
| Publish subject | `corvus.capture.v1.<proxy_id>.<partition>`; `partition` = first 4 bytes of `session_id` as big-endian unsigned int mod `partitions` (default 16), formatted as two-digit decimal (`00`..`15`) |
| Message body | one serialized `corvus.capture.v1.Envelope` (no length prefix) |
| Header `Nats-Msg-Id` | `<session-id-hex>-<n>`, `n` = per-session envelope counter starting at 0 (JetStream de-duplication) |
| Header `Corvus-Schema` | `capture.v1` |
| Max message size | 1 MiB; the plugin splits `CaptureBatch`es so that each serialized envelope stays below 512 KiB |
| Stream limits | file storage, `max_age` 24 h, `max_bytes` configurable (default 10 GiB), discard old, duplicate window 2 min |
| Consumer (ingest) | durable pull consumer `corvus-ingest` on `corvus.capture.v1.>`, explicit ack, `ack_wait` 120 s, `max_ack_pending` 1000; all ingest replicas share it |

All envelopes of one session use the same partition, so a consumer that filters on a partition sees a
session completely. The partition count is part of the deployment config and must be identical on all
proxies; changing it requires draining the scorers.

Envelope kinds: `SessionStart`, `SegmentStart`, `CaptureBatch`, `SessionEnd`. Every `CaptureBatch` carries
`session_id` and `segment`, so ingest never depends on message order.

The plugin publishes asynchronously and never blocks a Netty thread. If the in-flight limit is reached or a publish
fails, the frames of that batch are dropped and counted in `CaptureCounters.dropped_transport`.

## 2. Object storage layout (S3 / MinIO)

Bucket configurable (default `corvus-lake`). All files are Parquet, zstd-compressed, written by ingest.

```
lake/events/date=YYYY-MM-DD/part-<ulid>.parquet   long retention (per pool)
lake/raw/date=YYYY-MM-DD/part-<ulid>.parquet      short retention (default 21 days)
_staging/<table>/<ulid>.parquet                   upload target before promotion
```

`date` is the UTC date of the session start. Partitioning by player is forbidden (tiny files).

### Table `events` (one row per captured frame, also frames without a decoded event)

| Column | Arrow type | Notes |
|---|---|---|
| `session_id` | fixed_size_binary(16) | |
| `segment` | uint32 | |
| `seq` | uint64 | dedup key together with `session_id` |
| `offset_nanos` | uint64 | since session start |
| `direction` | uint8 | proto `Direction` value |
| `state` | uint8 | proto `ConnectionState` value |
| `packet_type` | uint32 | Corvus vocabulary id (`proto/corvus/capture/v1/vocabulary.json`) |
| `wire_packet_id` | int32 | |
| `payload_original_size` | uint32 | 0 if no payload |
| `event_kind` | uint8 | 0 none, 1 movement, 2 interact_entity, 3 swing, 4 dig, 5 place, 6 held_item, 7 entity_motion |
| `has_position`, `has_rotation`, `on_ground` | bool (nullable) | movement |
| `x`, `y`, `z` | float64 (nullable) | movement position, entity_motion vector |
| `yaw`, `pitch` | float32 (nullable) | movement |
| `entity_id` | int32 (nullable) | interact_entity, entity_motion |
| `action` | uint8 (nullable) | interact action / entity_motion kind (1 relative, 2 teleport, 3 velocity, 4 spawn) |
| `sneaking`, `off_hand` | bool (nullable) | interact, swing, place |
| `block_x`, `block_y`, `block_z`, `face`, `status` | int32 (nullable) | dig, place |
| `slot` | int32 (nullable) | held_item |

Rows are sorted by (`session_id`, `seq`) within a file.

### Table `raw` (short retention, crypto-shredded)

| Column | Arrow type | Notes |
|---|---|---|
| `session_id` | fixed_size_binary(16) | |
| `segment` | uint32 | |
| `seq` | uint64 | |
| `payload_original_size` | uint32 | |
| `nonce` | fixed_size_binary(12) | |
| `ciphertext` | binary | AES-256-GCM of the payload with the player's DEK, AAD = `session_id || seq (8 bytes big-endian)` |

Only frames that carry a payload get a `raw` row.

## 3. Keys and erasure

- Every pseudonymised player gets a random 256-bit data encryption key (DEK) on first sight.
- The DEK is stored wrapped (AES-256-GCM) with a key encryption key (KEK) from `CORVUS_KEK` (base64, 32 bytes),
  identified by `CORVUS_KEK_ID`. The KEK never touches the database or the lake.
- Erasure of a player: delete the `player_keys` row (crypto-shredding of `raw`), mark `players.erased_at`, add the
  pseudonym to the blocklist; `corvus-compact` later rewrites `events` and still-existing `raw` files without the player's sessions (section 7).

## 4. Catalog (PostgreSQL, schema `corvus`)

| Table | Key | Content |
|---|---|---|
| `players` | `pseudonym bytea` | `key_id`, `first_seen`, `last_seen`, `erased_at` |
| `player_keys` | `pseudonym bytea` → players | `dek_wrapped bytea`, `kek_id text`, `created_at` |
| `sessions` | `session_id bytea` | `pseudonym`, `proxy_id`, `client_protocol`, `observed_protocol`, `plugin_version`, `started_at`, `ended_at`, counters (`dropped_queue_full`, `dropped_privacy`, `listener_errors`, `dropped_transport`, summed over batches) |
| `segments` | (`session_id`, `segment`) | `backend_server`, `game_context`, `client_brand`, `started_at` |
| `lake_files` | `id bigserial`, unique `s3_key` | `table_name`, `date`, `row_count`, `size_bytes`, `schema_version`, `created_at`, `deleted_at` |
| `lake_file_sessions` | (`file_id`, `session_id`) | which sessions a file contains (pool selection, erasure, compaction) |

Migration `0005_compaction.sql` adds `lake_files.replaced_by` (successor of a compacted file), `lake_files.purge_pending`
(row marked deleted, object not yet removed), `erasure_requests.compacted_at` and the view `corvus.erased_sessions`
(`session_id`, `pseudonym`, `erased_at` for sessions of erased players).

Migrations are plain, numbered SQL files applied in order under a PostgreSQL advisory lock; they are never edited
after release.

## 5. Ingest delivery semantics

1. Fetch a batch of messages, decode envelopes, upsert `players`/`sessions`/`segments` immediately.
2. Buffer frames in Arrow builders per (table, date); flush when a buffer exceeds the row or byte limit or its age
   limit (defaults 200 000 rows, 64 MiB, 60 s).
3. Flush: write Parquet to `_staging/`, copy to the final key, insert `lake_files` + `lake_file_sessions` in one
   transaction, then ack all messages whose frames are in the file, then delete the staging object.
4. Redelivery can produce duplicate rows; readers de-duplicate on (`session_id`, `seq`). Final objects without a
   catalog row older than 1 h are orphans and are removed by housekeeping.

## 6. Online scoring

| Item | Value |
|---|---|
| Consumer (scoring) | one durable pull consumer per scorer replica, `corvus-score-<ordinal>`, `filter_subjects` = the replica's partitions (`corvus.capture.v1.*.<pp>`, `<pp>` formatted like the plugin: two digits, wider above 100 partitions, e.g. `005` of 128), deliver policy `new` (scoring never replays history), explicit ack right after the frames are fed into the featurizer. An existing consumer with different filters is updated, an unchanged one is left alone |
| Partition assignment | replica `i` of `n` owns partitions `p` with `p mod n == i` (`CORVUS_SCORE_REPLICA_INDEX`, else the StatefulSet ordinal; `CORVUS_SCORE_REPLICAS`, `CORVUS_CAPTURE_PARTITIONS`) |
| Session state | `SessionStart` gives the pseudonym, `SegmentStart` the game context. Because of deliver policy `new` a batch can arrive for a session the scorer never saw start: it is then looked up in the catalog (`sessions`, `segments`, written by ingest). If the catalog does not know it either (or ingest has not written it yet) the batch is skipped and counted (`corvus_score_unknown_session_skips_total`); the failed lookup is remembered for `CORVUS_SCORE_LOOKUP_RETRY_S` (5 s) |
| Featurizer | the same `corvus_ml.features` code as offline feature building (no training/serving skew); frames are mapped by `codec.event_row` (the `events` table mapping) plus the offline null conventions. State per session in a bounded LRU with idle eviction, freed on `SessionEnd`; the tail windows of an ended or evicted session are still scored. `tick_end` hint: sessions below protocol 768 are known not to send `CLIENT_TICK_END` (hint `false`), otherwise it is inferred |
| Models | loaded from the model registry (`corvus.models`) in the stages `shadow`, `canary`, `enforce`; every such model scores the windows of its `game_context` (several domains and several models per context are possible). Polled every `CORVUS_SCORE_REGISTRY_POLL_S` (30 s); ONNX and the `feature_spec.json` next to it are downloaded from S3, validated with `FeatureSpec.load` (a mismatch keeps only that model out) and swapped in atomically. The input layout (aggregates or token sequence) follows from the ONNX input rank. ONNX Runtime on CPU, sequential, inter-op 1, intra-op threads = CPU budget / concurrently running models |
| Inference | completed windows of all sessions are micro-batched (`CORVUS_SCORE_BATCH_MAX` 64, `CORVUS_SCORE_BATCH_MAX_WAIT_MS` 50) and run per model in a bounded thread pool. Scores are clipped to [0, 1]. Smoothing is an EMA (`CORVUS_SCORE_EMA_ALPHA`) per (pseudonym, segment, model) |
| Score subject | `corvus.score.v1.<game_context>` (characters outside `A-Za-z0-9_-` in the context are replaced by `_`); body = serialized `corvus.score.v1.Score` (`proto/corvus/score/v1/score.proto`) |
| Score stream | `CORVUS_SCORE`, subjects `corvus.score.v1.>`, `max_age` 7 d (`CORVUS_SCORE_STREAM_MAX_AGE_S`), created by the scorer if missing |
| Persistence | `scores_latest` (primary key pseudonym, game_context, domain, model_name; newer `updated_at` wins) and the append-only `score_history` (migration `0003_scores.sql`), written in batches off the consumer path. When the bounded write queue (`CORVUS_SCORE_WRITE_QUEUE`) is full, history rows are dropped and counted, the latest row is still upserted. `corvus-housekeeping` prunes the history after `CORVUS_SCORE_HISTORY_RETENTION_DAYS` (14) |
| Shutdown | on SIGTERM: stop fetching, score the pending windows, flush the writes, drain NATS. Featurizer state is not persisted; after a restart sessions continue from the next batch |

Scores of `shadow` models are persisted but never published. Consumers must treat `canary` scores as
informational; only `enforce` scores may drive automated actions in other systems, and Corvus itself
never sanctions players.

## 7. Compaction after erasure

`corvus-compact` is a one-shot process (Helm: CronJob, hourly) that makes erasure physical. It takes the
`erasure_requests` with `compaction_pending = true` that are older than `CORVUS_COMPACT_GRACE_S` (600 s, so frames still
buffered or cached by ingest at erasure time have landed), finds the live `events` and `raw` files that list sessions of
the erased pseudonyms in `lake_file_sessions`, and per file, under `pg_try_advisory_lock` (a file that another run holds
is skipped and the request stays pending):

1. downloads the file to local disk and rewrites it as zstd Parquet batch by batch (`CORVUS_COMPACT_BATCH_ROWS`, 65 536
   rows), dropping the rows of erased sessions; row order and schema are unchanged;
2. uploads the result to `_staging/<table>/<ulid>.parquet` and copies it to a new final key
   `lake/<table>/date=<day>/part-<ulid>.parquet`, like ingest does;
3. in one transaction inserts the new `lake_files` and `lake_file_sessions` rows (the old file's sessions minus the
   erased ones), sets the old file's `deleted_at`, `replaced_by` and `purge_pending`; a file with no rows left has no
   successor;
4. deletes the old object and clears `purge_pending`.

A file that lists an erased session without holding rows of it (typical for `raw`) is not rewritten; only the
`lake_file_sessions` entry is removed. When every file of the requests is done, the requests get
`compaction_pending = false` and `compacted_at`. The job exits non-zero if any file failed.

Crash safety: until step 3 commits the old file stays the live one (an object copied but never registered is an orphan
that housekeeping removes after one hour); after it only `purge_pending` work remains, which every run retries first.
Repeating a run, or a repeated erasure of the same pseudonym, changes nothing.

Snapshots are immutable and keep naming erased sessions and the file ids that compaction replaced. Jobs read a snapshot
through `GET /api/v1/snapshots/{id}/sessions?exclude_erased=true` (paged) and `GET /api/v1/snapshots/{id}/files`
(follows `replaced_by` to the live files) instead of trusting the manifest alone; see
[control-plane-api.md](control-plane-api.md).
