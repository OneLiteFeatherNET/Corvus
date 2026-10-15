# How to evaluate models with `corvus-eval`

Run the stage 3 harness against feature windows and labels, and read the report. The code is in
`ml/src/corvus_ml/eval/`.

```
cd ml
uv run corvus-eval run --config eval.toml --out reports/<name>
```

The output directory gets `report.json`, `report.md` and `onnx/<game_context>/<candidate>.onnx`
(plus `.int8.onnx` for the torch models). `ml/eval.synthetic.toml` is a ready-to-run smoke config.

## 1. Inputs

| Table | Source | Needed columns |
|---|---|---|
| `windows` | `corvus-features build` | see [features.md](../reference/features.md) section 3 |
| `window_tokens` | `corvus-features build` | only for sequence candidates (`gru_autoencoder`, `cnn1d`) |
| `sessions` | catalog export (Parquet or CSV) | `session_id`, `pseudonym`, optional `started_at` |
| `labels` | honeypot runs, staff verdicts (Parquet or CSV) | `pseudonym` or `session_id`, `label`, `source`, `confidence`, `valid_from`, `valid_to` |

In CSV files ids are hex strings and times are ISO 8601. Empty `valid_from` / `valid_to` mean an
open bound. A label row sets exactly one of `pseudonym` and `session_id`; `label` is `legit` or
`cheat`.

**Label join.** A label applies to a window when the window start
(`sessions.started_at + windows.start_offset_nanos`) lies in `[valid_from, valid_to)`. Without
`started_at` only labels with no bounds apply. If several labels match, the highest `confidence`
wins; on a tie `cheat` wins. Rows below `run.min_confidence` are ignored. Windows without a valid
label stay unlabelled. Windows whose session has no pseudonym are dropped and logged.

**Unlabelled windows.** With `run.unlabelled = "exclude"` (default) they are neither trained on
nor scored. With `"assume_legit"` they count as legit everywhere (training and evaluation) and
the report states how many were assumed. Use it only when you accept that unlabelled traffic is
overwhelmingly legit; it biases recall at low FPR optimistically if cheaters hide in there.

**Split.** Players (pseudonyms) are split into train / val / test, never windows. The split is
stratified by player label (cheat, legit, unlabelled), shuffled with `run.seed`, and asserted to
have no player in two parts. The pipeline runs once per `game_context`, with its own split.

## 2. Config reference (`eval.toml`)

```toml
name = "pilot-2026-10"

[run]
seed = 1337              # numpy, torch, sklearn and the split
threads = 1              # torch.set_num_threads and ONNX Runtime intra-op threads
seq_len = 256            # frames per window fed to sequence models (longer: cut, shorter: padded)
unlabelled = "exclude"   # or "assume_legit"
contexts = []            # empty = every game_context in the data
split = [0.6, 0.2, 0.2]  # train / val / test share of players
fpr_targets = [0.01, 0.001]
min_confidence = 0.0
learning_curve = [0.1, 0.25, 0.5, 1.0]  # empty list disables it

[data]                   # paths relative to this file; or use [synthetic] instead
windows = "lake/windows"
tokens = "lake/window_tokens"
sessions = "catalog/sessions.parquet"
labels = "labels.csv"

[candidates.isolation_forest]    # one table per candidate; keys are that candidate's parameters
n_estimators = 200
[candidates.gradient_boosting]
max_iter = 200
learning_rate = 0.1
[candidates.gru_autoencoder]
epochs = 30
batch_size = 64
lr = "auto"              # or a number; "auto" runs the LR range test and uses its suggestion
hidden = 64
latent = 32
patience = 5
[candidates.cnn1d]
epochs = 30
batch_size = 64
lr = 0.002
channels = 32

[benchmark]
enabled = true
warmup = 20
iters = 200              # single-window timings behind p50 / p99
batch = 64               # batch for the throughput number
isolated = true          # one fresh process per model so peak RSS is that model's own
quantize = true          # int8 dynamic quantisation variant of the torch models
```

Unknown keys are errors. Candidate names: `isolation_forest`, `gradient_boosting`,
`gru_autoencoder`, `cnn1d`.

Candidates train on different subsets: supervised ones (`gradient_boosting`, `cnn1d`) on labelled
windows and are skipped when the training split lacks one of the classes; unsupervised ones
(`isolation_forest`, `gru_autoencoder`) on windows that are legit (or assumed legit). Early
stopping uses the validation split. Scores are "higher = more likely cheat".

## 3. Reading the report

Per `game_context` one table row per candidate, all on the **test** split:

- `ROC-AUC`, `PR-AUC` (average precision), per window.
- `R@1%`, `R@0.1%`: recall at a false-positive rate of 1 % / 0.1 %, per window (`win`) and per
  player (`player`, max window score per player; a player is a cheater if any window is). A `*`
  marks an operating point that the test split cannot resolve (fewer than 1 / FPR legit windows
  or players). Do not trust those cells beyond "upper bound".
- `ECE`: expected calibration error, 10 equal-width bins, supervised candidates only (the
  unsupervised scores are not probabilities).
- `p50 ms`, `p99 ms`: single-window ONNX Runtime CPU latency at the configured thread count; the
  target is under 10 ms p99. `batch w/s`: throughput at the batch size. `RSS MB`: peak resident
  set of the benchmark process (`model RSS delta` in the JSON is the growth caused by the model).
- `ONNX diff`: maximum absolute difference between ONNX and native scores on the test windows;
  `parity_ok` in the JSON applies `rtol=1e-3, atol=1e-4`.

Further sections: the int8 variant (metric deltas against fp32, latency, file size), the confusion
matrix on test at the threshold that gives the target FPR **on validation**, and the learning
curve (metrics against the share of training players). A curve that is still rising at 100 % says
more data would help; use it to estimate how much labelled data is needed.

Latency and RSS depend on the host, load and thread count. Compare numbers only from the same
machine and config.

## 4. Synthetic data

`corvus_ml.eval.synthetic` generates windows, tokens, sessions and labels in the real schemas,
for tests and smoke runs. Put a `[synthetic]` table in the config instead of `[data]`
(`n_legit`, `n_cheat`, `n_unlabelled`, `sessions_per_player`, `windows_per_session`,
`frames_per_window`, `contexts`, `cheat_strength`, `cheat_window_frac`). The generator models
toy "human" and "cheat" input (Gaussian rotation noise with per-player sensitivity versus aim snaps,
steady packet cadence and click rhythm). **Its results prove the code runs; they say nothing about
real players**, and reports made from it carry a banner saying so.

## 5. Adding a candidate

Subclass `corvus_ml.eval.candidates.base.Candidate`, set `name`, `supervised`, `input_kind`
(`"agg"` or `"seq"`), implement `fit`, `score`, `export_onnx`, `n_params` (and `onnx_to_score` if
the graph does not output the score first), decorate with `@register`, and import the module in
`candidates/__init__.py`. Config, split, metrics, ONNX parity, benchmark and report pick it up.
