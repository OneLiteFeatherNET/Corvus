# corvus-ml

Python tooling for Corvus. Currently: the pilot capture analyzer.

```
uv sync
uv run corvus-pilot analyze <captures-dir> [--json out.json] [--denylist denylist.json] [--workers N]
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

Protobuf bindings are generated from `../proto` into `src/corvus_ml/_gen/`
(gitignored) automatically on first import, or explicitly with
`scripts/gen_proto.sh`. See `../docs/pilot.md` for the pilot runbook.
