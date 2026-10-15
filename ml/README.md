# corvus-ml

Python tooling for Corvus. Currently: the pilot capture analyzer.

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
