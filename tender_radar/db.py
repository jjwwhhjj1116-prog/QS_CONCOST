from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS notices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    source_key TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    institution TEXT NOT NULL DEFAULT '',
    published_at TEXT NOT NULL DEFAULT '',
    deadline_at TEXT NOT NULL DEFAULT '',
    estimated_price INTEGER,
    region TEXT NOT NULL DEFAULT '',
    notice_type TEXT NOT NULL DEFAULT '신규',
    change_reason TEXT NOT NULL DEFAULT '',
    changed_at TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    score INTEGER NOT NULL DEFAULT 0,
    matched_keywords TEXT NOT NULL DEFAULT '[]',
    workflow_status TEXT NOT NULL DEFAULT 'new',
    content_hash TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    UNIQUE(source, source_key)
);
CREATE INDEX IF NOT EXISTS idx_notices_deadline ON notices(deadline_at);
CREATE INDEX IF NOT EXISTS idx_notices_score ON notices(score DESC);
CREATE TABLE IF NOT EXISTS admins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_salt TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    iterations INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS app_settings (
    setting_key TEXT PRIMARY KEY,
    setting_value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS news (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    source_key TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    published_at TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    score INTEGER NOT NULL DEFAULT 0,
    matched_keywords TEXT NOT NULL DEFAULT '[]',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    UNIQUE(source, source_key)
);
CREATE INDEX IF NOT EXISTS idx_news_published ON news(published_at DESC);
CREATE TABLE IF NOT EXISTS digest_recipients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS digest_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'sending',
    subject TEXT NOT NULL DEFAULT '',
    recipient_count INTEGER NOT NULL DEFAULT 0,
    new_notice_count INTEGER NOT NULL DEFAULT 0,
    existing_notice_count INTEGER NOT NULL DEFAULT 0,
    new_news_count INTEGER NOT NULL DEFAULT 0,
    existing_news_count INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS digest_delivery_items (
    delivery_id INTEGER NOT NULL,
    item_kind TEXT NOT NULL,
    source TEXT NOT NULL,
    source_key TEXT NOT NULL,
    is_new INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(delivery_id,item_kind,source,source_key),
    FOREIGN KEY(delivery_id) REFERENCES digest_deliveries(id)
);
CREATE INDEX IF NOT EXISTS idx_digest_items_key
ON digest_delivery_items(item_kind,source,source_key);
"""

PASSWORD_ITERATIONS = 310_000


class ClosingConnection(sqlite3.Connection):
    """Commit/rollback like sqlite3.Connection, then release the Windows file handle."""

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> bool:
        result = super().__exit__(exc_type, exc_value, traceback)
        self.close()
        return result


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(notices)")}
        migrations = {
            "notice_type": "TEXT NOT NULL DEFAULT '신규'",
            "change_reason": "TEXT NOT NULL DEFAULT ''",
            "changed_at": "TEXT NOT NULL DEFAULT ''",
        }
        for name, definition in migrations.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE notices ADD COLUMN {name} {definition}")
        if "notice_type" not in columns:
            for row in conn.execute("SELECT id, raw_json FROM notices WHERE raw_json <> '{}'"):
                try:
                    raw = json.loads(row["raw_json"])
                except json.JSONDecodeError:
                    continue
                official_kind = str(raw.get("ntceKindNm", "등록공고"))
                notice_type = {
                    "변경공고": "개정", "재공고": "재공고", "취소공고": "취소"
                }.get(official_kind, "신규")
                changed_at = str(raw.get("chgDt") or raw.get("rgstDt") or "") if notice_type != "신규" else ""
                conn.execute(
                    "UPDATE notices SET notice_type=?, change_reason=?, changed_at=? WHERE id=?",
                    (notice_type, str(raw.get("chgNtceRsn") or ""), changed_at, row["id"]),
                )
        bootstrap_password = os.getenv("ADMIN_BOOTSTRAP_PASSWORD", "").strip()
        if bootstrap_password:
            username = os.getenv("ADMIN_USERNAME", "concost").strip() or "concost"
            salt = secrets.token_hex(16)
            now = datetime.now().astimezone().isoformat(timespec="seconds")
            conn.execute(
                "INSERT OR IGNORE INTO admins(username,password_salt,password_hash,iterations,updated_at) VALUES(?,?,?,?,?)",
                (username, salt, _password_hash(bootstrap_password, salt), PASSWORD_ITERATIONS, now),
            )
        for email in os.getenv("DIGEST_RECIPIENTS", "").split(","):
            email = email.strip().lower()
            if email:
                now = datetime.now().astimezone().isoformat(timespec="seconds")
                conn.execute(
                    "INSERT OR IGNORE INTO digest_recipients(email,name,is_active,created_at,updated_at) "
                    "VALUES(?,?,1,?,?)",
                    (email, "", now, now),
                )
        conn.execute(
            "UPDATE app_settings SET setting_value='10:00' "
            "WHERE setting_key='digest_schedule_time' AND setting_value='08:30'"
        )
        conn.execute(
            "UPDATE app_settings SET setting_value=REPLACE(setting_value,'@con-cost.com','@con-cost.co.kr') "
            "WHERE setting_key='digest_from_email' AND setting_value LIKE '%@con-cost.com%'"
        )


def _password_hash(password: str, salt_hex: str, iterations: int = PASSWORD_ITERATIONS) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), iterations
    ).hex()


def authenticate_admin(db_path: Path, username: str, password: str) -> bool:
    init_db(db_path)
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT password_salt,password_hash,iterations FROM admins WHERE username=?", (username,)
        ).fetchone()
    if not row:
        _password_hash(password, "00" * 16)
        return False
    calculated = _password_hash(password, row["password_salt"], row["iterations"])
    return hmac.compare_digest(calculated, row["password_hash"])


def change_admin_password(db_path: Path, username: str, new_password: str) -> None:
    salt = secrets.token_hex(16)
    digest = _password_hash(new_password, salt)
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE admins SET password_salt=?,password_hash=?,iterations=?,updated_at=? WHERE username=?",
            (salt, digest, PASSWORD_ITERATIONS, now, username),
        )


def get_setting(db_path: Path, key: str, default: str = "") -> str:
    init_db(db_path)
    with connect(db_path) as conn:
        row = conn.execute("SELECT setting_value FROM app_settings WHERE setting_key=?", (key,)).fetchone()
    return str(row["setting_value"]) if row else default


def set_setting(db_path: Path, key: str, value: str, only_if_missing: bool = False) -> None:
    init_db(db_path)
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    with connect(db_path) as conn:
        if only_if_missing:
            conn.execute(
                "INSERT OR IGNORE INTO app_settings(setting_key,setting_value,updated_at) VALUES(?,?,?)",
                (key, value, now),
            )
        else:
            conn.execute(
                "INSERT INTO app_settings(setting_key,setting_value,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value,updated_at=excluded.updated_at",
                (key, value, now),
            )


def upsert_notice(db_path: Path, notice: dict[str, Any]) -> str:
    init_db(db_path)
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    raw_json = json.dumps(notice.get("raw", {}), ensure_ascii=False, sort_keys=True)
    digest_source = json.dumps(
        {k: v for k, v in notice.items() if k not in {"raw", "matched_keywords"}},
        ensure_ascii=False,
        sort_keys=True,
    )
    content_hash = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()
    with connect(db_path) as conn:
        existing = conn.execute(
            "SELECT content_hash FROM notices WHERE source=? AND source_key=?",
            (notice["source"], notice["source_key"]),
        ).fetchone()
        if existing is None:
            action = "inserted"
        elif existing["content_hash"] == content_hash:
            action = "unchanged"
        else:
            action = "updated"
        conn.execute(
            """
            INSERT INTO notices (
                source, source_key, category, title, institution, published_at,
                deadline_at, estimated_price, region, notice_type, change_reason,
                changed_at, url, score,
                matched_keywords, content_hash, raw_json, first_seen_at, last_seen_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, source_key) DO UPDATE SET
                category=excluded.category, title=excluded.title,
                institution=excluded.institution, published_at=excluded.published_at,
                deadline_at=excluded.deadline_at, estimated_price=excluded.estimated_price,
                region=excluded.region, notice_type=excluded.notice_type,
                change_reason=excluded.change_reason, changed_at=excluded.changed_at,
                url=excluded.url, score=excluded.score,
                matched_keywords=excluded.matched_keywords,
                content_hash=excluded.content_hash, raw_json=excluded.raw_json,
                last_seen_at=excluded.last_seen_at
            """,
            (
                notice["source"], notice["source_key"], notice["category"], notice["title"],
                notice.get("institution", ""), notice.get("published_at", ""),
                notice.get("deadline_at", ""), notice.get("estimated_price"),
                notice.get("region", ""), notice.get("notice_type", "신규"),
                notice.get("change_reason", ""), notice.get("changed_at", ""),
                notice.get("url", ""), notice.get("score", 0),
                json.dumps(notice.get("matched_keywords", []), ensure_ascii=False),
                content_hash, raw_json, now, now,
            ),
        )
    return action


def upsert_news(db_path: Path, item: dict[str, Any]) -> str:
    init_db(db_path)
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    with connect(db_path) as conn:
        existing = conn.execute(
            "SELECT id FROM news WHERE source=? AND source_key=?",
            (item["source"], item["source_key"]),
        ).fetchone()
        conn.execute(
            """INSERT INTO news(source,source_key,category,title,summary,published_at,url,score,
            matched_keywords,first_seen_at,last_seen_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(source,source_key) DO UPDATE SET category=excluded.category,title=excluded.title,
            summary=excluded.summary,published_at=excluded.published_at,url=excluded.url,
            score=excluded.score,matched_keywords=excluded.matched_keywords,last_seen_at=excluded.last_seen_at""",
            (item["source"], item["source_key"], item["category"], item["title"],
             item.get("summary", ""), item.get("published_at", ""), item.get("url", ""),
             item.get("score", 0), json.dumps(item.get("matched_keywords", []), ensure_ascii=False),
             now, now),
        )
    return "updated" if existing else "inserted"


def prune_news(db_path: Path, items: list[dict[str, Any]]) -> int:
    """Remove stale rows only for sources that returned at least one valid item."""
    by_source: dict[str, set[str]] = {}
    for item in items:
        by_source.setdefault(item["source"], set()).add(item["source_key"])
    removed = 0
    with connect(db_path) as conn:
        for source, keys in by_source.items():
            marks = ",".join("?" for _ in keys)
            cursor = conn.execute(
                f"DELETE FROM news WHERE source=? AND source_key NOT IN ({marks})",
                (source, *keys),
            )
            removed += cursor.rowcount
    return removed


def list_news(db_path: Path, category: str = "", query: str = "", limit: int = 100) -> list[dict[str, Any]]:
    init_db(db_path)
    clauses, params = ["1=1"], []
    if category:
        clauses.append("category=?")
        params.append(category)
    if query:
        clauses.append("(title LIKE ? OR summary LIKE ? OR source LIKE ?)")
        like = f"%{query}%"
        params.extend([like, like, like])
    params.append(limit)
    with connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT * FROM news WHERE {' AND '.join(clauses)} ORDER BY published_at DESC,score DESC LIMIT ?",
            params,
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["matched_keywords"] = json.loads(item["matched_keywords"])
        result.append(item)
    return result


def list_notices(
    db_path: Path, query: str = "", category: str = "", source: str = "", notice_type: str = "",
    min_score: int = 0, limit: int = 300
) -> list[dict[str, Any]]:
    init_db(db_path)
    clauses = ["score >= ?"]
    params: list[Any] = [min_score]
    if query:
        clauses.append("(title LIKE ? OR institution LIKE ? OR region LIKE ?)")
        like = f"%{query}%"
        params.extend([like, like, like])
    if category:
        clauses.append("category = ?")
        params.append(category)
    if source:
        clauses.append("source = ?")
        params.append(source)
    if notice_type:
        clauses.append("notice_type = ?")
        params.append(notice_type)
    params.append(limit)
    sql = f"SELECT * FROM notices WHERE {' AND '.join(clauses)} ORDER BY score DESC, published_at DESC LIMIT ?"
    with connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["matched_keywords"] = json.loads(item["matched_keywords"])
        item.pop("raw_json", None)
        item.pop("content_hash", None)
        result.append(item)
    return result


def update_status(db_path: Path, notice_id: int, status: str) -> bool:
    if status not in {"new", "review", "watch", "excluded"}:
        return False
    with connect(db_path) as conn:
        cur = conn.execute("UPDATE notices SET workflow_status=? WHERE id=?", (status, notice_id))
    return cur.rowcount == 1


def stats(db_path: Path) -> dict[str, int]:
    init_db(db_path)
    with connect(db_path) as conn:
        row = conn.execute(
            """SELECT COUNT(*) total,
            SUM(CASE WHEN score >= 60 THEN 1 ELSE 0 END) high,
            SUM(CASE WHEN workflow_status='watch' THEN 1 ELSE 0 END) watch,
            SUM(CASE WHEN workflow_status='review' THEN 1 ELSE 0 END) review,
            SUM(CASE WHEN notice_type='신규' THEN 1 ELSE 0 END) new_count,
            SUM(CASE WHEN notice_type='개정' THEN 1 ELSE 0 END) revised_count
            ,SUM(CASE WHEN source='나라장터' THEN 1 ELSE 0 END) g2b_count
            ,SUM(CASE WHEN source='LH' THEN 1 ELSE 0 END) lh_count
            ,SUM(CASE WHEN source='도로공사' THEN 1 ELSE 0 END) ex_count
            ,SUM(CASE WHEN source='공동주택관리정보시스템' THEN 1 ELSE 0 END) kapt_count
            FROM notices"""
        ).fetchone()
    return {key: int(row[key] or 0) for key in row.keys()}


def list_digest_recipients(db_path: Path) -> list[dict[str, Any]]:
    init_db(db_path)
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id,email,name,is_active,created_at,updated_at FROM digest_recipients "
            "ORDER BY is_active DESC,name,email"
        ).fetchall()
    return [dict(row) for row in rows]


def save_digest_recipient(db_path: Path, email: str, name: str = "") -> dict[str, Any]:
    init_db(db_path)
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    email = email.strip().lower()
    name = name.strip()[:80]
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO digest_recipients(email,name,is_active,created_at,updated_at) VALUES(?,?,1,?,?) "
            "ON CONFLICT(email) DO UPDATE SET name=excluded.name,is_active=1,updated_at=excluded.updated_at",
            (email, name, now, now),
        )
        row = conn.execute(
            "SELECT id,email,name,is_active,created_at,updated_at FROM digest_recipients WHERE email=?",
            (email,),
        ).fetchone()
    return dict(row)


def delete_digest_recipient(db_path: Path, recipient_id: int) -> bool:
    init_db(db_path)
    with connect(db_path) as conn:
        cursor = conn.execute("DELETE FROM digest_recipients WHERE id=?", (recipient_id,))
    return cursor.rowcount == 1


def list_digest_deliveries(db_path: Path, limit: int = 10) -> list[dict[str, Any]]:
    init_db(db_path)
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM digest_deliveries ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(row) for row in rows]
