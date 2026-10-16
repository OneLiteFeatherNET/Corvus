# Reference: raw-byte tokenizer and hierarchical model

ADR-CORV-0008 decides that the model input is the tokenised raw packet bytes. This page is the
contract of that path: how the bytes are read, how they become token ids, how windows are cut, what
the model is, what the exported artefacts contain and what a scorer has to do to feed the model.
Code: `ml/src/corvus_ml/raw/` (reader, tokenizer, windows, model), `ml/src/corvus_ml/eval/` (candidates,
tuning) and `ml/src/corvus_ml/jobs/raw_job.py`.

Everything here is additive: the aggregate and token-sequence models of [features.md](features.md)
and [feature-spec.md](feature-spec.md) are unchanged.

## 1. Pipeline

```
lake events + raw ──decrypt──> frames of a session ──BPE──> packet table (Parquet, in S3)
                                                              │ windows (seconds, stride, caps)
                                                              v
        ONNX (score) <──export── model <──pretrain/finetune── token tensor (batch, [macro,] P, T+1)
```

1. **Read.** `raw` rows are decrypted and joined with `events` on (`session_id`, `seq`).
2. **Train the tokenizer** on payload prefixes of a session sample (step `train_tokenizer`).
3. **Tokenize** every frame once into the packet table. It does not depend on the window settings.
4. **Cut windows** from the packet table when a model is evaluated or trained, so `window_seconds`,
   `stride_seconds` and `max_packets` can be tuned without reading the encrypted data again.
5. **Pretrain / train / tune / export** (sections 6 to 9).

## 2. Reading raw bytes

Payloads are AES-256-GCM encrypted with the player's DEK; the AAD is `session_id || seq` (8 bytes
big-endian); see [data-lake.md](data-lake.md). `corvus_ml.raw.crypto` repeats the services' algorithm
(the ML package must not depend on `corvus_services`); `tests/test_raw_crypto.py` checks it against
vectors computed with `corvus_services.crypto`.

The wrapped DEKs are not available through the control-plane API, so the port
`corvus_ml.raw.keys.DekProvider` has an adapter that reads them directly:

| Item | Value |
|---|---|
| Table | `corvus.player_keys` (`pseudonym`, `dek_wrapped`), read-only `SELECT ... WHERE pseudonym = ANY(...)` |
| `CORVUS_PG_DSN`, `PGPASSWORD` | the connection (same names as the services) |
| `CORVUS_KEK` | base64 KEK (32 bytes); unwraps `nonce(12) \|\| AES-GCM(dek)` with the pseudonym as AAD |
| Permission | the job's database role needs `SELECT` on `corvus.player_keys` and nothing else |

All three values are scrubbed from job messages. A pseudonym without a `player_keys` row is an
**erased** player: its sessions are skipped without a message and counted
(`sessions_crypto_shredded`). A frame that does not decrypt (wrong key, tampered ciphertext) is
treated as a frame without payload and counted (`decrypt_failures`). Sessions without a pseudonym
cannot be decrypted (`sessions_without_pseudonym`). Frames without a `raw` row (privacy-stripped
types, clientbound frames outside the allowlist, empty payloads) keep their header tokens.

Sessions come from `GET /api/v1/snapshots/{id}/sessions`. The raw-byte build additionally reads
`client_protocol` (integer, optional) of each item for the protocol token; see
[ml-jobs.md](ml-jobs.md). The live `raw` files come from `raw` of `GET /api/v1/snapshots/{id}/files`
(found through `lake_file_sessions`, compaction replacements followed); only without that field does
the job fall back to the `lake/raw/date=...` partitions of the days the snapshot's sessions started on.

## 3. Token layout

Ids below `n_special` are reserved; byte-level BPE tokens follow. The layout is determined by five
parameters stored in the feature spec (`dt_buckets` 16, `dt_min_ms` 0.5, `dt_max_ms` 5000,
`protocols`, `packet_type_max` 255).

| Ids (in order) | Tokens |
|---|---|
| 0 to 4 | `[PAD]` `[MASK]` `[CLS]` `[SEP]` `[UNK]` |
| 5 to 7 | `[DIR_SB]` `[DIR_CB]` `[DIR_OTHER]` (serverbound, clientbound, other) |
| 8, 9 | `[TRUNC]` `[NOPAYLOAD]` |
| 10 to 12 | `[PROTO_OTHER]` `[DT_NONE]` `[PKT_OTHER]` |
| next `dt_buckets` | `[DT_0]` ... `[DT_<n-1>]`: log-spaced buckets of the gap to the previous frame of the segment |
| next `len(protocols)` | `[PROTO_<v>]` for the known protocol versions (default 47, 340, 754, 758, 762 to 771); anything else, or none: `[PROTO_OTHER]` |
| next `packet_type_max` | `[PKT_1]` ... `[PKT_<max>]`: one token per Corvus packet type id (`vocabulary.json`, ids 1 to 228 today); ids above the maximum or 0: `[PKT_OTHER]` |

