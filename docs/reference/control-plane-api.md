# Reference: control-plane API (v1)

HTTP API behind the management UI. It manages training pools, dataset snapshots, labels, players and erasure, the
blocklist, the model registry and training jobs. Corvus never sanctions players itself: the API only produces data
and models. The catalog it reads is described in [data-lake.md](data-lake.md); its own tables are created by
`services/migrations/0002_control_plane.sql`.

- Service: `corvus-api` (FastAPI, uvicorn), default port 8080.
- OpenAPI: `/openapi.json`, interactive docs at `/docs`.
- All business endpoints are under `/api/v1`. `/healthz`, `/readyz` and `/metrics` are unversioned and unauthenticated.
- Errors are RFC 9457 problem details (`application/problem+json`) with `type`, `title`, `status`, `detail`,
  `instance` and, where useful, extension members (`errors` on validation errors, `reasons` on a rejected model
  transition). Validation errors list the location of a problem but never echo the submitted value.
- Lists use keyset pagination: `?limit=` (server maximum applies) and `?cursor=`; the response is
  `{"items": [...], "next_cursor": "..." | null}`, newest first.

## Authentication and roles

Bearer tokens (`Authorization: Bearer <token>`), validated against an OIDC issuer:

| Check | Behaviour |
|---|---|
| Signature | Keys from `CORVUS_API_OIDC_JWKS_URL`, cached for `CORVUS_API_JWKS_CACHE_TTL_S` (600 s). An unknown `kid` triggers one refetch per `CORVUS_API_JWKS_MIN_REFRESH_S` (30 s). Cached keys keep working during an IdP outage. |
| Algorithms | Allowlist `RS256`, `ES256` only (`CORVUS_API_OIDC_ALGORITHMS`). `none` and HMAC algorithms are rejected, the key type must match the algorithm. |
| Claims | `iss` equals `CORVUS_API_OIDC_ISSUER`, `aud` contains `CORVUS_API_OIDC_AUDIENCE`, `exp` and `sub` are required, `nbf` is honoured, leeway `CORVUS_API_OIDC_LEEWAY_S` (30 s). |
| Roles | Read from the claim `CORVUS_API_ROLES_CLAIM` (default `roles`; dotted paths such as `realm_access.roles` work; a list or a space-separated string). `CORVUS_API_ROLE_MAPPING` (JSON) maps external names to Corvus roles; unknown values are ignored. |

Roles: `admin`, `data_scientist`, `staff`, `viewer`. Each endpoint declares the roles it needs; `admin` may call every
endpoint, the other roles are not hierarchical. A missing or invalid token is `401` (with `WWW-Authenticate`), a valid
token without the role is `403`.

**Development token.** Setting `CORVUS_API_DEV_TOKEN` accepts that static value as a bearer token with the roles in
`CORVUS_API_DEV_TOKEN_ROLES` (default `["admin"]`) and logs a warning at startup. It is off unless set. Never set it
outside local development. The service refuses to start if neither OIDC nor a dev token is configured.

**Proxy API tokens.** `GET /api/v1/plugin/blocklist` is a machine endpoint: it accepts only the tokens in
`CORVUS_API_PLUGIN_TOKENS` (comma separated) and nothing else; OIDC tokens and the dev token do not work there, and
proxy tokens open nothing else. With no tokens configured the endpoint rejects every request.

## Endpoints

`DS` = `data_scientist`. "read" = `viewer`, `staff`, `data_scientist` (and `admin`).

### Pools and snapshots

| Method and path | Role | Purpose |
|---|---|---|
| `POST /pools`, `GET /pools`, `GET/PUT/DELETE /pools/{id}` | DS | Pool definitions. Names are unique (`409`). Deleting a pool keeps its snapshots. |
| `POST /pools/{id}/preview` | DS | Counts for the current filter. |
| `POST /pools/{id}/snapshots` | DS | Materialise an immutable snapshot. |
| `GET /pools/{id}/snapshots`, `GET /snapshots`, `GET /snapshots/{id}` | DS | List and detail (the detail view includes the manifest). |

