-- Online scoring (docs/reference/data-lake.md section 6). Never edit after release.

-- Latest score per player, game context, domain and model; upserted by corvus-score.
CREATE TABLE corvus.scores_latest (
    pseudonym     bytea            NOT NULL CHECK (octet_length(pseudonym) = 32),
    game_context  text             NOT NULL,
    domain        text             NOT NULL,
    model_name    text             NOT NULL,
    model_version text             NOT NULL,
    stage         text             NOT NULL CHECK (stage IN ('shadow', 'canary', 'enforce')),
    score         double precision NOT NULL CHECK (score >= 0 AND score <= 1),
    smoothed      double precision NOT NULL CHECK (smoothed >= 0 AND smoothed <= 1),
    windows       integer          NOT NULL CHECK (windows >= 1),
    session_id    bytea            NOT NULL,
    segment       integer          NOT NULL,
    updated_at    timestamptz      NOT NULL,
    PRIMARY KEY (pseudonym, game_context, domain, model_name)
);

-- Append-only score history, pruned by corvus-housekeeping (CORVUS_SCORE_HISTORY_RETENTION_DAYS).
CREATE TABLE corvus.score_history (
    id                        bigserial        PRIMARY KEY,
    pseudonym                 bytea            NOT NULL CHECK (octet_length(pseudonym) = 32),
    session_id                bytea            NOT NULL,
    segment                   integer          NOT NULL,
    game_context              text             NOT NULL,
    domain                    text             NOT NULL,
    model_name                text             NOT NULL,
    model_version             text             NOT NULL,
    stage                     text             NOT NULL CHECK (stage IN ('shadow', 'canary', 'enforce')),
    score                     double precision NOT NULL,
    smoothed                  double precision NOT NULL,
    windows                   integer          NOT NULL,
    window_start_offset_nanos bigint           NOT NULL,
    window_end_offset_nanos   bigint           NOT NULL,
    scored_at                 timestamptz      NOT NULL
);

CREATE INDEX score_history_pseudonym_idx ON corvus.score_history (pseudonym, scored_at DESC, id DESC);
CREATE INDEX score_history_scored_at_idx ON corvus.score_history (scored_at);
