-- Compaction after player erasure (corvus-compact), see docs/reference/data-lake.md section 7.

ALTER TABLE corvus.erasure_requests ADD COLUMN compacted_at timestamptz;

-- A compacted file is replaced by a new file (replaced_by) or, if nothing remained, by none.
-- purge_pending: the file row is marked deleted but its object still has to be removed from the
-- lake; compaction retries this until it succeeds (housekeeping treats the key as known).
ALTER TABLE corvus.lake_files ADD COLUMN replaced_by bigint REFERENCES corvus.lake_files (id);
ALTER TABLE corvus.lake_files ADD COLUMN purge_pending boolean NOT NULL DEFAULT false;

CREATE INDEX lake_files_purge_pending_idx ON corvus.lake_files (id) WHERE purge_pending;

-- Sessions of erased players. Snapshots are immutable and keep listing them; readers skip them.
CREATE VIEW corvus.erased_sessions AS
SELECT s.session_id, s.pseudonym, p.erased_at
FROM corvus.sessions s
JOIN corvus.players p ON p.pseudonym = s.pseudonym
WHERE p.erased_at IS NOT NULL;
