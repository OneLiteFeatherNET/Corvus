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
   curl -s localhost:9464/livez      # liveness, no dependencies
   curl -s localhost:9464/healthz    # readiness: nats, postgres, s3
   curl -s localhost:9464/metrics | grep corvus_ingest
   ```

4. Point the capture plugin at `nats://localhost:4222`. Parquet files appear in the `corvus-lake` bucket (RustFS console:
   <http://localhost:9001>) under `lake/events/` and `lake/raw/`; the catalog is in the `corvus` schema of the
   PostgreSQL database (`localhost:5432`, user `corvus`).

Clean up housekeeping candidates once with:

```sh
docker compose run --rm ingest corvus-housekeeping
```

## Run the control-plane API locally

The `api` service of the same stack serves the management API on <http://localhost:8080> (reference:
[control-plane-api.md](../reference/control-plane-api.md)). It starts after `migrate`, which also creates its tables.

1. Create its environment file. It is read by the `api` service only and is optional for the other services:

   ```sh
   cd deploy/dev
   cp .env.api.example .env.api
   ```

2. For a quick local try-out set a development token in `.env.api` (never do this on a shared host), for example with
   `openssl rand -hex 24`. For anything else configure the OIDC issuer, audience and JWKS URL instead. Set
   `CORVUS_PSEUDONYM_KEY` (the key the proxies use) if you want player lookup and erasure by UUID.

   ```sh
   CORVUS_API_DEV_TOKEN=<generated value>
   ```

3. Start the service and call it:

   ```sh
   docker compose up --build api
   curl -s localhost:8080/readyz
   curl -s -H "Authorization: Bearer $CORVUS_API_DEV_TOKEN" localhost:8080/api/v1/pools
   ```

   The interactive OpenAPI documentation is at <http://localhost:8080/docs>. Without the `docker compose` stack, run
   `uv run corvus-api` in `services/` with `CORVUS_PG_DSN` set; jobs then start local subprocesses
   (`CORVUS_API_JOB_RUNNER=local`).

## Unit and integration tests

```sh
cd services
uv sync --group proto --group dev
uv run pytest                 # unit tests, no Docker needed
uv run pytest -m integration  # needs the compose stack above (ports on localhost), includes the API SQL
```
