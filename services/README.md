# corvus-services

Backend services for the Corvus capture pipeline: `corvus-ingest` (NATS JetStream to Parquet lake and
PostgreSQL catalog), `corvus-score` (online scoring with ONNX Runtime, see `docs/how-to/run-scoring-locally.md`),
`corvus-migrate` (catalog migrations), `corvus-housekeeping` (lake cleanup, one-shot) and `corvus-api` (control-plane
API for the management UI, see `docs/reference/control-plane-api.md`).

The contract is `docs/reference/data-lake.md`. Local run: `docs/how-to/run-ingest-locally.md`.

    uv sync --group proto --group dev
    uv run --group proto scripts/gen_proto.sh
    uv run pytest
