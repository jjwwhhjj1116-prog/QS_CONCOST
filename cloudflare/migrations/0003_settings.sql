CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE recipients (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL UNIQUE COLLATE NOCASE, name TEXT NOT NULL DEFAULT '');
CREATE TABLE sessions (token_hash TEXT PRIMARY KEY, expires_at INTEGER NOT NULL);
CREATE TABLE login_limits (bucket TEXT PRIMARY KEY, attempts INTEGER NOT NULL);
CREATE TABLE delivery_days (
 day TEXT PRIMARY KEY, state TEXT NOT NULL, created_at INTEGER NOT NULL,
 payload TEXT NOT NULL CHECK(json_valid(payload)), error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE deliveries (
 day TEXT NOT NULL, email TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
 provider_id TEXT NOT NULL DEFAULT '', lease_until INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(day,email)
);
CREATE INDEX sessions_expiry ON sessions(expires_at);
CREATE TABLE collection_gate (scope TEXT PRIMARY KEY, run_id TEXT NOT NULL, deadline INTEGER NOT NULL);
