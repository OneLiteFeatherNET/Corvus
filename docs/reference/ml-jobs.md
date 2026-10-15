# Reference: ML jobs (`corvus-job`)

The feature, evaluation and training pipelines run as jobs started by the control plane
([control-plane-api.md](control-plane-api.md), section Jobs). The image `corvus-ml` (`ml/Dockerfile`)
has the entry point `corvus-job <build_features|evaluate|train>`. Code: `ml/src/corvus_ml/jobs/`.
Jobs on raw packet bytes (ADR-CORV-0008) are described in [raw-byte-tokenizer.md](raw-byte-tokenizer.md);
this page lists their parameters and extra requirements.

## 1. Contract with the runner

| Item | Value |
|---|---|
| Argument | the job type (`build_features`, `evaluate`, `train`); must equal `CORVUS_JOB_TYPE` if that is set |
| `CORVUS_JOB_ID`, `CORVUS_JOB_SNAPSHOT_ID` | UUIDs |
| `CORVUS_JOB_PARAMS` | JSON object, see section 3; unknown keys fail the job |
| `CORVUS_API_URL`, `CORVUS_API_TOKEN` | API base URL and bearer token; every request carries `Authorization: Bearer <token>` and `X-Corvus-Job-Id` |
| `CORVUS_S3_ENDPOINT_URL`, `CORVUS_S3_ACCESS_KEY`, `CORVUS_S3_SECRET_KEY`, `CORVUS_S3_BUCKET` (default `corvus-lake`), `CORVUS_S3_REGION` (default `us-east-1`) | the lake bucket, path-style addressing, same names as the services |
| `CORVUS_JOB_SCRATCH_DIR` | optional directory for temporary files (default: the system temp dir; features can be large, mount an `emptyDir`) |
| `CORVUS_PG_DSN`, `PGPASSWORD`, `CORVUS_KEK` | **raw-byte jobs only** (`build_features` with `kind = "raw_bytes"` or `"train_tokenizer"`): read-only access to `corvus.player_keys` and the KEK that unwraps the DEKs. The role needs `SELECT` on `corvus.player_keys`; the values are scrubbed from job messages |
| `CORVUS_OPTUNA_STORAGE` | optional Optuna storage URL for `params.tune` (default: a SQLite file in the job's scratch dir) |

Exit code 0: succeeded. 1: the job failed (reported as `failed`). 2: the contract was violated or the
API could not be told about the state.

Status updates: `PATCH /api/v1/jobs/{id}` with `running` at the start, then `succeeded` (message and
`result`) or `failed` (message). A message is at most 500 characters and is scrubbed: the API token,
the S3 keys and anything shaped like `Bearer ...` are replaced by `***`. SIGTERM (for example the
Kubernetes deadline) is reported as `failed`. Non-finite numbers are dropped from `result`.

API calls beyond `PATCH /jobs/{id}`:

| Call | Used for |
|---|---|
| `GET /api/v1/snapshots/{id}` | the manifest: `files.events[].key` (object keys of the `events` files), `sessions`; `needs_rebuild` |
| `GET /api/v1/snapshots/{id}/files` | `raw[].key`: the live `raw` files that list a session of the snapshot (raw-byte jobs) |
| `GET /api/v1/snapshots/{id}/sessions?exclude_erased=true` | sessions of the snapshot, paged (`limit`, `cursor`, `next_cursor`) |
| `POST /api/v1/jobs/{job_id}/artifacts` | registering every prefix a job wrote, right after writing it (section 2) |
| `POST /api/v1/models` | registering a trained model (not retried) |

**Shape assumed for the sessions endpoint.** Each item has `session_id` (hex, 16 bytes), `pseudonym`
(hex, 32 bytes), `started_at` (ISO 8601), `segments` (`[{"segment": 0, "game_context": "pvp"}]`),
`client_protocol` (integer or `null`, `sessions.client_protocol`; the raw-byte build takes the protocol
token from it, a missing value gives `[PROTO_OTHER]` and is counted) and optionally `labels` (`[{"label": "cheat", "source": "honeypot", "confidence": 1.0, "valid_from": null,
"valid_to": null}]`) and `erased`. Sessions flagged `erased` are skipped by the job even if the API
returned them, and sessions that are not in the manifest's `sessions` list are ignored. A missing
pseudonym keeps the session out of evaluation (windows without a pseudonym are dropped there).

## 2. Artefact layout (below the bucket root)

```
features/<snapshot_id>/windows/part-*.parquet
features/<snapshot_id>/window_tokens/part-*.parquet
features/<snapshot_id>/feature_spec.json
features/<snapshot_id>/sessions.parquet      session_id, pseudonym (hex), started_at
features/<snapshot_id>/labels.parquet        session_id, label, source, confidence, valid_from, valid_to
features/<snapshot_id>/build.json            written last: marks a complete build
reports/<snapshot_id>/<job_id>/report.json, report.md
models/<name>/<version>/model.onnx           calibrated, single output "score"
models/<name>/<version>/feature_spec.json
models/<name>/<version>/calibration.json
models/<name>/<version>/model_card.json
models/<name>/<version>/evaluation.json     the candidate's entry of report.json
```

A raw-byte build writes `features/<snapshot_id>/raw/` instead (tokenizer, feature spec, packet table,
sessions, labels, `build.json`; section 4 of [raw-byte-tokenizer.md](raw-byte-tokenizer.md)); a
`train_tokenizer` job writes `tokenizers/<snapshot_id>/<job_id>/{tokenizer.json,feature_spec.json}`.
Raw-byte models additionally upload `models/<name>/<version>/tokenizer.json`.

**Register what you write.** `features/<snapshot_id>/raw/` holds tokenised payloads, which are personal data derived from
`raw`. Right after writing a prefix (and, for the feature builds, after its `build.json` marker) a job calls
`POST /api/v1/jobs/{job_id}/artifacts` with `{"kind", "s3_prefix", "contains_raw"}` (`s3_prefix` ends with `/`):

| Job | `kind` | `s3_prefix` | `contains_raw` |
|---|---|---|---|
| `build_features`, `raw_bytes` | `raw_features` | `features/<snapshot_id>/raw/` | `true` |
| `build_features`, aggregate | `window_features`, `window_tokens` | `features/<snapshot_id>/windows/`, `features/<snapshot_id>/window_tokens/` | `false` |
| `build_features`, `train_tokenizer` | `tokenizer` | `tokenizers/<snapshot_id>/<job_id>/` | `false` |

Erasure and the raw retention then delete the prefix; see [data-lake.md](data-lake.md) section 7 and
[control-plane-api.md](control-plane-api.md). `201` is a new registration, `200` a repeat (a repeated call is safe and
is retried like a read). **`409`** means an erasure invalidated the snapshot's raw-derived artefacts after the job
started: the job deletes the prefix it just wrote and fails with "an erasure invalidated the raw-derived artefacts of
this snapshot while the job ran; <prefix> was deleted. Start a new job to rebuild". Other errors fail the job as well.

A new `build_features` job replaces `features/<snapshot_id>/` completely (the raw-byte build only its `raw/` prefix). The windows and tokens
tables are those of [features.md](features.md); the same code builds them offline and online.

## 3. Jobs and parameters

**`build_features`** reads the `events` files of the snapshot's manifest (explicit file list, no
directory scan) and the sessions from the API. It reads a few sessions per task and Arrow batches,
so memory stays bounded; the number of worker processes is at most the CPU limit of the cgroup.