The vocabulary is append-only, so existing ids keep their token. `packet_type_max` defaults to 255 so
the tokenizer does not have to be retrained when types are added; the image does not need
`vocabulary.json`.

**Packet row** (one frame, at most `max_tokens_per_packet`, default 48, ids; the rest is `[PAD]`):

```
[DIR_*] [PKT_<id>] [DT_<bucket>] [PROTO_<v>]? <payload BPE tokens...> [TRUNC]? [SEP]
[DIR_*] [PKT_<id>] [DT_<bucket>] [PROTO_<v>]? [NOPAYLOAD] [SEP]
```

- `[DT_*]` is `[DT_NONE]` for the first frame of a segment, otherwise the bucket of the gap in ms.
- `[PROTO_*]` position is `proto_mode`: `first` (first packet of every window, default), `every`, `none`.
- `[NOPAYLOAD]`: the frame has no payload row (stripped, dropped or empty). The header still counts.
- `[TRUNC]`: `payload_original_size` is larger than the recorded bytes (the plugin cut the payload at
  2048), **or** the payload has more BPE tokens than fit into the row. The model cannot tell the two
  apart; both mean "there is more than you see".
- At most `max_tokens_per_packet - 3 - 2` payload tokens fit (`- 3` header, `- 2` flag and `[SEP]`);
  with a protocol token one less, and a payload that no longer fits is cut and flagged `[TRUNC]`.

### Byte tokens

Payload byte `b` is mapped to the character `chr(0x100 + b)` and fed to a Hugging Face `tokenizers`
BPE model without normalizer or pre-tokenizer, so merges may span any bytes and decoding is exact
(`RawTokenizer.decode(encode(x)) == x` for all byte strings, tested). The characters never collide with
the ASCII spelling of the reserved tokens, so a payload that contains the text `[PAD]` cannot forge
one. The 256 single-byte tokens come right after the reserved ids; merges follow.

Training (`TokenizerConfig`): `vocab_size` 4096 (at most 32767; ids are stored as `int16`),
`min_frequency` 2, only the first `train_bytes_per_payload` (256) bytes of each payload, at most
`max_train_payloads` (2 000 000) payloads and `max_train_bytes` (256 MiB), whichever comes first. The
iterator is streamed, so memory is bounded by the number of distinct payload prefixes. Training is
deterministic for the same payloads in the same order (the job samples sessions evenly from the
sorted session ids). `tokenizer.json` is written compactly and hashed (SHA-256) into the spec.

`TokenizerConfig.mode = "bytes"` (job parameter `tokenizer_mode`, `[raw.tokenizer] mode` in an eval
config) builds the **plain-byte tokenizer** instead: no merges, no training data, one token per payload
byte, byte `b` has id `n_special + b` (vocabulary `n_special + 256`; `vocab_size` is ignored). The mode is
recorded in the spec (`tokenizer.mode`, default `bpe` for older specs) next to the file hash, so a model
trained on one tokenizer is refused by a runtime that expects the other. A packet row then holds one token
per byte: raise `max_tokens_per_packet` (for example 96) and expect a longer local sequence.

## 4. Artefacts

Written by `build_features` with `kind = "raw_bytes"` below `features/<snapshot_id>/raw/`
(the aggregate build in `features/<snapshot_id>/` is not touched):

```
raw/tokenizer.json            the trained tokenizer (hash in feature_spec.json)
raw/feature_spec.json         kind raw_bytes, see section 9
raw/packets/part-*.parquet    packet table
raw/sessions.parquet          session_id, pseudonym (hex), started_at, client_protocol
raw/labels.parquet            as for the aggregate build
raw/build.json                written last: marks a complete build
```

Packet table (one row per frame in a segment with a game context): `session_id` fixed_size_binary(16),
`segment` uint32, `seq` uint64, `offset_nanos` uint64, `direction` uint8, `packet_type` uint32,
`dt_id` int16 (token id), `trunc` bool, `nopayload` bool, `tokens` list&lt;int16&gt; (payload BPE ids),
`game_context` string. `kind = "train_tokenizer"` writes only `tokenizer.json` and
`feature_spec.json` to `tokenizers/<snapshot_id>/<job_id>/`; a later build reuses them with
`tokenizer_prefix` (a tokenizer trained once can serve several snapshots).

## 5. Windows

Per (session, segment), never across segments:

