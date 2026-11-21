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

### Table `raw` (Parquet: short retention, crypto-shredded; Iceberg: section 2a)

| Column | Arrow type | Notes |
|---|---|---|
| `session_id` | fixed_size_binary(16) | |
| `segment` | uint32 | |
| `seq` | uint64 | |
| `payload_original_size` | uint32 | |
| `nonce` | fixed_size_binary(12) | |
| `ciphertext` | binary | AES-256-GCM of the payload with the player's DEK, AAD = `session_id || seq (8 bytes big-endian)` |

Only frames that carry a payload get a `raw` row.

Retention is per pool (NFR-018): `pools.raw_retention_days` (default `CORVUS_RAW_RETENTION_DAYS`, 21; at most
`CORVUS_RAW_RETENTION_MAX_DAYS`, 365). `corvus-housekeeping` deletes a `raw` file only when its date is older than the
**longest** retention of any pool whose snapshot lists a session of the file (found through `lake_file_sessions` and
the snapshot manifests). Sessions in no snapshot, and pools that were deleted, count with the default. So a pool can
shorten the life of data nobody else uses, and keep a file longer than the default.

## 2a. Iceberg tables (target format, opt-in)

Status: the target format of the lake. `CORVUS_LAKE_FORMAT=parquet` stays the default and keeps everything above;
`iceberg` is chosen per deployment (Helm `lake.format`). Layout version 2 (`ICEBERG_LAKE_VERSION` in
`services/src/corvus_services/iceberg_sink.py`). Decision of 2026-11-18: in this layout raw packets are stored in
plaintext and kept permanently. Crypto-shredding does not apply to it; sections 2 (`raw`) and 3 describe the Parquet
layout only.

| Item | Value |
|---|---|
| Catalog | Apache Polaris REST catalog, OAuth2 client credentials (Entra ID), warehouse `corvus` (`CORVUS_LAKE_WAREHOUSE`), namespace `capture` (`CORVUS_LAKE_NAMESPACE`, must exist) |
| Ingest grants | `NAMESPACE_LIST`, `TABLE_LIST`, `TABLE_CREATE`, `TABLE_WRITE_DATA` on `capture` |
| Data files | Ceph RGW bucket `polaris-corvus`, path-style, static S3 keys (`CORVUS_LAKE_S3_*`). No STS: the client sends `X-Iceberg-Access-Delegation` empty |
| Tables | `capture.events`, `capture.raw`, `capture.sessions`, `capture.segments`, `capture.session_ends` |

Settings: `CORVUS_LAKE_FORMAT` (`parquet` | `iceberg`); with `iceberg`, these are required and checked at start-up:
`CORVUS_LAKE_CATALOG_URI`, `CORVUS_LAKE_OAUTH_TOKEN_URL`, `CORVUS_LAKE_OAUTH_SCOPE`, `CORVUS_LAKE_CLIENT_ID`,
`CORVUS_LAKE_CLIENT_SECRET` (secret), `CORVUS_LAKE_S3_ENDPOINT_URL`, `CORVUS_LAKE_S3_ACCESS_KEY`,
`CORVUS_LAKE_S3_SECRET_KEY` (secret). Optional: `CORVUS_LAKE_WAREHOUSE` (`corvus`), `CORVUS_LAKE_NAMESPACE` (`capture`),
`CORVUS_LAKE_S3_REGION` (`us-east-1`). No further setting exists for the raw layout: it is the layout of this format.
`CORVUS_LAKE_SNAPSHOT_RETENTION_DAYS` (`7`, Helm `lake.snapshotRetentionDays`) is read by housekeeping only, and
`corvus-compact` and `corvus-housekeeping` read the same settings with `iceberg`.

### Privacy and retention

Decision of 2026-11-18 (maintainer): the lake keeps the complete raw packet stream, "wirklich alles".

