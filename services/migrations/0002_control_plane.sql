-- Control-plane schema v1, see docs/reference/control-plane-api.md. Never edit after release.

-- Declarative pool definitions: the filter is stored as JSON and translated to parameterised SQL.
CREATE TABLE corvus.pools (
    id          uuid        PRIMARY KEY,
    name        text        NOT NULL UNIQUE CHECK (length(name) BETWEEN 1 AND 200),
    description text        NOT NULL DEFAULT '',
    definition  jsonb       NOT NULL,
    created_by  text        NOT NULL,
    created_at  timestamptz NOT NULL,
    updated_at  timestamptz NOT NULL
);

-- Immutable dataset snapshots. No FK to pools: deleting a pool must not touch snapshots.
CREATE TABLE corvus.dataset_snapshots (
    id            uuid        PRIMARY KEY,
    pool_id       uuid        NOT NULL,
    pool_name     text        NOT NULL,
    manifest      jsonb       NOT NULL,
    sha256        text        NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    session_count integer     NOT NULL CHECK (session_count >= 0),
    file_count    integer     NOT NULL CHECK (file_count >= 0),
    created_by    text        NOT NULL,
    created_at    timestamptz NOT NULL
);

CREATE INDEX dataset_snapshots_pool_idx ON corvus.dataset_snapshots (pool_id, created_at DESC);

CREATE FUNCTION corvus.reject_snapshot_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'dataset snapshots are immutable' USING ERRCODE = 'integrity_constraint_violation';
END;
$$;

CREATE TRIGGER dataset_snapshots_immutable
    BEFORE UPDATE OR DELETE ON corvus.dataset_snapshots
    FOR EACH ROW EXECUTE FUNCTION corvus.reject_snapshot_mutation();

CREATE TABLE corvus.labels (
    id           uuid             PRIMARY KEY,
    subject_type text             NOT NULL CHECK (subject_type IN ('pseudonym', 'session')),
    pseudonym    bytea            CHECK (pseudonym IS NULL OR octet_length(pseudonym) = 32),
    session_id   bytea            CHECK (session_id IS NULL OR octet_length(session_id) = 16),
    label        text             NOT NULL CHECK (label IN ('legit', 'cheat')),
    source       text             NOT NULL
                                  CHECK (source IN ('honeypot', 'staff_verdict', 'ban_import', 'weak_rule')),
    confidence   double precision NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    valid_from   timestamptz,
    valid_to     timestamptz,
    note         text             NOT NULL DEFAULT '' CHECK (length(note) <= 2000),
    created_by   text             NOT NULL,
    created_at   timestamptz      NOT NULL,
    CHECK ((subject_type = 'pseudonym' AND pseudonym IS NOT NULL AND session_id IS NULL)
        OR (subject_type = 'session' AND session_id IS NOT NULL AND pseudonym IS NULL)),
    CHECK (valid_from IS NULL OR valid_to IS NULL OR valid_from < valid_to)
);

CREATE INDEX labels_pseudonym_idx ON corvus.labels (pseudonym) WHERE pseudonym IS NOT NULL;
CREATE INDEX labels_session_idx ON corvus.labels (session_id) WHERE session_id IS NOT NULL;
CREATE INDEX labels_created_idx ON corvus.labels (created_at DESC, id DESC);

-- Opt-out / erasure blocklist and explicit allowlist, one row per pseudonym.
CREATE TABLE corvus.blocklist (
    pseudonym  bytea       PRIMARY KEY CHECK (octet_length(pseudonym) = 32),
    kind       text        NOT NULL CHECK (kind IN ('block', 'allow')),
    source     text        NOT NULL CHECK (source IN ('manual', 'erasure')),
    reason     text        NOT NULL DEFAULT '' CHECK (length(reason) <= 500),
    created_by text        NOT NULL,
    created_at timestamptz NOT NULL
);

CREATE TABLE corvus.erasure_requests (
    id                 uuid        PRIMARY KEY,
    pseudonym          bytea       NOT NULL CHECK (octet_length(pseudonym) = 32),
    requested_by       text        NOT NULL,
    requested_at       timestamptz NOT NULL,
    status             text        NOT NULL CHECK (status IN ('completed')),
    player_known       boolean     NOT NULL,
    key_deleted        boolean     NOT NULL,
    compaction_pending boolean     NOT NULL
);

CREATE INDEX erasure_requests_pseudonym_idx ON corvus.erasure_requests (pseudonym);
CREATE INDEX erasure_requests_compaction_idx ON corvus.erasure_requests (requested_at)
    WHERE compaction_pending;

CREATE TABLE corvus.models (
    id             uuid        PRIMARY KEY,
    name           text        NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    version        text        NOT NULL CHECK (length(version) BETWEEN 1 AND 100),
    candidate_type text        NOT NULL,
    game_context   text        NOT NULL,
    domain         text        NOT NULL,
    snapshot_id    uuid        NOT NULL REFERENCES corvus.dataset_snapshots (id),
    metrics        jsonb       NOT NULL DEFAULT '{}'::jsonb,
    onnx_key       text        NOT NULL,
    stage          text        NOT NULL
                               CHECK (stage IN ('registered', 'shadow', 'canary', 'enforce', 'archived')),
    created_by     text        NOT NULL,
    created_at     timestamptz NOT NULL,
    UNIQUE (name, version)
);

CREATE INDEX models_enforce_scope_idx ON corvus.models (snapshot_id, game_context) WHERE stage = 'enforce';
CREATE INDEX models_created_idx ON corvus.models (created_at DESC, id DESC);

CREATE TABLE corvus.model_transitions (
    id         uuid        PRIMARY KEY,
    model_id   uuid        NOT NULL REFERENCES corvus.models (id),
    from_stage text        NOT NULL,
    to_stage   text        NOT NULL,
    actor      text        NOT NULL,
    reason     text        NOT NULL DEFAULT '',
    gate       jsonb,
    at         timestamptz NOT NULL
);

CREATE INDEX model_transitions_model_idx ON corvus.model_transitions (model_id, at);

CREATE TABLE corvus.jobs (
    id          uuid        PRIMARY KEY,
    type        text        NOT NULL CHECK (type IN ('build_features', 'evaluate', 'train')),
    snapshot_id uuid        NOT NULL REFERENCES corvus.dataset_snapshots (id),
    params      jsonb       NOT NULL DEFAULT '{}'::jsonb,
    status      text        NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    runner      text        NOT NULL,
    runner_ref  text,
    message     text        NOT NULL DEFAULT '',
    created_by  text        NOT NULL,
    created_at  timestamptz NOT NULL,
    updated_at  timestamptz NOT NULL,
    finished_at timestamptz
);

CREATE INDEX jobs_created_idx ON corvus.jobs (created_at DESC, id DESC);