| Setting | Default | Meaning |
|---|---|---|
| `window_seconds` | 20 | length of a window |
| `stride_seconds` | 10 | distance between window starts (the grid is anchored at the first packet of the segment) |
| `max_packets` | 2048 | packets per window; on overflow `overflow = "recent"` keeps the newest, `"uniform"` an evenly spaced subsample |
| `min_packets` | 8 | windows with fewer packets are dropped |
| `macro_window_seconds` | none | hierarchy over minutes: this many seconds of consecutive windows form one macro window; needs `stride_seconds == window_seconds` |

The defaults (20 s windows, half-window overlap, 2048 packets) follow the traffic of one real player.
Pilot measurement 2026-10-16, single player (lobby and survival, 2 h 10 min, 269,566 packets), packets per
window:

| Window | p50 | p90 | p99 | max |
|---|---|---|---|---|
| 20 s, both directions | 524 | 1,386 | 1,969 | 8,180 |
| 20 s, serverbound only | | | 888 | |
| 20 s, clientbound only | | | 1,084 | |
| 10 s (previous default), both directions | | | 1,084 | |

`max_packets = 2048` therefore covers the p99 of a 20 s window, whereas the former 1024 already truncated
a 10 s window at p99. The windows above the cap are handled by `overflow`. This is one player; re-measure
with more players before tightening or raising the cap. A model keeps the window recorded in its own
spec: the defaults only apply to new builds.

A partial window at the end of a segment is kept only if it contains packets that no earlier window
contained. A segment shorter than one window gives one window. In a macro window a missing window
(too few packets) stays padding; the second-level model ignores it.

**Model input**: one float32 array `(batch, [macro,] packets, max_tokens_per_packet + 1)`. Channels
`0 .. T-1` are token ids (exact in float32; `0` = `[PAD]`, a packet whose first token is `[PAD]`
is padding), the last channel is the packet time in seconds since the window start.
`corvus_ml.raw.windows.to_input` builds it. Windows are padded to the largest packet count in the
data; the exported graph accepts any packet count.

## 6. Model

`corvus_ml.raw.model` (PyTorch, CPU, ONNX opset 17), MEGABYTE-style:

1. **Local encoder** per packet: token embedding plus learned position, `local_layers` (1) small
   transformer blocks (`local_type = "cnn"` uses two 1D convolutions instead) over the packet's tokens,
   masked mean pooling. The embeddings of the three header tokens are concatenated and fused with the
   pooled vector into the **packet embedding** (`d_model`).
2. **Global transformer** (`layers` 2, `d_model` 64, `heads` 4) over `[CLS]` and the packet embeddings.
   The position of a packet is a sinusoidal encoding of its time in seconds (`time_freqs` 16 log-spaced
   periods from 10 ms to 600 s), not its index. Padding packets are masked.
3. **Head**: `[CLS]` to one logit.
4. **Second level** (`macro_window_seconds` set): the `[CLS]` vectors of the windows of a macro window
   get a learned window position, pass `macro_layers` (1) transformer blocks and are pooled (`macro_pool`
   `"attention"` or `"mean"`, missing windows masked) before the head. The packet axis and the batch axis
   are dynamic in ONNX; the macro axis is fixed.

Attention is written with matmul and softmax with an additive mask, so padded packets cannot produce
NaN and the graph uses plain operators. Extra padding packets do not change the output (tested).

| Candidate | Supervised | Output before calibration | Notes |
|---|---|---|---|
| `raw_hier_classifier` | yes | probability of cheating | BCE with the class ratio as `pos_weight`; supports macro windows |
| `raw_hier_anomaly` | no | mean masked-prediction loss of the window (higher = less predictable) | trained on legit windows with random masks; single windows only |

The anomaly score measures how unpredictable a window is for a model of legit traffic. It is not a
cheat detector by construction: a cheat that is more regular than human input (a constant cadence)
scores low. Treat it as a feature for review and compare it against labels before trusting it.

Parameters of both candidates (all optional; unknown keys are errors): `epochs` 10, `batch_size` 16,
`lr` (number or `"auto"` for the LR range test), `weight_decay` 0.01, `patience` 3, `d_model`, `layers`,
`heads`, `ffn_mult` 2, `dropout` 0.1, `local_dim` 32, `local_layers` 1, `local_type`, `local_heads` 2,
`macro_layers`, `macro_pool`, `pretrain_epochs` 0, `pretrain_lr` 1e-3, `init_from`, `checkpoint_out`,
`p_token` 0.15, `p_packet` 0.1, `first_tokens` 2, `max_masked` 2048, `score_period` 8,
`score_passes` 2, `score_batch` 16, `memory_budget_mb` (see below). Training is AdamW with linear warmup
and cosine decay, gradient clipping and early stopping on the validation loss.

