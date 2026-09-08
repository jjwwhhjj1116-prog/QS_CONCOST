"""Public bid recovery copy, without credentials, recipients or admin state."""
from __future__ import annotations

import base64
import json
import os
import re
from datetime import datetime, timedelta
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .scoring import score_notice, should_keep_notice

REPOSITORY = "jjwwhhjj1116-prog/QS_CONCOST"
BRANCH = "codex/bootstrap-bids"
FILE = "collection_snapshot.json"
PUBLIC_URL = f"https://raw.githubusercontent.com/{REPOSITORY}/{BRANCH}/{FILE}"
FIELDS = (
    "source", "source_key", "category", "title", "institution", "published_at",
    "deadline_at", "estimated_price", "region", "notice_type", "change_reason",
    "changed_at", "url",
)


def merge_snapshot(previous: dict, rows: list[dict], completed_sources: list[str],
                   now: datetime | None = None) -> dict:
    now = now or datetime.now(ZoneInfo("Asia/Seoul"))
    today = now.date()
    merged = {}
    for row in [*previous.get("rows", []), *rows]:
        if not isinstance(row, dict) or not all(row.get(k) for k in ("source", "source_key", "title")):
            continue
        match = re.search(r"(20\d{2})[-./년\s]*(\d{1,2})[-./월\s]*(\d{1,2})", str(row.get("published_at", "")))
        try:
            published = datetime(*map(int, match.groups())).date() if match else None
        except ValueError:
            continue
        if published is None or not today - timedelta(days=7) <= published <= today:
            continue
        clean = {key: row.get(key, "") for key in FIELDS}
        clean["score"], clean["matched_keywords"] = score_notice(
            clean["title"], clean["institution"], clean["region"])
        if should_keep_notice(clean):
            merged[(clean["source"], clean["source_key"])] = clean
    dates = {source: date for source, date in previous.get("source_dates", {}).items()
             if isinstance(date, str) and date == today.isoformat()}
    dates.update({source: today.isoformat() for source in completed_sources})
    return {"version": 1, "generated_at": now.isoformat(), "source_dates": dates,
            "rows": list(merged.values())}


def publish_snapshot(rows: list[dict], completed_sources: list[str]) -> None:
    """Commit public-only rows before importing to an ephemeral Render process."""
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if not token:
        if os.getenv("GITHUB_ACTIONS") == "true":
            raise RuntimeError("GITHUB_TOKEN is required for restart-safe collection")
        return
    url = f"https://api.github.com/repos/{REPOSITORY}/contents/{FILE}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "User-Agent": "CONCOST-Public-Snapshot/1.0"}
    previous, sha = {}, None
    try:
        with urlopen(Request(f"{url}?ref={BRANCH}", headers=headers), timeout=20) as response:
            current = json.load(response)
        sha = current["sha"]
        previous = json.loads(base64.b64decode(current["content"]))
    except HTTPError as exc:
        if exc.code != 404:
            raise
    snapshot = merge_snapshot(previous, rows, completed_sources)
    body = {"message": "Update public collection recovery snapshot", "branch": BRANCH,
            "content": base64.b64encode(json.dumps(snapshot, ensure_ascii=False).encode()).decode()}
    if sha:
        body["sha"] = sha
    with urlopen(Request(url, data=json.dumps(body).encode(), headers=headers, method="PUT"),
                 timeout=30) as response:
        if response.status not in (200, 201):
            raise RuntimeError(f"Snapshot save failed: HTTP {response.status}")
    print(f"Public recovery snapshot saved: {len(snapshot['rows'])} rows")
