-- User-authorized cutover to Cloudflare, 2026-09-08. No credentials in SQL.
INSERT INTO settings(key,value) VALUES ('digest_enabled','true')
ON CONFLICT(key) DO UPDATE SET value='true';
