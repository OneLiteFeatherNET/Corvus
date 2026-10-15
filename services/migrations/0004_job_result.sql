-- Job runners report a result document when a job finishes (PATCH /api/v1/jobs/{id}).
-- Never edit after release.
ALTER TABLE corvus.jobs ADD COLUMN result jsonb;
