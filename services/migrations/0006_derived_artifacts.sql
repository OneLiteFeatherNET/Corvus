-- Derived artefacts of raw payloads (tokenised raw-byte features), see docs/reference/data-lake.md
-- section 7. They are personal data derived from `raw`: erasure and retention have to reach them.

-- One row per prefix a job wrote below the lake bucket and registered through the API.
-- contains_raw: the prefix holds tokenised payload content (the packet table), not only aggregates.
CREATE TABLE corvus.derived_artifacts (
    id            uuid        PRIMARY KEY,
    snapshot_id   uuid        NOT NULL REFERENCES corvus.dataset_snapshots (id),
    job_id        uuid        REFERENCES corvus.jobs (id),
    kind          text        NOT NULL CHECK (kind ~ '^[a-z][a-z0-9_]{0,39}$'),
    s3_prefix     text        NOT NULL CHECK (s3_prefix ~ '^(features|tokenizers)/.+/$'
                                              AND s3_prefix !~ '(^|/)\.\.?(/|$)'),
    contains_raw  boolean     NOT NULL,
    created_at    timestamptz NOT NULL,
    deleted_at    timestamptz,
    deleted_reason text       CHECK (deleted_reason IN ('erasure', 'retention')),
    UNIQUE (snapshot_id, s3_prefix),
    CHECK ((deleted_at IS NULL) = (deleted_reason IS NULL))
);

CREATE INDEX derived_artifacts_live_raw_idx ON corvus.derived_artifacts (snapshot_id)
    WHERE deleted_at IS NULL AND contains_raw;

-- Mutable state next to the immutable dataset_snapshots (its trigger rejects every UPDATE):
-- needs_rebuild is set when erasure deleted a snapshot's raw-derived artefacts. A rebuild by a job
-- that started after needs_rebuild_at clears it.
CREATE TABLE corvus.snapshot_state (
    snapshot_id      uuid        PRIMARY KEY REFERENCES corvus.dataset_snapshots (id),
    needs_rebuild    boolean     NOT NULL,
    needs_rebuild_at timestamptz,
    reason           text        NOT NULL DEFAULT ''
);
