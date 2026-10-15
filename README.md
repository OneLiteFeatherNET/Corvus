# Corvus

ML-based anti-cheat data collector and scorer for the OneLiteFeather network.

A Velocity plugin passively records player packets via PacketEvents, an ingest service stores them
pseudonymised in a data lake (Parquet on S3/MinIO, catalog in PostgreSQL), curated pools feed model
training, and a CPU-only scoring service publishes a per-player "likely cheating" score.

Corvus never sanctions players on its own; the score is a decision aid for staff.

## Layout

| Path | Content |
|---|---|
| `proto/` | Protobuf schemas shared by Java and Python |
| `plugin/` | Velocity capture plugin (Java) |
| `services/` | Python services: ingest, enrich, score |
| `ml/` | Python: evaluation, training, pilot analysis |
| `ui/` | Management UI (Nuxt, Material Design 3) |
| `deploy/` | Docker Compose (pilot/standalone), Helm |

Requirements and architecture decisions (ADR-CORV-*) live in the OneLiteFeather Outline wiki.
