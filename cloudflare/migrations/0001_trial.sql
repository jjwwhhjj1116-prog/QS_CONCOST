-- Trial data only. Not a migration of production users/secrets/delivery history.
CREATE TABLE items (
    kind TEXT NOT NULL CHECK(kind IN ('notice','news','pipeline','cost')),
    source TEXT NOT NULL,
    source_key TEXT NOT NULL,
    variant TEXT NOT NULL DEFAULT '',
    published_at TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL CHECK(json_valid(payload)),
    collected_at TEXT NOT NULL,
    PRIMARY KEY(kind,source,source_key,variant)
);
CREATE INDEX items_date ON items(kind,published_at);
CREATE TABLE collection_jobs (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    label TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'queued',
    created_at INTEGER NOT NULL,
    deadline INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    candidates INTEGER,
    kept INTEGER,
    filtered INTEGER,
    error TEXT NOT NULL DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 0,
    UNIQUE(run_id,source_id)
);
CREATE INDEX jobs_run ON collection_jobs(run_id);
