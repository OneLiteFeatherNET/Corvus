-- Per-pool retention of raw payloads (NFR-018). NULL: the deployment default
-- (CORVUS_RAW_RETENTION_DAYS, 21). The upper limit is enforced by the API
-- (CORVUS_API_RAW_RETENTION_MAX_DAYS) and clamped by housekeeping; the CHECK only keeps nonsense out.
ALTER TABLE corvus.pools
    ADD COLUMN raw_retention_days integer
    CHECK (raw_retention_days IS NULL OR raw_retention_days BETWEEN 1 AND 3650);