* **`capture.raw` is personal data in plaintext, including chat, commands and names.** Each row holds the packet
  payload exactly as the plugin sent it. The plugin records chat, commands, message-signing frames and player lists
  (`capture.text_packets = "full"`, the default) and keeps names, UUIDs and free text inside payloads
  (`capture.redact_names = false`, the default). Ingest does not filter anything further. What the plugin sends is
  described in `docs/pilot.md` (text packets, names and raw payload policy). Cookie responses (credential material)
  stay stripped in every setting, and the handshake, status and login frames are never recorded.
  An operator can switch back to `text_packets = "drop"` and `redact_names = true` in the plugin config; frames
  captured before that switch keep their content.
* **Names of other players are in the rows of the observing player.** Clientbound player list packets carry names and
  UUIDs of players other than the session's player, and chat carries the sender's name. Erasing a player by
  `player_key` removes the rows of that player's own sessions only. It does not remove that player's name from the
  sessions of other players. Erasure has to cover both cases (see "Not covered yet").
* **Access** is only through the Polaris grants on the `capture` namespace. Data files live in the `polaris-corvus`
  bucket and are reached through the catalog only; nothing copies `raw` out of the catalog.
* **Retention is permanent.** No housekeeping job deletes Iceberg rows (housekeeping expires snapshots only). The
  contents are reviewed once a year.
* **Erasure** of a player removes the Iceberg rows by `player_key` (and by session for null-key orphans and for
  `segments`) in all five tables, then expires the older snapshots and removes their files; see *Erasure in Iceberg* in section 2a. A completed
  erasure request in Iceberg mode means exactly that: the completion waits for the Iceberg step. The DEK deletion alone
  does not count. It does not yet remove an erased player's name from the payloads of other players' rows (see
  "Privacy and retention" above).
