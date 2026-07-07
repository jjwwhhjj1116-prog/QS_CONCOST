from __future__ import annotations

from typing import Any

from . import expressway, g2b, kapt, lh


def collect_all(service_key: str, lookback_hours: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    notices: list[dict[str, Any]] = []
    statuses: list[dict[str, Any]] = []
    collectors = (
        ("나라장터", lambda: g2b.collect_recent(service_key, lookback_hours)),
        ("LH", lambda: lh.collect_recent(service_key, lookback_hours)),
        ("도로공사", lambda: expressway.collect_recent(lookback_hours)),
        ("공동주택관리정보시스템", lambda: kapt.collect_recent(lookback_hours)),
    )
    for source, collect in collectors:
        try:
            rows = collect()
            notices.extend(rows)
            statuses.append({"source": source, "ok": True, "total": len(rows)})
        except (g2b.G2BError, lh.LHError, expressway.ExpresswayError, kapt.KaptError) as exc:
            statuses.append({"source": source, "ok": False, "total": 0, "error": str(exc)})
    return notices, statuses