### Memory behaviour

The packet transformer attends over all packets of a window, so the activation memory of one window grows
with the square of `max_packets` (the byte transformer inside a packet adds a term linear in it). Measured on
a 4-CPU machine with the default model (`d_model` 64, 2 layers, 4 heads, `local_dim` 32, 48 tokens per packet):

| `max_packets` | training, per window | scoring, per window |
|---|---|---|
| 512 | about 0.11 GiB | about 0.06 GiB |
| 1024 | about 0.25 GiB | about 0.12 GiB |
| 2048 | about 0.72 GiB | about 0.26 GiB |

A batch of 16 windows at 2048 packets therefore needs about 12 GiB, more than the default job pod limit (8 GiB).
The ONNX parity check (64 test windows in one session run) needed about 17 GiB, and a validation pass over
a whole split in one go more still. Every pass over raw windows is capped:

- **Estimate.** With `P` packets, `T = max_tokens_per_packet`, `d = d_model`, `dl = local_dim` and 4-byte floats,
  `global_att = heads * (P+1)^2 * 4` and `local_att = local_heads * T^2 * P * 4` (0 for `local_type = "cnn"`).
  Training: `4 * (layers * global_att + local_layers * local_att) + local_layers * (6 + 2 * ffn_mult) * P*T*dl*4
  + layers * (8 + 2 * ffn_mult) * (P+1)*d*4 + 3 * max_masked * vocab * 4`. Scoring: `3 * max(global_att, local_att)
  + (6 + 2 * ffn_mult) * P*T*dl*4`. JEPA objectives add one scoring pass, macro windows multiply by
  `macro_windows`. The estimate is 1.2 to 1.4 times the measurement (see
  `ml/src/corvus_ml/membudget.py`; `ml/scripts/measure_raw_memory.py` reproduces the numbers).
- **Budget.** The activations may use `memory_budget_mb` (candidate parameter), else the environment variable
  `CORVUS_ML_MEMORY_BUDGET_MB`, else half of the container memory limit (cgroup `memory.max`, else the installed
  RAM). The other half is for the window tensors, weights, optimiser state and runtimes.
- **Micro-batch.** A pass processes at most `budget / estimate` windows (at least 1). `batch_size` stays the
  optimiser batch: the gradients of the micro-batches are accumulated, so the update is that of the whole batch
  (for the masked-token loss, which divides by the number of masked tokens of a chunk, only approximately).
  Training, pretraining, the LR range test (same `batch_size`, same micro-batch), validation, `score_batch`, the
  kNN memory bank, the ONNX parity run and the benchmark batch are all capped. A reduced micro-batch is logged
  at warning level (`micro-batch 4 instead of 16 ...`). Smaller micro-batches only cost time.

Rule of thumb: memory per window is proportional to `max_packets` squared, so halving `max_packets` divides the
attention part by four. With the default model and the default 4 GiB budget (8 GiB pod) the micro-batch is
about 4 windows at 2048 packets, 13 at 1024 and the whole batch of 16 at 512. If a job is still killed, lower
`memory_budget_mb` (or raise `api.jobs.memoryLimit` in the Helm chart, `CORVUS_API_JOB_K8S_MEMORY_LIMIT`); the
resident window tensors (`windows * P * T * 2` bytes as `int16`, plus a copy per split) come on top of the
budget.

## 7. Pretraining

Objectives on unlabelled windows (all windows of the training players, labelled or not):

- **Masked byte tokens**: `p_token` of the payload tokens become `[MASK]`; they are predicted from the
  local state plus the global state of their packet (`max_masked` caps the masked tokens per batch to
  bound the logits).
- **Masked packets**: `p_packet` of the packets get `[PKT_*]` and the first `first_tokens` payload tokens
  masked; the packet type and those tokens are predicted from the global state.

`pretrain_epochs > 0` pretrains inside the classifier's `fit`; `checkpoint_out` saves the weights;
`init_from` starts a classifier from a checkpoint (the architecture keys must match, otherwise the load
fails with the list of differences). `corvus-eval pretrain` pretrains on its own and writes
`<out>/pretrain/<game_context>.pt`. A checkpoint made on single windows also initialises a macro
classifier. `raw_hier_anomaly` is itself the pretraining objective.

## 8. Hyper-parameter search