Pool filter (`filter` in the body; all parts optional, combined with AND):

| Field | Meaning |
|---|---|
| `game_contexts`, `backend_servers` | Session has a segment in one of these. |
| `client_brand.include` / `.exclude` | Session has a segment with (or without) one of these brands. Segment conditions are evaluated on the same segment. |
| `client_protocol.min` / `.max` | Client protocol range of the session. |
| `date_range.start` / `.end` | Inclusive UTC dates of the session start. |
| `label.value` (`legit`, `cheat`, `labeled`, `unlabeled`), `label.min_confidence` | Labels that apply to the session (directly or via its pseudonym) and are valid at its start. |
| `min_session_duration_s` | Ended sessions at least this long. |
| `max_drop_rate` | Dropped frames (queue, privacy, transport) divided by dropped plus estimated captured frames. The captured frames are estimated from `lake_files` row counts split evenly over the sessions of each file. |
| `exclude_orphans` (default `true`) | Skip sessions whose `SessionStart` never arrived. |
| `retention_days` | Stored with the pool for retention jobs; does not change the selection. |

Sessions of erased players are never selected. The filter is translated to SQL with named parameters only; values
never appear in the SQL text.

**Preview** returns `sessions`, `segments` (matching the segment filters), `distinct_players`, `events_files`,
`total_frames_estimate` (each `events` file's `row_count` times the share of its sessions that are selected) and
`label_distribution` (`legit`, `cheat`, `unlabeled`; per session the applicable label with the highest confidence,
then the newest).

**Snapshot** manifest (canonical JSON, keys sorted, no whitespace):

```json
{"version": 1, "pool": {"id": "...", "name": "..."}, "filter": {...},
 "sessions": ["<hex session id, sorted>"], "files": {"events": [{"id": 12, "key": "lake/events/..."}]},
 "created_at": "2026-10-15T12:00:00+00:00"}
```

`sha256` is the SHA-256 of that canonical form, so the same inputs and timestamp give the same hash. Selection and
file list are read in one repeatable-read transaction. Snapshots are append-only: there is no update or delete
endpoint and a database trigger rejects `UPDATE` and `DELETE`.

### Labels

| Method and path | Role | Purpose |
|---|---|---|
| `POST /labels` | `staff` | One label. |
| `GET /labels` | read | List; filter with `subject_type` (`pseudonym`, `session`) and `subject` (hex). |
| `POST /labels/import` | DS | Bulk import, body `text/csv`, all rows or none. |

A label has `subject_type` and `subject` (pseudonym: 64 hex characters, session id: 32), `label` (`legit`, `cheat`),
`source` (`honeypot`, `staff_verdict`, `ban_import`, `weak_rule`), `confidence` 0 to 1, optional timezone-aware
`valid_from` and `valid_to` (`valid_from` before `valid_to`), `note` (at most 2000 characters; keep personal data out)
and `created_by`, which is always the token subject. CSV columns: `subject_type,subject,label,source,confidence`
(required) and `valid_from,valid_to,note` (optional). An invalid file is answered with `422` and the failing line
numbers in `errors`; limits `CORVUS_API_LABEL_IMPORT_MAX_ROWS` (50 000) and `..._MAX_BYTES` (8 MiB, `413`).

### Players, erasure and blocklist (admin)

| Method and path | Purpose |
|---|---|
| `POST /players/lookup` | `{"uuid": ...}` to the pseudonym and its catalog state. `POST` keeps the UUID out of URLs and access logs. |
| `POST /players/erasure` | `{"uuid": ...}` or `{"pseudonym": ...}` (exactly one). Returns `202` with the request. |
| `GET /players/erasure/{id}` | The audit row of a request. |
| `GET /blocklist`, `GET/PUT/DELETE /blocklist/{pseudonym}` | Blocklist and allowlist (`kind`: `block`, `allow`). |
| `GET /plugin/blocklist` | Proxy feed (proxy API token): `{"version", "blocked": [...], "allowed": [...]}`, `Cache-Control: no-store`. `version` is a hash of both lists. |

The UUID to pseudonym step is `HMAC-SHA-256(key, 16 UUID bytes)` with `CORVUS_PSEUDONYM_KEY` (base64) and
`CORVUS_PSEUDONYM_KEY_ID`, identical to the plugin; see [pseudonym-test-vector.md](pseudonym-test-vector.md). Without
the key, UUID endpoints answer `503`; erasure by pseudonym still works. The API never stores or logs a clear UUID; it
logs only the first 12 hex characters of the pseudonym.

**Erasure semantics.** In one transaction: (1) delete the `player_keys` row, which crypto-shreds the player's `raw`
payloads; (2) set `players.erased_at` (ingest stops storing data for that pseudonym); (3) put the pseudonym on the
blocklist with `source = erasure`; (4) insert an `erasure_requests` row (who, when, pseudonym, `status = completed`,
`player_known`, `key_deleted`, `compaction_pending = true`). If any step fails nothing is changed. A pseudonym that was
never captured is still blocklisted and audited. Erasure can be repeated. `events` files are not rewritten here:
`compaction_pending` stays `true` until the later compaction job clears it. Blocklist entries created by an erasure
are permanent: changing or deleting them is `409`. Snapshots keep the (random) session ids they were built from; a
training job must skip sessions of erased players.

### Models

| Method and path | Role | Purpose |
|---|---|---|
| `POST /models` | DS | Register (`stage = registered`). `name` plus `version` is unique; the snapshot must exist. |
| `GET /models`, `GET /models/{id}`, `GET /models/{id}/transitions` | read | List (filter `stage`), detail, audit trail. |
| `POST /models/{id}/stage` | DS | `{"stage": ..., "reason": ...}`. |

Fields: `name`, `version`, `candidate_type`, `game_context`, `domain`, `snapshot_id`, `metrics` (JSON), `onnx_key`.
Stages: `registered`, `shadow`, `canary`, `enforce`, `archived`.

Allowed transitions: `registered` to `shadow` to `canary` to `enforce`, and any stage to `archived`; `archived` is
final. Anything else is `409`.

**Quality gate for `enforce`.** The candidate's metrics must satisfy:

1. `p99_latency_ms` is present and below `CORVUS_API_GATE_MAX_P99_LATENCY_MS` (default 10, strictly).
2. If a model is currently in `enforce` for the same snapshot and game context, `recall_at_fpr_0_1pct` is present and
   at least that model's. If the incumbent has no such metric the gate fails closed. Without an incumbent only the
   latency applies.

A failing gate (or a not-allowed transition) answers `409` with every failed condition in `reasons`; nothing is
changed or audited. A passing promotion archives the replaced incumbent in the same transaction. Every applied
transition (including the automatic archive) is stored in `model_transitions` with actor, reason, time and, for
`enforce`, the gate result.

### Jobs (DS)

`POST /jobs` (`202`), `GET /jobs` (filter `status`), `GET /jobs/{id}`. A job has `type` (`build_features`, `evaluate`,
`train`), a `snapshot_id` that must exist, and free-form `params` (at most 16 KiB; no secrets). Status values:
`queued`, `running`, `succeeded`, `failed`. `GET /jobs/{id}` refreshes a non-final job from the runner; runner errors
leave the stored status unchanged.

The runner is selected with `CORVUS_API_JOB_RUNNER`:

| Runner | Behaviour |
|---|---|
| `local` | Starts `CORVUS_API_JOB_LOCAL_COMMAND` (JSON list, default `["corvus-ml"]`) plus the job type as a subprocess without a shell. The child does not inherit `CORVUS_API_*`, `CORVUS_PSEUDONYM_KEY*` or `CORVUS_KEK`. Process handles are in memory: after an API restart a running job is reported `failed` ("process handle lost"). |
| `kubernetes` | Creates a `batch/v1` Job through the official Python client (in-cluster config, else kubeconfig) in `CORVUS_API_JOB_K8S_NAMESPACE`: image, command, `CPU`/`memory` requests and limits (`CORVUS_API_JOB_K8S_*`), labels `app.kubernetes.io/name=corvus-job` and `corvus.onelitefeather.net/job-id`/`job-type`, `ttlSecondsAfterFinished`, `activeDeadlineSeconds`, `backoffLimit` 0, `restartPolicy: Never`, non-root, no privilege escalation, all capabilities dropped. `CORVUS_API_JOB_K8S_ENV_SECRET` names a Secret exposed as environment. Status comes from the Job's `Complete` and `Failed` conditions. The API's service account needs `create` and `get` on `jobs` in that namespace. |

Contract with the job process, identical for both runners: arguments are the command followed by the job type; the
environment carries `CORVUS_JOB_ID`, `CORVUS_JOB_TYPE`, `CORVUS_JOB_SNAPSHOT_ID` and `CORVUS_JOB_PARAMS` (JSON).

### Scores (staff)

Read-only view of what `corvus-score` persisted (see [data-lake.md](data-lake.md) section 6). Role `staff` (and
`admin`). The pseudonym is the 64-hex form (`POST /players/lookup` resolves a UUID); a malformed one is `422`.
Scores of `shadow` models are visible here although they are never published. A player without scores yields an
empty list, not `404`. Scores are informational, Corvus never sanctions players.

| Method and path | Purpose |
|---|---|
| `GET /scores/players/{pseudonym}` | `{"items": [...]}`: the latest score per (`game_context`, `domain`, `model_name`) with `model_version`, `stage`, `score`, `smoothed`, `windows`, `updated_at`. |
| `GET /scores/players/{pseudonym}/history?game_context=&limit=` | `{"items": [...]}` newest first: the same fields per window plus `session_id`, `segment`, `window_start_offset_nanos`, `window_end_offset_nanos`, `scored_at`. `game_context` is optional, `limit` defaults to 100 (1 to 1000). |

The history keeps `CORVUS_SCORE_HISTORY_RETENTION_DAYS` (default 14) days; `corvus-housekeeping` prunes it.

### Operations

`GET /healthz` (liveness, no dependencies), `GET /readyz` (`503` when PostgreSQL does not answer within 3 s),
`GET /metrics` (Prometheus: `corvus_api_requests_total`, `corvus_api_request_seconds` by route template and method,
`corvus_api_erasures_total`, `corvus_api_model_transitions_total`, `corvus_api_jobs_total`). `/metrics` is
unauthenticated; keep it on the cluster network.

## Configuration summary

| Variable | Default | Notes |
|---|---|---|
| `CORVUS_PG_DSN` | `postgresql://corvus@localhost:5432/corvus` | Shared with ingest; password via `PGPASSWORD`. |
| `CORVUS_API_PORT`, `CORVUS_API_HOST` | `8080`, `0.0.0.0` | |
| `CORVUS_API_PG_POOL_MAX` | `8` | |
| `CORVUS_API_OIDC_ISSUER`, `..._AUDIENCE`, `..._JWKS_URL` | unset | All three are needed for OIDC. |
| `CORVUS_API_DEV_TOKEN`, `CORVUS_API_DEV_TOKEN_ROLES` | unset, `["admin"]` | Development only. |
| `CORVUS_API_PLUGIN_TOKENS` | empty | Comma separated proxy tokens. |
| `CORVUS_PSEUDONYM_KEY`, `CORVUS_PSEUDONYM_KEY_ID` | unset, `k1` | Same as the plugin. |
| `CORVUS_API_DEFAULT_PAGE_SIZE`, `CORVUS_API_MAX_PAGE_SIZE` | `50`, `200` | |

## Data model

`0002_control_plane.sql` adds `pools`, `dataset_snapshots` (with the immutability trigger), `labels`, `blocklist`,
`erasure_requests`, `models`, `model_transitions` and `jobs` to the `corvus` schema; `0003_scores.sql` adds
`scores_latest` and `score_history`. Run `corvus-migrate` before starting the API.
