-- Iceberg erasure (corvus-compact with CORVUS_LAKE_FORMAT=iceberg), see docs/reference/data-lake.md
-- section 2a. NULL: the request's rows are gone from the Iceberg tables. Set: the erasure is held
-- back (a branch or tag keeps a snapshot that still holds the rows); the request stays pending.
ALTER TABLE corvus.erasure_requests ADD COLUMN lake_blocked_reason text;
