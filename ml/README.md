# corvus-ml

Python tooling for Corvus: the pilot capture analyzer, the feature stage and the model evaluation
harness.

The core install needs only numpy, pyarrow and protobuf (plus grpcio-tools for the bindings).
torch, scikit-learn, onnx and skl2onnx live in the optional extra `eval`; ONNX Runtime is in `eval`
and in `serve`. In this checkout `uv sync` installs the `eval` extra through the `dev` group, so
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
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

The privacy check uses `../proto/corvus/capture/v1/vocabulary.json` (exported by the plugin) as its
denylist and payload policy by default; see `../docs/pilot.md`.

Protobuf bindings are generated from `../proto` into `src/corvus_ml/_gen/`
(gitignored) automatically on first import, or explicitly with
`scripts/gen_proto.sh`. See `../docs/pilot.md` for the pilot runbook.
