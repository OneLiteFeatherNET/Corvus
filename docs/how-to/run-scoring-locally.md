# How to run the scoring service locally

Runs `corvus-score` next to the dev stack of [run-ingest-locally.md](run-ingest-locally.md). The service consumes the
capture stream, featurizes the sessions with the same code as the offline build, scores the windows with the ONNX
models of the model registry and writes the scores to PostgreSQL (`shadow`, `canary`, `enforce`) and to the
`CORVUS_SCORE` stream (`canary`, `enforce` only). The contract is section 6 of
[data-lake.md](../reference/data-lake.md).

Prerequisites: the dev stack from the ingest how-to (`deploy/dev/.env` filled in) and at least one registered model in
a scored stage whose files are in the bucket: `<onnx_key>` (the ONNX file) and `feature_spec.json` in the same
directory, as the evaluation export writes them (see [evaluate-models.md](evaluate-models.md)).

1. Start the stack including the scorer. `migrate` creates the score tables (`0003_scores.sql`):

   ```sh
   cd deploy/dev
   docker compose up --build nats s3 s3-init postgres migrate ingest score
   ```

   The `score` service runs one replica that owns all 16 partitions (`CORVUS_SCORE_REPLICAS=1`).

2. Put a model into a scored stage with the control-plane API (role `data_scientist`; see
   [control-plane-api.md](../reference/control-plane-api.md)):

   ```sh
   curl -s -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     -d '{"stage": "shadow", "reason": "local try-out"}' localhost:8080/api/v1/models/<id>/stage
   ```

   The scorer picks it up within `CORVUS_SCORE_REGISTRY_POLL_S` (30 s). A model whose `feature_spec.json` does not
   match the running code is not loaded; see `corvus_score_model_load_failures_total` and the error log.

3. Check readiness and metrics (the scorer uses host port 9465 so it does not clash with ingest):

   ```sh
   curl -s localhost:9465/livez          # liveness, no dependencies
   curl -s localhost:9465/healthz        # readiness: nats, postgres, registry
   curl -s localhost:9465/metrics | grep corvus_score
   ```

4. Point the capture plugin at `nats://localhost:4222`. Once players play, read the scores through the API
   (role `staff`, the pseudonym is the 64-hex form) or directly:

   ```sh
   curl -s -H "Authorization: Bearer $TOKEN" localhost:8080/api/v1/scores/players/<pseudonym>
   docker compose exec postgres psql -U corvus -c 'SELECT * FROM corvus.scores_latest'
   ```

   Published scores are on `corvus.score.v1.<game_context>` in the `CORVUS_SCORE` stream, for example
   `nats sub 'corvus.score.v1.>'`.

## Several replicas

Replica `i` of `n` owns the partitions `p` with `p mod n == i`. Give every replica `CORVUS_SCORE_REPLICAS=n` and its
own `CORVUS_SCORE_REPLICA_INDEX=i` (or a hostname ending in `-<i>`). The durable consumer `corvus-score-<i>` is
created, or its filters updated, at startup. Changing `n` or `CORVUS_CAPTURE_PARTITIONS` changes the assignment of every
replica; roll all replicas together and keep the partition count identical on all proxies.

## Tuning

| Variable | Default | Effect |
|---|---|---|
| `CORVUS_SCORE_CPUS` | cgroup limit | CPU budget for inference; split across the loaded models (intra-op threads), inter-op is always 1 |
| `CORVUS_SCORE_BATCH_MAX`, `CORVUS_SCORE_BATCH_MAX_WAIT_MS` | `64`, `50` | Micro-batch size and maximum wait |
| `CORVUS_SCORE_EMA_ALPHA` | `0.3` | Smoothing of `score_smoothed` |
| `CORVUS_SCORE_SESSIONS_MAX`, `CORVUS_SCORE_SESSION_IDLE_S` | `20000`, `900` | Featurizer state bound and idle eviction |
| `CORVUS_SCORE_RAW_MAX_BUFFERED_FRAMES` | `16384` | Raw-byte models: frames buffered per session, segment and windowing; the oldest are dropped and counted (`corvus_score_raw_frames_dropped_total`) |
| `CORVUS_SCORE_P99_BUDGET_MS` | `5000` | Raw-byte models: sanity bound, not a real-time budget (scores are asynchronous). A model whose `max_packets` is estimated to exceed this p99 per window logs a warning at load; `0` disables |
| `CORVUS_SCORE_MEMORY_BUDGET_MB`, `CORVUS_SCORE_MEMORY_FRACTION` | half of the cgroup memory limit, `0.5` | Raw-byte models: memory one ONNX run may use. A micro-batch is split into runs of at most `budget / estimated bytes per window` rows (at least 1), so a long-window model cannot get the pod OOM-killed; about 0.3 GiB per window at 2048 packets (see [raw-byte-tokenizer.md](../reference/raw-byte-tokenizer.md) section 10). The load logs the estimate and the rows per run; a window above the budget is logged as an error and still served |
| `CORVUS_SCORE_LAG_POLL_S`, `CORVUS_SCORE_LAG_GROWTH_POLLS` | `15`, `4` | Consumer lag watch: the pending count of the capture consumer is polled every N seconds; a warning is logged once when it grew over this many consecutive polls (`0` polls disables the watch) |
| `CORVUS_SCORE_WRITE_QUEUE` | `20000` | History rows queued before they are dropped (latest scores are never dropped) |
| `CORVUS_SCORE_MODELS_DIR` | `/tmp/corvus-score/models` | Local model cache |
| `CORVUS_SCORE_HISTORY_RETENTION_DAYS` | `14` | Applied by `corvus-housekeeping` |

## Raw-byte models

A model whose `model_card.json` has `input_kind: "raw_bytes"` is loaded with its `tokenizer.json`, which must hash to
the value in `feature_spec.json` (otherwise the model is rejected and the others keep running). The scorer needs the
plaintext payloads of the capture stream for it: they are tokenized when a batch arrives and only token ids are kept,
for about one window per session and segment. Payload bytes are never persisted or logged. The buffer costs roughly
0.3 KiB per buffered frame; with many concurrent players lower `CORVUS_SCORE_SESSIONS_MAX` or raise the memory limit.
See [raw-byte-tokenizer.md](../reference/raw-byte-tokenizer.md) section 10 for the contract and the measured cost.

## Tests

```sh
cd services
uv run pytest tests/scoring                 # unit tests, no Docker needed
uv run pytest -m integration tests/scoring  # needs NATS and PostgreSQL from the compose stack
```
