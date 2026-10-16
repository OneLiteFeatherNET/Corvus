# How to evaluate models with `corvus-eval`

Run the stage 3 harness against feature windows and labels, and read the report. The code is in
`ml/src/corvus_ml/eval/`.

```
cd ml
uv run corvus-eval run --config eval.toml --out reports/<name>
```

The output directory gets `report.json`, `report.md` and `onnx/<game_context>/<candidate>.onnx`
with `<candidate>.calibration.json` and `<candidate>.raw.onnx` (the graph without the calibration;
torch models also get `.int8.onnx`). `ml/eval.synthetic.toml` is a ready-to-run smoke config.

The control plane runs the same harness as the `evaluate` and `train` jobs; see
[ml-jobs.md](../reference/ml-jobs.md). Models on raw packet bytes (`raw_hier_classifier`,
`raw_hier_anomaly`) and the Optuna search (`corvus-eval tune`) are covered in sections 6 and 7.

## Learning rate: `auto`

Every torch candidate (`gru_autoencoder`, `cnn1d`, `raw_hier_classifier`, `raw_hier_anomaly`) takes
`lr = "auto"` (and the raw-byte models `pretrain_lr = "auto"`). Before the training a range test finds
the learning rate on a copy of the model; a number keeps the fixed behaviour. Optional parameters (with
the prefix `pretrain_` for the pretraining): `lr_steps` (default 60), `lr_rule` (`valley` or
`steepest`), `lr_mult` (factor on the suggestion, what Optuna tunes), `lr_schedule` (`cosine` or `wsd`).

```
uv run corvus-eval lr-find --config eval.toml --out reports/lr [--steps 100]
```

prints the suggestion and the schedule per game context and candidate and writes
`<out>/lr/<context>__<candidate>.json` and `.csv` (`lr,smoothed_loss`; plus a `.png` if matplotlib
happens to be installed). `run` and `tune` put the same information in `report.md` ("Learning rate")
and `report.json` (`training.lr_plan`, `training.pretrain_lr_plan`), and the `evaluate` / `train`
jobs return it in `result.contexts.<ctx>.candidates.<name>.training`. How it works and what its limits
are: [learning-rate-finder.md](../explanation/learning-rate-finder.md).

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
A label stratum needs at least 2 players to put one in the test split and at least 3 to fill all
three parts. With fewer players the val / test split is empty: the candidate is not scored on it,
the report lists it under "No test metrics", and the ONNX parity check and benchmark use the
windows of the first non-empty split (test, val, train).
For smoke tests with few players, `run.split_by = "session"` splits by session instead. The same
player can then be in train and test (leakage), so the report carries a warning banner and the
numbers must not be compared with player-split results. It cannot be combined with `[tune]`.

## 2. Config reference (`eval.toml`)

