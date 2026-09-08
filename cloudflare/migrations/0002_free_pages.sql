ALTER TABLE collection_jobs ADD COLUMN start_date TEXT NOT NULL DEFAULT '';
ALTER TABLE collection_jobs ADD COLUMN end_date TEXT NOT NULL DEFAULT '';
ALTER TABLE collection_jobs ADD COLUMN next_page INTEGER NOT NULL DEFAULT 1;
ALTER TABLE collection_jobs ADD COLUMN upstream_total INTEGER;
ALTER TABLE collection_jobs ADD COLUMN lease_until INTEGER NOT NULL DEFAULT 0;
-- An agency now has one job per day slice, not one job for the whole run.
CREATE TABLE collection_pages (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL, source_id TEXT NOT NULL,
 label TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
 created_at INTEGER NOT NULL, deadline INTEGER NOT NULL, updated_at INTEGER NOT NULL,
 candidates INTEGER, kept INTEGER, filtered INTEGER, error TEXT NOT NULL DEFAULT '',
 attempts INTEGER NOT NULL DEFAULT 0, start_date TEXT NOT NULL DEFAULT '',
 end_date TEXT NOT NULL DEFAULT '', next_page INTEGER NOT NULL DEFAULT 1,
 upstream_total INTEGER, lease_until INTEGER NOT NULL DEFAULT 0
);
INSERT INTO collection_pages SELECT * FROM collection_jobs;
DROP TABLE collection_jobs;
ALTER TABLE collection_pages RENAME TO collection_jobs;
CREATE INDEX jobs_run ON collection_jobs(run_id);
CREATE TABLE trial_budget (day TEXT PRIMARY KEY, pages INTEGER NOT NULL DEFAULT 0);
