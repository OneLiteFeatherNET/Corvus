# Reference: capture transport, data lake and catalog (v1)

This is the contract between the capture plugin, the ingest service and the ML side.
Changing it requires a schema version bump or an append-only change.

## 1. Transport (NATS JetStream)

| Item | Value |
|---|---|
| Stream | `CORVUS_CAPTURE` |
| Subjects | `corvus.capture.v1.>` |
| Publish subject | `corvus.capture.v1.<proxy_id>` |
| Message body | one serialized `corvus.capture.v1.Envelope` (no length prefix) |
| Header `Nats-Msg-Id` | `<session-id-hex>-<n>`, `n` = per-session envelope counter starting at 0 (JetStream de-duplication) |
| Header `Corvus-Schema` | `capture.v1` |
| Max message size | 1 MiB; the plugin splits `CaptureBatch`es so that each serialized envelope stays below 512 KiB |
| Stream limits | file storage, `max_age` 24 h, `max_bytes` configurable (default 10 GiB), discard old, duplicate window 2 min |
| Consumer | durable pull consumer `corvus-ingest`, explicit ack, `ack_wait` 120 s, `max_ack_pending` 1000; all ingest replicas share it |

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
| `action` | uint8 (nullable) | interact action / entity_motion kind |
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
  pseudonym to the blocklist; a compaction job later rewrites `events` files without the player's sessions.

## 4. Catalog (PostgreSQL, schema `corvus`)

| Table | Key | Content |
|---|---|---|
| `players` | `pseudonym bytea` | `key_id`, `first_seen`, `last_seen`, `erased_at` |
| `player_keys` | `pseudonym bytea` → players | `dek_wrapped bytea`, `kek_id text`, `created_at` |
| `sessions` | `session_id bytea` | `pseudonym`, `proxy_id`, `client_protocol`, `observed_protocol`, `plugin_version`, `started_at`, `ended_at`, counters (`dropped_queue_full`, `dropped_privacy`, `listener_errors`, `dropped_transport`, summed over batches) |
| `segments` | (`session_id`, `segment`) | `backend_server`, `game_context`, `client_brand`, `started_at` |
| `lake_files` | `id bigserial`, unique `s3_key` | `table_name`, `date`, `row_count`, `size_bytes`, `schema_version`, `created_at`, `deleted_at` |
| `lake_file_sessions` | (`file_id`, `session_id`) | which sessions a file contains (pool selection, erasure, compaction) |

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