`corvus-eval tune` (or `params.tune` in an `evaluate` / `train` job) runs one Optuna study per game
context and tunable candidate (raw-byte candidates, `gru_autoencoder`, `cnn1d`): TPE sampler and median
pruner (2 startup trials, 1 warm-up epoch), fixed seed (`run.seed`), objective = best validation loss
(the loss early stopping uses; the pruner sees it per epoch). The split is by player and fixed before the
search; the test split is never touched. After the search the candidate is evaluated with the best
parameters, and the exported spec carries the best window.

| Parameter | Space |
|---|---|
| `lr` | log 3e-4 to 1e-2 |
| `weight_decay` | log 1e-4 to 1e-1 |
| `d_model` | 32, 64, 96, 128 |
| `layers` | 1 to 4 |
| `dropout` | 0 to 0.3 |
| `window_seconds` | 5, 10, 20 (stride and macro window scale with it) |
| `max_packets` | 256, 512, 1024, 2048 |
| `hidden`, `latent` (`gru_autoencoder`), `channels` (`cnn1d`) | categorical |

`[tune]` keys: `trials` 20, `timeout_s`, `storage`, `study_name`, `tune_windows` true, `space` (per
parameter `[low, high]` or a list of choices). Storage: `"memory"`, a URL, or by default
`CORVUS_OPTUNA_STORAGE`, else `sqlite:///<out>/optuna.db`. With a persistent storage an existing study is
resumed up to `trials` trials in total. The report lists the sampler, pruner, search space, every trial and
the best parameters (`report.json` and `report.md`).

## 9. Export, feature spec and model card

The exported graph is calibrated as for every other candidate: input `x`, **single output `score`**
(float32, `[batch]`, in [0, 1], nothing to apply afterwards), see [ml-jobs.md](ml-jobs.md). The calibration
is fitted on the validation split and baked into the graph; parity of the graph against PyTorch is
checked on a sample of the test windows (`parity_ok`).

`feature_spec.json` next to the model has `"version": 2` and a `raw_bytes` section inside
`model_inputs`:

```json
"raw_bytes": {
  "tokenizer": {"file": "tokenizer.json", "sha256": "…", "vocab_size": 4096, "mode": "bpe", "byte_mapping": "chr(0x100 + byte)"},
  "layout": {"dt_buckets": 16, "dt_min_ms": 0.5, "dt_max_ms": 5000.0, "protocols": [47, 340, …], "packet_type_max": 255},
  "packet": {"max_tokens_per_packet": 48, "proto_mode": "first"},
  "window": {"window_seconds": 20.0, "stride_seconds": 10.0, "max_packets": 2048, "overflow": "recent",
             "min_packets": 8, "macro_window_seconds": null},
  "input": {"dtype": "float32", "shape": "(batch, [macro,] packets, max_tokens_per_packet + 1)", "channels": "…"}
}
```

`FeatureSpec.load(path, require_tokenizer=True)` checks that `tokenizer.json` next to the spec hashes to
`tokenizer.sha256` (`FeatureSpecMismatch` otherwise) and `RawTokenizer.load` that the file follows the
layout (every reserved token has its id, no extra reserved token). Version 2 makes a runtime that only
knows aggregate / token-sequence models refuse the spec instead of feeding the tensor to the wrong graph.

`model_card.json` of a raw-byte model: `input_kind: "raw_bytes"`, `tokenizer` (`file`, `sha256`), `window`,
`packet`, plus the usual fields (`candidate_type` is `<candidate>_calibrated`). `models/<name>/<version>/`
also holds `tokenizer.json`.

## 10. What the scorer has to do

`corvus-score` (`services/src/corvus_services/scoring/`) implements this; the steps below are the contract and
name where each one lives. A scorer that serves a raw-byte model must:

1. Dispatch on `model_card.input_kind == "raw_bytes"` (or `feature_spec.json` having `raw_bytes`), **not**
   on the ONNX input rank: a token-sequence model has the same rank 3.
2. Validate with `FeatureSpec.load(dir, require_tokenizer=True)` and load `tokenizer.json` with
   `corvus_ml.raw.tokenizer.RawTokenizer.load(dir, spec.raw_bytes)`. The `serve` extra contains
   `tokenizers` (a Rust wheel of a few MB).
3. Receive the **payloads**, not only the decoded events. Online, the `CaptureBatch` frames carry them;
   decode them like ingest does (`payload` and `payload_original_size` per frame) and map the frame to
   direction, packet type and time as `codec.event_row` does.
4. Keep per (session, segment) the packets of the last `window_seconds` (bounded by `max_packets` with
   the configured `overflow`), tokenize each frame once with `session_packet_table` semantics
   (`[DT_*]` from the gap to the previous frame of the segment, `[NOPAYLOAD]` / `[TRUNC]` as in section
   3), and cut windows on the same grid (`window_bounds`: anchored at the first packet of the segment,
   every `stride_seconds`).