```toml
name = "pilot-2026-10"

[run]
seed = 1337              # numpy, torch, sklearn and the split
threads = 1              # torch.set_num_threads and ONNX Runtime intra-op threads
seq_len = 256            # frames per window fed to sequence models (longer: cut, shorter: padded)
unlabelled = "exclude"   # or "assume_legit"
calibration = "auto"     # auto | isotonic | platt | ecdf | none, see "Calibration" below
contexts = []            # empty = every game_context in the data
split = [0.6, 0.2, 0.2]  # train / val / test share of players
# split_by = "player"    # or "session" (leakage-prone, smoke tests with few players only)
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
`gru_autoencoder`, `cnn1d`, and the raw-byte models `raw_hier_classifier` and `raw_hier_anomaly`
(section 6; they need a `[raw]` table and cannot share a run with the others).

Candidates train on different subsets: supervised ones (`gradient_boosting`, `cnn1d`) on labelled
windows and are skipped when the training split lacks one of the classes; unsupervised ones
(`isolation_forest`, `gru_autoencoder`) on windows that are legit (or assumed legit). Early
stopping uses the validation split. Scores are "higher = more likely cheat".

### Calibration

Every exported graph outputs a score in [0, 1], not the raw model output. The calibration is fitted
on the **validation** split and embedded in the ONNX graph, which then has one output named
`score`:

| Candidate | Method (`auto`) | Meaning of the score |
|---|---|---|
| supervised (`gradient_boosting`, `cnn1d`) | isotonic regression with at least 20 windows per class and 200 in total, else Platt scaling | estimated P(cheating, window) |
| unsupervised (`isolation_forest`, `gru_autoencoder`) | empirical CDF of the raw score of the legit (or assumed legit) validation windows | share of legit windows that score lower: 0.99 is higher than 99 % of legit windows |

The map is monotone, so it never reorders windows; isotonic regression and the CDF can merge
neighbouring raw values into ties. All metrics in the report (including ECE and the confusion
matrices) are computed on the calibrated score, the thing that is deployed. `none` exports the
raw graph (benchmark use only; the jobs refuse it). Without legit validation windows an
unsupervised candidate is calibrated on its training windows (`fit_on: "train"` in the report).
The calibration, its method and the number of windows it was fitted on are in
`<candidate>.calibration.json` and the `calibration` entry of the candidate in `report.json`.

## 3. Reading the report

Per `game_context` one table row per candidate, all on the **test** split:

- `ROC-AUC`, `PR-AUC` (average precision), per window.
- `R@1%`, `R@0.1%`: recall at a false-positive rate of 1 % / 0.1 %, per window (`win`) and per
  player (`player`, max window score per player; a player is a cheater if any window is). A `*`
  marks an operating point that the test split cannot resolve (fewer than 1 / FPR legit windows
  or players). Do not trust those cells beyond "upper bound".
- `ECE`: expected calibration error, 10 equal-width bins, supervised candidates only (the
  unsupervised scores are percentiles of the legit distribution, not probabilities).
- `p50 ms`, `p99 ms`: single-window ONNX Runtime CPU latency at the configured thread count.
  `players/core`: how many concurrent players one core keeps up with, `stride_seconds / (mean
  latency * threads)` (`stride / mean latency` with the default of one thread; for example stride
  5 s and 50 ms is 100 players per core, 5 s latency is 1). The stride is the one of the raw-byte
  window; candidates without raw windows show `-`. Scores are computed asynchronously, so latency
  is no real-time target: a window may take seconds, what counts is that the scorer keeps up. `batch w/s`: throughput at the batch size. `RSS MB`: peak resident
  set of the benchmark process (`model RSS delta` in the JSON is the growth caused by the model).
- `ONNX diff`: maximum absolute difference between ONNX and native (calibrated) scores on the test
  windows; `parity_ok` in the JSON applies `rtol=1e-3, atol=2e-3` (the calibration interpolates in
  float32 next to steep steps). The raw graph is checked separately (`onnx_raw`).

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
(`"agg"`, `"seq"` or `"raw_bytes"`), implement `fit`, `score`, `export_onnx`, `n_params` (and `onnx_to_score` if
the graph does not output the score first), decorate with `@register`, and import the module in
`candidates/__init__.py`. Config, split, metrics, ONNX parity, benchmark and report pick it up.

Optional class attributes: `tunable = True` makes the candidate searchable by `corvus-eval tune`
(its `fit` must pass `self.on_epoch` to the training loop and `corvus_ml.eval.tune.DEFAULT_SPACES` needs
an entry), `pretrains = True` hands `fit` the unlabelled windows of the training players as `pool`, and
`parity_rows` limits the number of test windows used for the ONNX parity check.

## 6. Raw-byte models

`raw_hier_classifier` (supervised) and `raw_hier_anomaly` (self-supervised, masked-prediction score)
read tokenised raw packet bytes instead of windows and token features; the format and the model are in
[raw-byte-tokenizer.md](../reference/raw-byte-tokenizer.md). A run uses either these candidates or the
others, because they cut their own time windows:

```toml
[raw]
dir = "lake/raw"          # features/<snapshot>/raw as built by build_features kind=raw_bytes
# or: [raw.synthetic] (below)
window_seconds = 20.0     # any window key overrides what the build recorded
stride_seconds = 10.0
max_packets = 2048
overflow = "recent"       # or "uniform"
min_packets = 8
# macro_window_seconds = 60.0   # second-level model over consecutive windows (stride == window)
max_windows = 100000      # random cap on the number of windows (memory)

[raw.tokenizer]           # only for [raw.synthetic]: the tokenizer is trained on the synthetic packets
vocab_size = 1024
[raw.packet]
max_tokens_per_packet = 48

[candidates.raw_hier_classifier]
epochs = 10
lr = "auto"
d_model = 64
layers = 2
pretrain_epochs = 5       # masked pretraining on all windows of the training players first
# init_from = "pretrain/pvp.pt"   # or start from a checkpoint (corvus-eval pretrain)
[candidates.raw_hier_anomaly]
epochs = 10

