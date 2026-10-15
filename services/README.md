# corvus-services

Backend services for the Corvus capture pipeline: `corvus-ingest` (NATS JetStream to Parquet lake and
PostgreSQL catalog), `corvus-migrate` (catalog migrations) and `corvus-housekeeping` (lake cleanup, one-shot).

The contract is `docs/reference/data-lake.md`. Local run: `docs/how-to/run-ingest-locally.md`.

    uv sync --group proto --group dev
    uv run --group proto scripts/gen_proto.sh
    uv run pytest