5. Assemble the rows with `corvus_ml.raw.windows.assemble_rows` / `stream_input` (the single code path
   used by training and evaluation) and the session's `client_protocol` for `[PROTO_*]`, pad packets to the
   longest window of the micro-batch, and run the graph. The packet count may vary per call.
6. For a macro model keep the last `macro_windows` windows of the segment (gaps stay padding).
7. Use the same code, not a re-implementation: the token ids are part of the model.

How `corvus-score` does it:

| Step | Implementation |
|---|---|
| 1, 2 | `ModelManager` reads `model_card.json` first. `input_kind` `raw_bytes` (or a `raw_bytes` section in the spec) selects the raw path; a card that contradicts the spec (`agg`/`seq` with a raw spec, `raw_bytes` without one, an unknown kind) rejects the model. `FeatureSpec.load(..., require_tokenizer=True)` and `RawTokenizer.load` check the hash and the layout; a mismatch keeps the model out (reason `rejected`, not retried), all other models keep running. The ONNX input rank (3, or 4 with macro windows) and the channel count `max_tokens_per_packet + 1` are checked against the spec. |
| 3 | `scoring/raw.py: raw_frame` maps the capture `Frame` (plaintext `payload`, `payload_original_size`, direction, packet type, offset). |
| 4, 6 | `corvus_ml.raw.streaming.SegmentWindower`, one per (session, segment, distinct `RawBytesSpec`). It shares `tokenize_payloads` and `cut_window` with the offline path; `tests/test_raw_streaming.py` proves stream = batch for chunked feeds, segments, caps, protocol modes and macro windows. A window is emitted as soon as a frame at or after its end arrived; the windows that are only complete at the end of the segment follow on a new segment, the end of the session or an idle eviction. A macro window is emitted when the first window of the next group is complete (like training: disjoint groups). |
| 5 | The `[PROTO_*]` token comes from `sessions.client_protocol` (`SessionStart.client_protocol_version`, or the catalog on a lookup). The micro-batch is padded to its longest window (`scoring/raw.py: stack_inputs`). |

Memory and privacy: the windower tokenizes a frame when it arrives and keeps token ids only (int16), from the start of
the next undecided window on, so about `window_seconds + stride_seconds` of traffic; a cap
(`CORVUS_SCORE_RAW_MAX_BUFFERED_FRAMES`) bounds it. Plaintext payload bytes live for the duration of one call and are
never stored, persisted or logged. A model that appears while a segment runs starts its window grid at its first frame
(the featurizers behave the same); a frame whose `seq` does not exceed the last one (redelivery) is ignored.

Metrics: `corvus_score_model_window_max_packets{model}`, `corvus_score_model_window_seconds{model}`,
`corvus_score_model_estimated_p99_ms{model}`, `corvus_score_model_estimated_capacity_players{model}`,
`corvus_score_consumer_pending`, `corvus_score_falling_behind`, `corvus_score_raw_window_packets` (packets per window handed to the
models) and `corvus_score_raw_frames_dropped_total`.

Cost: the table below was measured with default-size models (`d_model` 64, 2 global layers; random
weights, latency does not depend on them) on a 32-core development machine, ONNX Runtime CPU, one window
per call. The anomaly graph is slower because it runs the prediction heads for the masked positions.

| Packets per window | `raw_hier_classifier` 1 thread | 2 threads | `raw_hier_anomaly` 1 thread | 2 threads |
|---|---|---|---|---|
| 128 | 3.8 ms | 2.3 ms | 10.0 ms | 5.9 ms |
| 512 | 17.0 ms | 10.0 ms | 44.7 ms | 25.8 ms |
| 1024 | 41.9 ms (p99 47.6) | 25.6 ms | 108.7 ms | 66.9 ms |

A larger model (`d_model` 128, 4 layers) needs 68.9 ms (1 thread) for 1024 packets.

**Throughput, not latency.** Scores are asynchronous decision aids computed in the scorer (NATS consumer) and never
block the proxy, so a window may take longer than a few milliseconds, even seconds, which allows windows long enough
to show behaviour clearly. What matters is that the scorer keeps up with the incoming windows, otherwise the backlog
grows. With window stride S seconds and mean latency L, one core serves about `S / L` concurrent players (single
thread): stride 5 s and 50 ms are 100 players per core, a 5 s latency only 1.

