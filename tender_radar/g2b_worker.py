"""Collect G2B and Nuri notices on GitHub and import them into Render."""

from __future__ import annotations

import json
import os
from urllib.request import Request, urlopen

from . import g2b, nuri
from .scoring import should_keep_notice
from .public_snapshot import publish_snapshot


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
        lookback_hours = max(1, min(int(os.getenv("LOOKBACK_HOURS", "168")), 168))
    except ValueError:
        lookback_hours = 168

    rows: list[dict] = []
    completed_sources: list[str] = []
    errors: list[str] = []
    for source, collect in (
        ("나라장터", g2b.collect_recent),
        ("누리장터", nuri.collect_recent),
    ):
        try:
            rows.extend(collect(service_key, lookback_hours))
            completed_sources.append(source)
        except Exception as exc:
            errors.append(f"{source}: {exc}")
    if not completed_sources:
        raise SystemExit(" / ".join(errors) or "공공데이터 수집 실패")

    filtered = [row for row in rows if should_keep_notice(row)]
    publish_snapshot(filtered, completed_sources)
    payload = json.dumps(
        {"rows": filtered, "errors": errors, "completed_sources": completed_sources},
        ensure_ascii=False,
    ).encode("utf-8")
    request = Request(
        target_url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "CONCOST-Public-Bid-Worker/1.0",
        },
    )
    with urlopen(request, timeout=60) as response:
        print(f"Import HTTP {response.status}: {response.read().decode('utf-8')}")
    if errors:
        print("Partial source errors:", " | ".join(errors))


if __name__ == "__main__":
    main()
