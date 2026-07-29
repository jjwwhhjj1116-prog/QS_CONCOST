"""Collect JiwonCOK boards outside the Render web process."""

from __future__ import annotations

import json
import os
from urllib.request import Request, urlopen

from .jiwoncok import collect_recent_with_status
from .scoring import should_keep_notice


def main() -> None:
    target_url = os.getenv(
        "TARGET_URL",
        "https://qs-concost.onrender.com/api/automation/import-jiwoncok",
    ).strip()
    token = os.getenv("DIGEST_TRIGGER_TOKEN", "").strip()
    if not token:
        raise SystemExit("DIGEST_TRIGGER_TOKEN is required")
    try:
        lookback_hours = max(1, min(int(os.getenv("LOOKBACK_HOURS", "72")), 168))
    except ValueError:
        lookback_hours = 72

    rows, statuses = collect_recent_with_status(lookback_hours)
    filtered = [row for row in rows if should_keep_notice(row)]
    payload = json.dumps(
        {"rows": filtered, "sources": statuses},
        ensure_ascii=False,
    ).encode("utf-8")
    request = Request(
        target_url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "CONCOST-JiwonCOK-Worker/1.0",
        },
    )
    with urlopen(request, timeout=45) as response:
        print(f"Import HTTP {response.status}: {response.read().decode('utf-8')}")


if __name__ == "__main__":
    main()