| Param | Default | Meaning |
|---|---|---|
| `game_context` / `game_contexts` | all | only segments in these contexts |
| `window`, `overlap`, `max_duration_s`, `min_frames`, `dt_edges_ms` | as `corvus-features` | windowing, recorded in `feature_spec.json` |
| `group_size` | 4 | sessions per task (memory knob) |
| `workers` | cgroup limit | upper bound; never above the cgroup limit |
| `strict` | `false` | refuse a snapshot with `needs_rebuild`, see below |

**`needs_rebuild`.** `GET /snapshots/{id}` flags a snapshot whose raw-derived artefacts an erasure deleted. A feature
build is the way to recreate them, so by default (`strict` false, for all `build_features` kinds) the job only logs a
warning and builds. With `"strict": true` it fails before reading any data and writes nothing.

**`build_features` with `kind = "raw_bytes"`** reads the `events` and `raw` files of the snapshot, decrypts
the payloads, trains the tokenizer (unless `tokenizer_prefix` points at a trained one) and tokenizes all
sessions. Sessions whose player has no DEK (erased) are skipped; the result counts them
(`sessions_crypto_shredded`). `raw` files come from the `raw` list of `GET /snapshots/{id}/files`, so no partition is scanned; an empty
list means the snapshot has no raw files (the job fails). Only if the API does not send the field (an older
version) the job logs a warning and falls back to `manifest.files.raw`, else to the `lake/raw/date=...`
partitions of the sessions' start dates. Parameters (all optional): `kind` (`aggregate` default, `raw_bytes`,
`train_tokenizer`), `strict` (see below), `game_context(s)`, `group_size` (4), `vocab_size` (4096), `min_frequency` (2),
`train_bytes_per_payload` (256), `max_train_payloads`, `max_train_bytes`, `tokenizer_sessions` (2000),
`tokenizer_prefix`, `dt_buckets`, `dt_min_ms`, `dt_max_ms`, `protocols`, `packet_type_max`,
`max_tokens_per_packet` (48), `proto_mode` and the window defaults recorded in the spec (`window_seconds`,
`stride_seconds`, `max_packets`, `overflow`, `min_packets`, `macro_window_seconds`). The build runs in one
process (decryption and tokenization are not parallelised).

