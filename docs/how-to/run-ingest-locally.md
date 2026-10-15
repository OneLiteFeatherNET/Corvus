# How to run the ingest service locally

Runs NATS JetStream, an S3-compatible object store (RustFS), PostgreSQL and the ingest service with Docker Compose. The contract the service
implements is in [data-lake.md](../reference/data-lake.md).

1. Create the environment file and fill in the secrets (the file explains how to generate each value):

   ```sh
   cd deploy/dev
   cp .env.example .env
   ```

2. Start the stack. `migrate` applies the catalog migrations once, `s3-init` creates the bucket, then `ingest` starts:

   ```sh
   docker compose up --build
   ```

3. Check that the service is ready and exporting metrics:

   ```sh
   curl -s localhost:9464/healthz
   curl -s localhost:9464/metrics | grep corvus_ingest
   ```

4. Point the capture plugin at `nats://localhost:4222`. Parquet files appear in the `corvus-lake` bucket (RustFS console:
   <http://localhost:9001>) under `lake/events/` and `lake/raw/`; the catalog is in the `corvus` schema of the
   PostgreSQL database (`localhost:5432`, user `corvus`).

Clean up housekeeping candidates once with:

```sh
docker compose run --rm ingest corvus-housekeeping
```

## Unit and integration tests

```sh
cd services
uv sync --group proto --group dev
uv run pytest                 # unit tests, no Docker needed
uv run pytest -m integration  # needs the compose stack above (ports on localhost)
```