**Sanity bound.** At load time the scorer estimates the p99 of a full window from `max_packets` (piecewise linear
through the 1-thread classifier points above, times 0.6 for two or more threads, times 2.6 for `raw_hier_anomaly`, times
1.15 from mean to p99; a larger model needs about 1.6 times more) and logs a warning if it exceeds
`CORVUS_SCORE_P99_BUDGET_MS` (default 5000, like the registry gate `CORVUS_API_GATE_MAX_P99_LATENCY_MS`). It only
catches absurd models; the model is still served and the estimate is exported as `corvus_score_model_estimated_p99_ms`.
Re-measure on the target hardware (`[benchmark] raw_packets = [128, 512, 1024]` adds this table to every report).

**Memory.** The activations of an ONNX run grow with the rows of the run and, per row, with the square of
`max_packets`: about 0.06 GiB per window at 512 packets, 0.12 GiB at 1024 and 0.26 GiB at 2048 (measured, section 7),
which is why a micro-batch of 64 windows at 2048 packets would need ~16 GiB. The scorer therefore splits the
micro-batch of a raw-byte model into ONNX runs of at most `budget / per-window estimate` rows (at least 1) and
concatenates the scores. The estimate is the inference formula of `corvus_ml.membudget`
(`3 * max(heads * (P + 1)^2 * 4, local_heads * T^2 * P * 4) + (6 + 2 * ffn_mult) * P * T * local_dim * 4`, times the
macro windows) evaluated for the default architecture, because `feature_spec.json` carries `max_packets`,
`max_tokens_per_packet` and the macro windows but not the layer sizes; it errs on the high side (about 0.30 GiB at 2048
packets), and a larger model needs more. The budget is `CORVUS_SCORE_MEMORY_FRACTION` (default 0.5) of the container
memory limit (cgroup `memory.max`), or `CORVUS_SCORE_MEMORY_BUDGET_MB` if set. Rows per run for the default model:

| `max_packets` | 1 GiB limit | 2 GiB limit |
|---|---|---|
| 512 | 8 | 17 |
| 1024 | 4 | 8 |
| 2048 | 1 | 3 |

At load the scorer logs the estimate and the rows per run and exports `corvus_score_model_estimated_window_bytes{model}`
and `corvus_score_model_max_rows{model}`. If one window alone exceeds the budget it logs an error naming the model
(give the pods more memory or retrain with a smaller `max_packets`) and still serves it one window per run. Feature
models are not affected. The chart gives the score pods a 2Gi limit for this reason.

**Capacity.** At load time the scorer also logs and exports `corvus_score_model_estimated_capacity_players{model}`:
`max(1, cpus // intra_op_threads) * stride_seconds / mean_latency`, with the mean from the same estimate (equal to
`cpus * stride / mean latency` with one thread per window) and the CPUs from `CORVUS_SCORE_CPUS` or the cgroup limit.
It assumes the model runs alone; several loaded models share the CPUs. Compare it with the concurrent players of a
replica, and cap `max_packets` or add replicas or CPUs when it is lower. The evaluation report has the same figure per
candidate as `players/core`.

**Lag.** The scorer polls the pending count of its capture consumer (`num_pending`, every
`CORVUS_SCORE_LAG_POLL_S`) into `corvus_score_consumer_pending` and logs a warning once, setting
`corvus_score_falling_behind` to 1, when it grew over `CORVUS_SCORE_LAG_GROWTH_POLLS` consecutive polls. Alert on a
rising `corvus_score_consumer_pending` as well; the per-replica pending count is the ground truth for "the scorer does
not keep up", the capacity gauge is a forecast.

## 11. Limits

- Memory: a window of 2048 packets by 48 tokens is 196 KiB as `int16`; the evaluation holds the whole
  dataset in memory (`max_windows` caps it, default 200 000 in jobs). Use `window_seconds` / `max_packets`
  to size it. Training memory beyond the dataset is bounded by the micro-batching of section 6
  ("Memory behaviour") and grows with `max_packets` squared.
- The tokenizer sees what the lake keeps: serverbound play payloads and the clientbound motion allowlist,
  truncated at 2048 bytes. Chat, signs and other private types never reach it.
- `raw` has a short retention (21 days by default). A raw-byte build of an old snapshot fails with "no raw
  files"; the packet table in `features/` survives, the encrypted source does not.
- Erasure: the packet table contains tokenized payload content. It is derived data of the `raw` table and
  is deleted with it: the job registers the prefix (`POST /api/v1/snapshots/{id}/artifacts`,
  `contains_raw: true`), `corvus-compact` deletes the registered prefix when a player of the snapshot is
  erased and marks the snapshot `needs_rebuild`, and `corvus-housekeeping` deletes it with the pool's raw
  retention. A rebuild drops erased players, whose DEK is gone. The tokenizer itself is trained on a payload
  sample and contains no player identifier.
- An unregistered prefix is invisible to erasure and retention, so register every build.

## 12. Variants for the tokenizer ablation