* **Player attribution** is the pseudonym (section 3's HMAC, 32 bytes, `player_key`). The Mojang UUID and the name are
  now also present in plaintext inside payloads (see above), so the pseudonym is no longer the only identifier in
  the lake. Frames of orphan sessions (no `SessionStart` seen) have a null `player_key` and a null `protocol_version`.
  They cannot be attributed to a player, so they cannot be erased by player either.

### Schema

Iceberg has no unsigned integers: `uint8`, `uint32` and `uint64` become `int` or `long` (`int` for `uint8`, `long`
for `uint32` and `uint64`). Values are unchanged. Every table has a `date` column (`date`, not null): the UTC date of
the session start (`SessionInfo.partition_date`), the partition source.

`events`: the columns of section 2, plus `player_key` (`fixed(32)`, nullable, the pseudonym of the session's player).

`raw`: the columns below. Only frames that carry a payload get a row.

| Column | Iceberg type | Notes |
|---|---|---|
| `session_id` | `fixed(16)` | |
| `segment` | `long` | |
| `seq` | `long` | dedup key together with `session_id` |
| `offset_nanos` | `long` | since session start, as in `events` |
| `direction` | `int` | proto `Direction` value |
| `state` | `int` | proto `ConnectionState` value |
| `packet_type` | `long` | Corvus vocabulary id |
| `wire_packet_id` | `int` | |
| `protocol_version` | `int` (nullable) | the client's protocol version from the session's `SessionStart`, repeated in every row; null for orphan sessions |
| `player_key` | `fixed(32)` (nullable) | the pseudonym of the session's player; null for orphan sessions |
| `payload_original_size` | `long` | size before truncation; larger than the payload length means it was truncated |
| `payload` | `binary` | the payload bytes as received, plaintext |
| `date` | `date` | partition column |

Session metadata (Iceberg only, written next to the packets; Parquet mode has none). All three tables are append-only:
no row is ever updated, so no snapshot needs an update commit. Each carries `date` (the UTC date of the session start,
as in `raw`), so a join of `raw` with `segments` on (`session_id`, `segment`) prunes the same partitions.

`sessions`: one row per `SessionStart`, the start fields of the session. It has no end data.

| Column | Iceberg type | Notes |
|---|---|---|
| `session_id` | `fixed(16)` | |
| `player_key` | `fixed(32)` | the pseudonym of the session's player (same semantics as in `raw`) |
| `pseudonym_key_id` | `string` | key version of the pseudonym |
| `client_protocol_version` | `int` | protocol version spoken by the client |
| `observed_protocol_version` | `int` | protocol version of the frames as observed by the plugin |
| `proxy_id` | `string` | |
| `plugin_version` | `string` | |
| `started_at` | `timestamptz` | |
| `text_packets_captured` | `boolean` | capture policy of the session |
| `names_kept` | `boolean` | capture policy of the session |
| `date` | `date` | partition column |

`session_ends`: one row per `SessionEnd`. Its counters are the ones of that message (the part since the last batch);
the batch counters are not in the lake, only in the Postgres catalog. A session's end is found by joining on
`session_id`. The session row stays unchanged. A session without an end (crash, lost connection) has no row here.

| Column | Iceberg type | Notes |
|---|---|---|
| `session_id` | `fixed(16)` | |
| `player_key` | `fixed(32)` (nullable) | null for orphan sessions, as in `raw` |
| `ended_at` | `timestamptz` | |
| `dropped_queue_full`, `dropped_privacy`, `listener_errors`, `dropped_transport` | `long` | the `SessionEnd` counters |
| `date` | `date` | partition column |

`segments`: one row per `SegmentStart`. The `player_key` is not repeated: a segment belongs to a session, and the
session's `player_key` comes from `sessions`.

| Column | Iceberg type | Notes |
|---|---|---|
| `session_id` | `fixed(16)` | |
| `segment` | `long` | |
| `backend_server` | `string` | |
| `game_context` | `string` | tag from the backend-server mapping, e.g. `pvp`, `survival`, `lobby` |
| `client_brand` | `string` | may be empty |
| `started_at` | `timestamptz` | |
| `cloudnet_task` | `string` | CloudNet task of the backend service, e.g. `Lobby`; empty when CloudNet does not know the server |
| `cloudnet_groups` | `list<string>` (list required, elements optional) | CloudNet groups of the backend service; empty list when unknown |
| `cloudnet_node` | `string` | CloudNet node that runs the backend service; empty when unknown |
| `cloudnet_environment` | `string` | CloudNet `ServiceEnvironmentType`, e.g. `MINECRAFT_SERVER`; empty when unknown |
| `stage` | `string` | `production` or `staging`; empty when CloudNet does not know the server |
| `date` | `date` | partition column |

The `cloudnet_*` and `stage` columns are empty (empty string, empty list) for segments reported by plugins that
predate them, as `client_brand` is for an empty brand. They are not null.

Table properties: `format-version` 2, `write.parquet.compression-codec` zstd, `corvus.lake-schema-version` 2.
Partition spec: identity(`date`). Sort order: `session_id`, then `seq` for `events` and `raw`, `segment` for `segments`
(ascending, nulls first).

A redelivered `SessionStart`, `SegmentStart` or `SessionEnd` appends its row again, so readers de-duplicate on `session_id`
(and `segment`), as they do for `raw` on (`session_id`, `seq`).

Changes to the columns: add an Iceberg schema evolution (append-only), bump `corvus.lake-schema-version` and
`ICEBERG_LAKE_VERSION` together. The writer refuses at start-up a table whose lake schema version is not 2, and a table
whose columns or partition differ from this layout. Version 1 tables (encrypted `raw`, no `player_key`) were never in
production and are not migrated: drop the table and let ingest recreate it.

### Writes

* Start of ingest: the tables are created if missing (`CREATE` races between replicas are resolved by loading the
  table another replica created).
* Flush: one append per (table, date) buffer, with the same flush limits as section 5. Rows are sorted by
  (`session_id`, `seq`) in the append.
* Acks are unchanged: a message is acked only after the append committed for all its buffers; a failed append naks
  the message (redelivery), as in section 5. The session metadata rows go through the same buffers: a `SessionStart`
  is acked after its `sessions` row is committed, a `SegmentStart` after its `segments` row, a `SessionEnd` after its
  `session_ends` row. Postgres is written as before, first.
* Commit conflicts (another writer committed first): the table is reloaded and the append retried, at most 5 attempts
  with jittered backoff (base 0.2 s, cap 5 s). A failed attempt leaves its data files unreferenced (orphans for
  Iceberg's `remove_orphan_files`).
* No `lake_files` or `lake_file_sessions` rows in Iceberg mode. Session membership is read from the tables (filter on
  `session_id`, prune by `date`). Everything else in the catalog (players, keys, sessions, segments, counters, batch
  log) is unchanged.
* Raw payloads are written as plaintext. In Iceberg mode ingest does not read or unwrap a player's DEK. Session start
  still stores a `player_keys` row, which nothing reads in this mode (see the open points of the change that introduced
  it).
* Erased players: frames of a session whose player is erased are acknowledged and not written (as in Parquet mode).
  The check uses the session cache (`CORVUS_SESSION_CACHE_TTL_S`, 60 s), so frames of a player erased less than that
  time ago can still be written.

### Erasure in Iceberg

`corvus-compact` (section 7) handles the Iceberg tables when `CORVUS_LAKE_FORMAT=iceberg`, in the same run as the Parquet
files. Per erasure request, after the grace period:

1. delete the player's rows from all five tables: first every row whose `player_key` is the pseudonym (`events`, `raw`,
   `sessions`, `session_ends`; one commit per table), then the rows with a null `player_key` of the player's sessions
   (`corvus.sessions`, orphan frames of `events`, `raw` and `session_ends`), and every `segments` row of those sessions
   (`segments` has no `player_key`). The session lists are `IN` lists of 1000 session ids. pyiceberg 0.10 deletes copy-on-write (`Table.delete`): files with only erased rows are
   dropped, mixed files are rewritten without them. Every delete commit is tagged in its snapshot summary with the
   request id (`corvus.erasure-request`);
2. expire, per table, every snapshot older than the newest tagged snapshot (the cut). The cut and everything newer stay.
   Expiry runs right after the delete, in the same run;
3. if a branch or tag points at one of the snapshots to expire, nothing is expired for that table. The request stays
   pending with `lake_blocked_reason` set (the field of the erasure request in the API, `GET /api/v1/players/erasure/{id}`
   and the list). The rows are gone from the current view already. The next run tries again, so the request
   completes when the hold is released;
4. unless blocked, the files that only the expired snapshots named are deleted: manifest lists, manifests, data and
   delete files of the expired snapshots (live and deleted entries) that no retained snapshot names. Nothing else is
   touched. A file that no snapshot names (an in-flight write of another job, an orphan of a failed commit) is never
   deleted by erasure, so this step needs no age margin. If this deletion fails, the request stays pending and the files
   are left to the opt-in maintenance below.

Commit conflicts (an ingest append in between) retry the step with a fresh table load, at most 5 attempts with jittered
backoff, as in the sink. Other errors leave the request pending; the next run starts again (deletes are idempotent).

A request completes only when its Iceberg step finished without error and was not blocked. Dataset snapshots whose
manifest lists one of the sessions are marked `needs_rebuild` (`corvus.snapshot_state`, reason `erasure`), like the
snapshots with erased raw artefacts.

### Retention in Iceberg

* `raw` in Iceberg is kept, there is no time-based deletion of its rows: `CORVUS_RAW_RETENTION_DAYS` and pool retention
  do not apply to it. The session metadata tables are kept the same way. Only an erasure removes Iceberg rows.
* **Routine maintenance is owned by the platform.** The Airflow DAG `iceberg_maintenance` (Spark: `rewrite_data_files`,
  `expire_snapshots`, `remove_orphan_files`) runs nightly on `capture.events` and `capture.raw`. The table list of the
  DAG must also name `capture.sessions`, `capture.segments` and `capture.session_ends` (platform side, not in this
  repository). Corvus does not run a
  second orphan remover by default. Helm `lake.maintenance.enabled` (`CORVUS_LAKE_MAINTENANCE_ENABLED`, default `false`)
  switches on an opt-in variant in `corvus-housekeeping`: it expires snapshots older than
  `CORVUS_LAKE_SNAPSHOT_RETENTION_DAYS` (7) and removes orphan files older than 24 h, so that a long rewrite of the DAG
  is never cut. A snapshot is expired only when it is older than every branch and tag head. Expiry removes no rows.
  Enable it only where the DAG does not run.
* Physical removal of erased files: erasure deletes them in the same run. If that step failed, the files stay until the
  opt-in maintenance (24 h margin) or the DAG's orphan removal removes them.
* The Parquet `raw` files that exist from before the switch (`lake_files`) keep their retention until they are expired.

### One-off import of the Parquet lake

`corvus-import-parquet` copies the live Parquet files of the lake (`corvus.lake_files`, `deleted_at IS NULL`) into the
Iceberg tables once, so that captures written before the switch are in the Iceberg tables too. It is run by hand, per
UTC date range, and it is idempotent (operator steps below).

* `events`: the rows as stored, with the player key of their session.
* `raw`: the payloads are decrypted with the player's DEK and joined with the events of the same date on
  `(session_id, seq)` for offset, direction, state, packet type and wire packet id. The payload is stored in plaintext,
  like every Iceberg `raw` row. `protocol_version` is the client protocol of the session.
* `session_ends`: the sessions that ended, with their counters, from PostgreSQL.
* Not imported: `sessions` and `segments`. Their non-nullable columns `text_packets_captured`, `names_kept`, the CloudNet
  fields and `stage` are not stored in PostgreSQL. Dashboards that join to `sessions` or `segments` do not see the
  imported captures; the `events` and `raw` rows of those sessions are there.
* Never imported, each counted in the report: rows of an erased player, rows of a player without a DEK (including orphan
  sessions), rows of sessions unknown to the catalog. A raw row without its event, a payload that does not decrypt and a
  repeated `(session_id, seq)` of the lake's at-least-once duplicates are skipped too (a repeated event is copied once).
* Idempotency: each commit is tagged in its snapshot summary with `corvus.parquet-import-source` (the S3 key of the
  Parquet file, or `postgres:session_ends:<date>`). A rerun skips every source that is still in the snapshot history.
  Expired snapshots no longer carry the tag, so a rerun after snapshot expiry would import the file again. Finish the
  import within the snapshot retention, and use the dry run to check a rerun.

#### Running it once (operator)

1. Run a Job from the services image (the one of the compact CronJob) with the command `corvus-import-parquet`
   and the environment of the compact CronJob. The Job also needs `CORVUS_KEK` and `CORVUS_KEK_ID` from the same
   secret the ingest Deployment uses: the compact template does not carry the KEK. Set `restartPolicy: Never` and
   `backoffLimit: 0`; a failed run is resumed by starting a new Job, not by Kubernetes retrying it.
2. First run with `--dry-run`. It reads the lake and the catalog, creates nothing and prints the counts per table and
   date. Check the skipped counts and the rows to import against the expectation.
3. Run it without `--dry-run`. Optional `--date-from YYYY-MM-DD` and `--date-to YYYY-MM-DD` (inclusive, UTC) split the
   work by day. The events of one UTC date are read into memory at once, so size the Job's memory for the largest date.
4. Compare the row counts of the Iceberg tables with the report (`session_ends`, `events`, `raw` of the imported dates).

### Not covered yet

Each is a separate step: snapshot pins by Iceberg snapshot id, reading Iceberg `raw` in the ML jobs (`corvus-ml` reads
the Parquet `raw` layout only), importing `sessions` and `segments` (needs the four columns above in PostgreSQL), a
re-check of an erasure after its request completed (rows that land after the grace period are not seen again), and
removal of an erased player's name from other players' payloads (see "Privacy and retention").

## 3. Keys and erasure

- Every pseudonymised player gets a random 256-bit data encryption key (DEK) on first sight.
- The DEK is stored wrapped (AES-256-GCM) with a key encryption key (KEK) from `CORVUS_KEK` (base64, 32 bytes),
  identified by `CORVUS_KEK_ID`. The KEK never touches the database or the lake.
- Erasure of a player: delete the `player_keys` row (crypto-shredding of `raw`, Parquet layout only; Iceberg rows are
  plaintext and not covered, see 2a), mark `players.erased_at`, add the
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
3. Flush (Parquet format, section 2a for Iceberg): write Parquet to `_staging/`, copy to the final key, insert `lake_files` +
   `lake_file_sessions` in one transaction, then ack all messages whose frames are in the file, then delete the staging object.
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

Iceberg: with `CORVUS_LAKE_FORMAT=iceberg` the same run also erases the Iceberg rows and expires their snapshots; a
request completes only when that succeeded and is not blocked, see [Erasure in Iceberg](#erasure-in-iceberg).

Crash safety: until step 3 commits the old file stays the live one (an object copied but never registered is an orphan
that housekeeping removes after one hour); after it only `purge_pending` work remains, which every run retries first.
Repeating a run, or a repeated erasure of the same pseudonym, changes nothing.

Open points of the Iceberg step: the column statistics of the erased files stay in the manifest of the delete snapshot
(the cut) until the table's next commit (the rows do not). A new erasure request after the grace period is not verified
again once its request completed. Blocked requests are reported in the log and in the API only, there is no metric.

Snapshots are immutable and keep naming erased sessions and the file ids that compaction replaced. Jobs read a snapshot
through `GET /api/v1/snapshots/{id}/sessions?exclude_erased=true` (paged) and `GET /api/v1/snapshots/{id}/files`
(follows `replaced_by` to the live files; `raw` lists the live raw files, so a job does not scan partitions) instead of
trusting the manifest alone; see [control-plane-api.md](control-plane-api.md).

### Derived artefacts

Tokenised raw-byte features (`features/<snapshot_id>/raw/`, the packet table) contain payload content: they are personal
data derived from `raw`. A job registers every prefix it writes in `corvus.derived_artifacts` (`snapshot_id`, `job_id`,
`kind`, `s3_prefix`, `contains_raw`, `created_at`, `deleted_at`, `deleted_reason`) with
`POST /api/v1/snapshots/{id}/artifacts` (see [ml-jobs.md](ml-jobs.md)). Only registered prefixes are reached by erasure
and retention; the API accepts only prefixes below `features/<snapshot_id>/` and `tokenizers/<snapshot_id>/`, and the
deleting side refuses anything else.

- **Erasure.** Before it rewrites files, `corvus-compact` finds the live `contains_raw` artefacts of the snapshots that
  list a session of an erased player and deletes their whole prefix (not a rewrite: the packet table is a derived,
  window-agnostic table that is cheaper to rebuild than to rewrite, and a rebuild skips erased players because their DEK
  is gone). The artefact row gets `deleted_at` / `deleted_reason = 'erasure'` and the snapshot is marked
  `needs_rebuild` in `corvus.snapshot_state` (a table of its own, because `dataset_snapshots` is immutable;
  `GET /snapshots/{id}` shows `needs_rebuild`). Artefacts of jobs that started after the erasure request are kept: those
  jobs already excluded the player. The requests complete only after this step succeeded; a failed delete is retried.
- **Rebuild.** Registering a `contains_raw` artefact from a job that started after the erasure clears `needs_rebuild`;
  a job that started before it is refused with `409` (its output may contain the erased player, delete its prefix).
- **Retention.** `corvus-housekeeping` deletes a `contains_raw` artefact once its snapshot's pool retention has passed
  since the start of the newest session of the snapshot, so derived data never outlives the raw data it comes from.
  Expiry does not set `needs_rebuild`: the raw source is gone, a rebuild would fail.
- Prefixes nobody registered (a job that crashed before registering) are not tracked. Register right after writing,
  and before the long-running parts of the job where possible.
