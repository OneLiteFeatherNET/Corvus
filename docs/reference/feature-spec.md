# Reference: feature spec (`feature_spec.json`)

A feature spec pins everything a model depends on besides its weights: how the windows were cut,
which aggregates and token columns exist, and exactly how they are turned into model input
tensors. It is written next to the artifacts and checked on load, so a model is never silently
scored with features that differ from the ones it was trained on.

Code: `ml/src/corvus_ml/features/spec.py` (format and validation) and `model_input.py` (the
adapter that builds the tensors). Both import without Arrow, torch, scikit-learn or ONNX Runtime.

## 1. Where it is written and read

| Artifact | Written by | Read by |
|---|---|---|
| `<build out>/feature_spec.json` next to `windows/` and `window_tokens/` | `corvus-features build` | `corvus-eval` (window parameters of the data; validated) |
| `<eval out>/onnx/<game_context>/feature_spec.json` next to the exported `*.onnx` | `corvus-eval run` | the scoring service, before it loads a model |
| `models/<name>/<version>/feature_spec.json` next to `model.onnx` | the `train` job (copied from the export) | the scoring service |

The `train` job also writes `model_card.json` and `calibration.json` there; the feature spec stays
unchanged. What the model outputs is described in `model_card.json` (`output_semantics`), see
[ml-jobs.md](ml-jobs.md).

The file next to a model describes both input kinds (`agg`, `seq`); a model uses the one matching
its input. Normalisation (mean and std) is inside the ONNX graph and is not part of the spec.

## 2. Format (version 1)

```json
{
  "name": "corvus-features",
  "version": 1,
  "window": {
    "window": 256, "overlap": 0.5, "max_duration_s": 30.0, "min_frames": 16,
    "dt_edges_ms": [0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 35.0, 50.0, 75.0, 100.0, 200.0, 500.0, 1000.0, 5000.0],
    "entity_cache": 256
  },
  "aggregate_names": ["cps", "attacks_per_s", "packets_per_s", "movement_per_s", "rot_var",
                      "rot_speed_mean", "rot_speed_max", "on_ground_frac", "tick_end_rate"],
  "token_columns": ["packet_type", "direction", "event_kind", "domain", "action", "dt_bucket",
                    "log_dt", "has_pos_delta", "dx", "dy", "dz", "has_rot_delta", "dyaw", "dpitch",
                    "rot_speed", "rot_accel", "on_ground", "t_since_swing", "t_since_move",
                    "target_dist", "target_angle"],
  "model_inputs": {
    "agg": {"columns": ["cps", "attacks_per_s", "packets_per_s", "movement_per_s", "rot_var",
                        "rot_speed_mean", "rot_speed_max", "n_frames", "on_ground_frac",
                        "tick_end_rate", "has_on_ground_frac", "has_tick_end_rate"]},
    "seq": {
      "length": 256,
      "columns": [{"name": "event_kind", "transform": "scale8"}, "... 19 columns in total"],
      "mask": "last"
    }
  }
}
```

| Key | Meaning |
|---|---|
| `name`, `version` | Always `corvus-features`; `version` is bumped whenever the meaning of a feature or the input layout changes (a new token column, another transform, a changed formula). |
| `window` | The `FeaturizerConfig` the windows were produced with. |
| `aggregate_names` | Order of `CompletedWindow.aggregates` (float64, NaN = null). |
| `token_columns` | Column order of `CompletedWindow.tokens` (float32 matrix, one row per serverbound frame). |
| `model_inputs.agg.columns` | Column order of the aggregate input `(n, len(columns))`, float32. Nullable aggregates are 0 when null and followed by a `has_<name>` indicator (1 = present). `n_frames` is the window's frame count. |
| `model_inputs.seq` | Token input `(n, length, len(columns) + 1)`, float32, input name `x`. The transformed token columns are placed from position 0, zero padding follows, frames beyond `length` are cut, and the last channel is the mask (1 = real frame). Transforms: `id`, `slog` = `sign(x) * ln(1 + abs(x))`, `scale8` = `x / 8`. |

### Kind `raw_bytes` (version 2)

A model on tokenised raw packet bytes ([raw-byte-tokenizer.md](raw-byte-tokenizer.md)) has
`"version": 2` and a `raw_bytes` object inside `model_inputs`: the hash and size of `tokenizer.json`
(which must sit next to the spec), the token layout, the packet row width and the window configuration.
The aggregate and sequence sections are still written but unused. Because of the version, a runtime
that only knows version 1 refuses such a model (`FeatureSpecMismatch`, version) instead of feeding it
the wrong tensor. `FeatureSpec.load(path, require_tokenizer=True)` additionally compares the SHA-256 of
`tokenizer.json` with the recorded one.

## 3. Validation

`FeatureSpec.load(path)` parses the file and compares it with the spec of the running code:
`name`, `version`, `aggregate_names`, `token_columns` and both `model_inputs` layouts must be
equal. `window` and `length` of the file are authoritative; a service builds its
`SessionFeaturizer` from `spec.window`. `FeatureSpec.load(path, expected=...)` additionally
compares the window parameters and `seq_len` (for a service configured with explicit values).

Any difference raises `FeatureSpecMismatch` listing every difference, for example:

```
feature spec of the model does not match the runtime:
  - version: model has 1, runtime expects 2
  - token_columns: model has (...), runtime expects (...)
```

A missing, unparsable or structurally invalid file raises `FeatureSpecError`.

## 4. Using it

```python
from corvus_ml.features.featurizer import SessionFeaturizer
from corvus_ml.features.model_input import model_inputs
from corvus_ml.features.spec import FeatureSpec

spec = FeatureSpec.load(model_dir)               # raises on mismatch
featurizer = SessionFeaturizer(session_id, spec.window)
windows = [w for frame in frames for w in featurizer.feed(frame)]
x = model_inputs(windows, "seq", spec)           # feed to the ONNX graph as input "x"
```

Evaluation reads the offline Parquet tables and builds the same tensors with the same functions
(`aggregate_inputs`, `encode_tokens`, `scatter_frames`), and `tests/test_model_input.py` asserts that both
paths produce identical arrays for every candidate.