**`evaluate`** needs a complete feature build and runs `corvus-eval` on it ([evaluate-models.md](../how-to/evaluate-models.md));
it uploads the reports and registers nothing. **`train`** does the same, then for each game context
takes the best usable candidate (highest `select_by` on the test split, ties by lower p99 latency;
the ONNX parity check must pass), uploads its artefacts and registers the model.

| Param | Default | Meaning |
|---|---|---|
| `candidates` | `["isolation_forest", "gradient_boosting"]` | list of names or `{name: {hyperparameters}}`; `raw_hier_classifier` / `raw_hier_anomaly` need the raw build and cannot be mixed with the others |
| `game_context` / `game_contexts` | all | contexts to evaluate |
| `unlabelled` | `exclude` | or `assume_legit` |
| `label_sources`, `min_confidence` | all, 0 | which labels count (for example `["honeypot", "staff_verdict"]`) |
| `seed`, `threads`, `seq_len`, `split`, `fpr_targets`, `learning_curve`, `benchmark` | as `eval.toml` | `threads` at most 8 |
| `calibration` | `auto` | `isotonic`, `platt`, `ecdf`; `none` is refused |
| `select_by` | `recall@fpr0.1pct` | metric that picks the candidate |
| `tune` | off | `true` or an object (`trials`, `timeout_s`, `storage`, `study_name`, `tune_windows`, `space`): an Optuna study per tunable candidate before the evaluation, see section 8 of [raw-byte-tokenizer.md](raw-byte-tokenizer.md). The API has no `tune` job type, so a search is an `evaluate` or `train` job with this parameter; the result lists `best_params` per candidate |
| `window_seconds`, `stride_seconds`, `max_packets`, `overflow`, `min_packets`, `macro_window_seconds`, `max_windows` | from the build | raw-byte candidates only: override the window configuration of the raw build (`max_windows` 200 000 caps the dataset); refused for other candidates |
| `model_name`, `version`, `domain` (`train`) | candidate name, `<snapshot8>-<job8>`, `general` | registry fields; the registered name is `<model_name>-<game_context>` |

## 4. Output semantics and calibration

Every exported model outputs a calibrated, probability-like score in [0, 1] in the single output
`score` (float32, shape `[batch]`); the calibration is part of the graph, nothing else has to be
applied. Supervised candidates: isotonic regression or Platt scaling on the validation split
(estimate of P(cheating)). Unsupervised candidates: the empirical CDF of the validation windows
that are legit (or assumed legit), so 0.99 means "higher than 99 % of legit windows". Details and
limits: [evaluate-models.md](../how-to/evaluate-models.md#calibration). `model_card.json` records the
candidate, the calibration (`method`, `n_fit`, `fit_on`), snapshot and job, `created_at`, the
metrics and `output_semantics` (`onnx_output`, `range`, `meaning`, `post_processing`).

## 5. Model registration

`POST /models` with `name`, `version`, `candidate_type`, `game_context`, `domain`, `snapshot_id`,
`metrics` and `onnx_key` (`models/<name>/<version>/model.onnx`); the stage is `registered`.
`candidate_type` is `<candidate>_calibrated` (for example `isolation_forest_calibrated`), because
the graph differs from the raw scikit-learn graphs: see section 6.

Metrics (the test split of the chosen candidate): `roc_auc`, `pr_auc`, `recall_at_fpr_1pct`,
`recall_at_fpr_0_1pct` (the quality gate's name for `recall@fpr0.1pct`), their `_player_max` /
`_player_mean` variants, `resolved_at_fpr_*` (1 = the split resolves the operating point), `ece`
(supervised), `p50_latency_ms`, `p99_latency_ms`, `mean_latency_ms`, `batch_throughput_wps`,
`peak_rss_mb`, `model_size_kb`, `n_params`, `fit_seconds`, `test_windows`, `test_cheat_windows`.
Without latency (`benchmark.enabled = false`) the promotion to `enforce` fails closed.

## 6. What the scorer relies on

`corvus_services.scoring.models.scores_from_outputs` chooses how to read a graph by `candidate_type`:
`isolation_forest` and `gradient_boosting` use output 1 of the raw scikit-learn graph, anything else
the first output, clipped to [0, 1]. Calibrated graphs have one output, so they are registered with
the type `<candidate>_calibrated`, which takes the generic branch and works without a change.
Recommended follow-up in the services: decide by `output_semantics` in `model_card.json` (or by the
output name `score`) instead of by candidate type, and drop the clip once all models are calibrated.
