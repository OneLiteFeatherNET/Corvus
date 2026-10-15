# corvus-ml

Python tooling for Corvus: the pilot capture analyzer, the feature stage and the model evaluation
harness.

The core install needs only numpy, pyarrow and protobuf; grpcio-tools (protoc) is a build-time
tool in the dependency group `proto` and not installed at runtime.
torch, scikit-learn, onnx and skl2onnx (plus tokenizers, optuna, cryptography and psycopg for the
raw-byte models) live in the optional extra `eval`; ONNX Runtime is in `eval` and in `serve`
(`serve` also has `tokenizers`). In this checkout `uv sync` installs the `eval` extra through the `dev` group, so
tests and CI (`uv sync --locked`) cover everything. Outside the repo:

```
pip install corvus-ml                # features, pilot analyzer, FeatureSpec, model-input adapter
pip install 'corvus-ml[eval]'        # + corvus-eval (training, ONNX export, benchmark)
pip install 'corvus-ml[serve]'       # + onnxruntime for the scoring service
uv sync --no-default-groups          # in this checkout: core install only
```

```
uv sync
uv run corvus-pilot analyze <captures-dir> [--json out.json] [--vocabulary vocabulary.json]
    [--payload-policy none|serverbound|all] [--denylist extra.json] [--workers N]
uv run corvus-features build --events <lake/events> --segments <segments.parquet|csv> --out <dir>
    [--context pvp] [--window 256] [--overlap 0.5] [--workers N]
uv run corvus-job <build_features|evaluate|train>   # what the control plane starts, see below
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

Models on tokenised raw packet bytes (`raw_hier_classifier`, `raw_hier_anomaly`, `corvus-eval tune`):
`../docs/reference/raw-byte-tokenizer.md`.

`corvus-job` reads the job contract from the environment (`CORVUS_JOB_*`, `CORVUS_API_*`,
`CORVUS_S3_*`) and reports its status to the control plane; see
`../docs/reference/ml-jobs.md`. The container image is built from `ml/Dockerfile` with the
repository root as context:

```
docker build -f ml/Dockerfile -t corvus-ml .
```

The privacy check uses `../proto/corvus/capture/v1/vocabulary.json` (exported by the plugin) as its
denylist and payload policy by default; see `../docs/pilot.md`.

Protobuf bindings are generated from `../proto` into `src/corvus_ml/_gen/` (gitignored). In a
checkout (the `dev` group includes `proto`) that happens automatically on first import; images and
wheels get them at build time with `scripts/gen_proto.sh`, so the runtime install needs no
`grpcio-tools`. See `../docs/pilot.md` for the pilot runbook.