[benchmark]
raw_packets = [128, 512, 1024]    # extra single-window latency table at these packet counts
```

Without `[raw]` the labels come from `[data] labels`; with `dir` the build's `sessions.parquet`
(`session_id`, `pseudonym`, `started_at`, `client_protocol`) and `labels.parquet` are used. The report
states the tokenizer hash and the window configuration. Each exported model directory
(`onnx/<context>/`) holds `feature_spec.json` (version 2, `raw_bytes` section) and `tokenizer.json`
next to the graphs.

Pretraining on its own: `uv run corvus-eval pretrain --config eval.toml --out reports/pre` writes
`reports/pre/pretrain/<game_context>.pt`; finetune with `init_from`. The model shape comes from the
parameters of the first candidate in the config.

`ml/eval.synthetic-raw.toml` is a smoke config on synthetic packets with plausible movement layouts
(protocol differences, three cheat variants). Like every synthetic run it proves the code runs and says
nothing about real players. The anomaly model in particular does not separate the toy cheaters, which
are more regular than the toy legit players; a reconstruction loss does not see regularity.

### Ablation of the raw-byte variants

To compare the current model with plain bytes, packet-type positions, MAE-style masking with the
entropy-weighted loss, and JEPA on the same data and split, use two configs that differ only in
`[raw.tokenizer]` (the tokenizer belongs to the source, see
[raw-byte-tokenizer.md](../reference/raw-byte-tokenizer.md), section 12):

```toml
# ablation-bytes.toml (ablation-bpe.toml: mode = "bpe", vocab_size = 4096, max_tokens_per_packet = 48)
[raw]
dir = "lake/raw"
window_seconds = 10.0
stride_seconds = 10.0
max_packets = 256

[raw.tokenizer]            # only for [raw.synthetic]; with dir the build's tokenizer is used
mode = "bytes"
[raw.packet]
max_tokens_per_packet = 96

[candidates.raw_hier_anomaly__base]       # current model
epochs = 12
[candidates.raw_hier_anomaly__typepos]    # + packet-type position embedding
epochs = 12
type_pos = true
[candidates.raw_hier_anomaly__mae]        # + 50 % full-packet masks, byte spans, entropy weights
epochs = 12
type_pos = true
p_packet = 0.5
packet_mask = "full"
span_mean = 3.0
entropy_weight = true
[candidates.raw_hier_anomaly__jepa]       # latent prediction instead of bytes
epochs = 12
type_pos = true
objective = "jepa"
p_packet = 0.5
[candidates.raw_hier_anomaly__jepa_knn]   # + kNN memory bank in the score
epochs = 12
type_pos = true
objective = "jepa"
p_packet = 0.5
combine = "rank"

[benchmark]
raw_packets = [64, 256]
latency_budget_ms = 5000.0   # sanity bound for p99, not a real-time target
```

```
uv run corvus-eval run --config ablation-bpe.toml   --out reports/ablation-bpe
uv run corvus-eval run --config ablation-bytes.toml --out reports/ablation-bytes
```

For a real snapshot, `[raw] dir` points to a build made with `tokenizer_mode = "bpe"` and one made with
`"bytes"` (two `build_features` jobs, or `train_tokenizer` once per mode and `tokenizer_prefix`). Both runs
use the same seed and the same player split, so the rows are comparable. Read each report for ROC/PR-AUC
and recall at the FPR targets, the parameter count, and p50/p99 latency; the column `p99 <= 5000 ms` marks the
sanity bound (`benchmark.latency_budget_ms`), `players/core` the throughput, and the table "CPU latency per window by packet count" shows it at 64 and 256 packets.
`corvus-eval tune` searches the same candidates (`[tune.space] p_packet = [0.3, 0.9]` adds a mask-ratio
range). `ml/eval.ablation-raw-bpe.toml` and `ml/eval.ablation-raw-bytes.toml` are runnable smoke versions on
synthetic packets; like every synthetic run they prove the variants train, export and fit the latency
budget, and say nothing about detection quality.

## 7. Hyper-parameter search (`corvus-eval tune`)

```
uv run corvus-eval tune --config eval.toml --out reports/tuned [--trials 30]
```

For every game context and every tunable candidate (the raw-byte models, `gru_autoencoder`, `cnn1d`) an
Optuna study searches learning rate (with `lr = "auto"` a multiplier `lr_mult` of 0.3 to 3 around the found value), weight decay and the size parameters (for the raw-byte models also
`d_model`, `layers`, `dropout`, `window_seconds` and `max_packets`) with a TPE sampler and a median pruner
and the seed `run.seed`. The objective is the best validation loss, so the search never sees the test
split. The candidate is then evaluated with the best parameters; the report gets a "Hyper-parameter
search" section with every trial, and `report.json` the same under `contexts.<ctx>.tune`.

```toml
[tune]
trials = 20               # trials per study
timeout_s = 600           # optional
storage = ""              # "" = CORVUS_OPTUNA_STORAGE, else sqlite file <out>/tune/optuna.db; or "memory", or a URL
tune_windows = true       # raw-byte models: also search the window (rebuilds the windows per trial)
[tune.space]              # optional: narrow or replace a range
lr = [0.0005, 0.005]      # [low, high] keeps the kind (log / int / float) of the default
d_model = [32, 64]        # any other list is a set of choices
```

Set small `epochs` in the candidate tables while searching; each trial is a full fit. With a persistent
storage an existing study is resumed until it holds `trials` trials. In a job, pass `"tune": true` or the
object above as a parameter of `evaluate` / `train` ([ml-jobs.md](../reference/ml-jobs.md); the API has no
separate job type).