Every variant is an option of `ModelConfig` / the raw-byte candidates and **off by default**; a candidate
without these parameters is byte-for-byte the model of section 6 (same state dict, same score). The options
are candidate parameters, so `corvus-eval tune` can search them. A table `[candidates.<candidate>__<label>]`
runs `<candidate>` under its own name, so one run can hold several parameter sets of the same model (output
files, report rows and tuning studies use the full name; the tuning space is that of the candidate). The
tokenizer is part of the source, so plain bytes versus BPE is a separate run (see "Ablation of the raw-byte variants" in
[evaluate-models.md](../how-to/evaluate-models.md)).

| Variant | Parameters | What it does |
|---|---|---|
| Plain-byte tokenizer | `[raw.tokenizer] mode = "bytes"` | one token per byte, see section 3; recorded in the spec |
| Packet-type position | `type_pos`, `type_buckets` 64 | the position embedding of token `i` gets a second table indexed by (type bucket, `i`), added to the shared one, so the same byte offset of different packet types has different embeddings. The bucket of `[PKT_n]` is `1 + (n - 1) mod (type_buckets - 1)`; `[PKT_OTHER]`, `[MASK]` and padding are bucket 0, so a masked packet type is not leaked through the position. Cost: `type_buckets * max_tokens * local_dim` weights. A gather on a computed index, exports as plain ONNX. Works for the classifier too |
| MAE-style masking | `p_packet` (0 to 0.95, default 0.1), `packet_mask` `"partial"` / `"full"`, `p_token`, `span_mean` 1 | `p_packet` 0.3 to 0.9 masks that share of the packets. `"full"` replaces **all** byte tokens of a masked packet (default: the packet type and the first `first_tokens` only) and predicts them. `span_mean > 1` masks byte spans with a geometric length of that mean instead of independent positions; the share of masked bytes stays `p_token` |
| Entropy-weighted byte loss | `entropy_weight`, `entropy_score` false | masked-byte loss weight per (type bucket, offset) = inverse of the empirical entropy of that cell, `1 / (H + 0.25)`, normalised to mean 1 over seen cells; cells with fewer than 8 byte tokens, and types not seen in training, keep weight 1. The table (`byte_w`) is computed in `fit` from the training windows (`corvus_ml.raw.entropy`, deterministic, no sampling) and stored in the state dict. It weights the training loss only; with `entropy_score` the score is weighted too. Nothing is computed at inference |
| JEPA latent target | `objective` `"jepa"` / `"both"`, `jepa_loss` `"smooth_l1"` / `"cosine"`, `ema_momentum` 0.99 | masked packets (all bytes masked, time and direction kept) are predicted in representation space: target = the global-transformer output of an **EMA copy** of the encoder (`target`) on the unmasked window, no gradient, L2-normalised; predictor = a two-layer MLP on the context output of the masked packet. The EMA is updated at the start of every training step. The score is the mean latent error of the packets masked by the fixed score pattern, so the exported graph runs the online encoder on the masked and the target encoder on the unmasked window in one graph (no custom operators; Huber loss is written out because `smooth_l1_loss` has no ONNX operator). `"both"` adds the reconstruction terms. The byte heads are not built for `"jepa"`. `n_params` counts the target encoder, because it is in the exported graph |
| kNN combination | `combine` `"rank"` / `"zsum"`, `knn_bank` 256, `knn_k` 5, `knn_weight` 0.5 | after training, `fit_memory` draws `knn_bank` training windows with `run.seed`, stores their L2-normalised mean-pooled packet embeddings as a bank, and the distribution of the reconstruction score and of the leave-one-out kNN distance (mean cosine distance to the `knn_k` nearest) of those windows. Score = `(1 - knn_weight) * f(reconstruction) + knn_weight * f(knn)`; `f` is a smoothed empirical CDF against the stored scores (`rank`, in [0, 1]) or the z-score (`zsum`). In the ONNX graph the bank is a constant `[knn_bank, d_model]` matrix, one matmul and a TopK per window, plus one more encoder pass on the unmasked window. Memory and latency grow linearly with `knn_bank`; the default adds about `256 * d_model` floats. Without a fitted bank the score is the plain reconstruction score |

All of this is deterministic for a seed: masks and the bank use `torch` / `numpy` generators seeded from
`run.seed`, the entropy table has no randomness.

Latency: `benchmark.latency_budget_ms` (default 5000) is a sanity bound for the p99 per window on CPU, not a
real-time target. Together with `raw_packets = [..., 256]` the report prints parameters, p50, p99,
`players/core` (`stride_seconds / (mean latency * threads)`, see section 10) and `p99 <= budget` for every candidate and
every packet count.
