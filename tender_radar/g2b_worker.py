"""Collect G2B notices on GitHub and import them into Render."""

from __future__ import annotations

import json
import os
from urllib.request import Request, urlopen

from .g2b import collect_recent
from .scoring import should_keep_notice


def main() -> None:
    target_url = os.getenv(
        "TARGET_URL",
        "https://qs-concost.onrender.com/api/automation/import-g2b",
    ).strip()
    token = os.getenv("DIGEST_TRIGGER_TOKEN", "").strip()
    service_key = os.getenv("DATA_GO_KR_SERVICE_KEY", "").strip()
    if not token:
        raise SystemExit("DIGEST_TRIGGER_TOKEN is required")
    if not service_key:
        raise SystemExit("DATA_GO_KR_SERVICE_KEY is required")
    try:
        lookback_hours = max(1, min(int(os.getenv("LOOKBACK_HOURS", "72")), 168))
    except ValueError:
        lookback_hours = 72

    rows = collect_recent(service_key, lookback_hours)
    filtered = [row for row in rows if should_keep_notice(row)]
    payload = json.dumps({"rows": filtered}, ensure_ascii=False).encode("utf-8")
    request = Request(
        target_url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "CONCOST-G2B-Worker/1.0",
        },
    )
    with urlopen(request, timeout=60) as response:
        print(f"Import HTTP {response.status}: {response.read().decode('utf-8')}")


if __name__ == "__main__":
    main()
