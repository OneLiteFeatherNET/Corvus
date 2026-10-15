-- Catalog schema v1, see docs/reference/data-lake.md section 4. Never edit after release.

CREATE SCHEMA IF NOT EXISTS corvus;

CREATE TABLE corvus.players (
    pseudonym   bytea       PRIMARY KEY,
    key_id      text        NOT NULL,
    first_seen  timestamptz NOT NULL,
    last_seen   timestamptz NOT NULL,
    erased_at   timestamptz
);

CREATE TABLE corvus.player_keys (
    pseudonym   bytea       PRIMARY KEY REFERENCES corvus.players (pseudonym) ON DELETE CASCADE,
    dek_wrapped bytea       NOT NULL,
    kek_id      text        NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE corvus.sessions (
    session_id         bytea       PRIMARY KEY CHECK (octet_length(session_id) = 16),
    -- NULL only for orphan sessions: batches whose SessionStart never arrived (no pseudonym, no DEK,
    -- events rows only). A late SessionStart adopts the row and clears the flag.
    pseudonym          bytea       REFERENCES corvus.players (pseudonym),
    orphan             boolean     NOT NULL DEFAULT false,
    proxy_id           text        NOT NULL,
    client_protocol    integer     NOT NULL,
    observed_protocol  integer     NOT NULL,
    plugin_version     text        NOT NULL,
    started_at         timestamptz NOT NULL,
    ended_at           timestamptz,
    dropped_queue_full bigint      NOT NULL DEFAULT 0 CHECK (dropped_queue_full >= 0),
    dropped_privacy    bigint      NOT NULL DEFAULT 0 CHECK (dropped_privacy >= 0),
    listener_errors    bigint      NOT NULL DEFAULT 0 CHECK (listener_errors >= 0),
    dropped_transport  bigint      NOT NULL DEFAULT 0 CHECK (dropped_transport >= 0),
    CHECK (orphan OR pseudonym IS NOT NULL)
);

CREATE INDEX sessions_started_at_idx ON corvus.sessions (started_at);
CREATE INDEX sessions_pseudonym_idx ON corvus.sessions (pseudonym);

CREATE TABLE corvus.segments (
    session_id     bytea       NOT NULL REFERENCES corvus.sessions (session_id) ON DELETE CASCADE,
    segment        integer     NOT NULL CHECK (segment >= 0),
    backend_server text        NOT NULL,
    game_context   text        NOT NULL,
    client_brand   text        NOT NULL,
    started_at     timestamptz NOT NULL,
    PRIMARY KEY (session_id, segment)
);

CREATE INDEX segments_game_context_idx ON corvus.segments (game_context);

CREATE TABLE corvus.lake_files (
    id             bigserial   PRIMARY KEY,
    s3_key         text        NOT NULL UNIQUE,
    table_name     text        NOT NULL CHECK (table_name IN ('events', 'raw')),
    date           date        NOT NULL,
    row_count      bigint      NOT NULL CHECK (row_count >= 0),
    size_bytes     bigint      NOT NULL CHECK (size_bytes >= 0),
    schema_version integer     NOT NULL DEFAULT 1,
    created_at     timestamptz NOT NULL DEFAULT now(),
    deleted_at     timestamptz
);

CREATE INDEX lake_files_table_date_idx ON corvus.lake_files (table_name, date);

-- No FK to sessions on purpose: compaction and erasure may remove sessions while files still exist.
CREATE TABLE corvus.lake_file_sessions (
    file_id    bigint NOT NULL REFERENCES corvus.lake_files (id) ON DELETE CASCADE,
    session_id bytea  NOT NULL CHECK (octet_length(session_id) = 16),
    PRIMARY KEY (file_id, session_id)
);

CREATE INDEX lake_file_sessions_session_idx ON corvus.lake_file_sessions (session_id);

-- Ingest bookkeeping (not part of the lake contract): one row per envelope whose counters were
-- applied. Makes counter updates idempotent against JetStream redelivery. Pruned by housekeeping.
CREATE TABLE corvus.session_batches (
    session_id  bytea       NOT NULL,
    msg_id      text        NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (session_id, msg_id)
);

CREATE INDEX session_batches_received_at_idx ON corvus.session_batches (received_at);
