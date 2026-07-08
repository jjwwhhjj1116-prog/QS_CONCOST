from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from . import expressway, g2b, kapt, law_news, lh, official_news


def collect_all(service_key: str, lookback_hours: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    notices: list[dict[str, Any]] = []
    statuses: list[dict[str, Any]] = []
    collectors = (
        ("나라장터", lambda: g2b.collect_recent(service_key, lookback_hours)),
        ("LH", lambda: lh.collect_recent(service_key, lookback_hours)),
        ("도로공사", lambda: expressway.collect_recent(lookback_hours)),
        ("공동주택관리정보시스템", lambda: kapt.collect_recent(lookback_hours)),
    )
    with ThreadPoolExecutor(max_workers=len(collectors), thread_name_prefix="bid-source") as pool:
        futures = {pool.submit(collect): source for source, collect in collectors}
        for future in as_completed(futures):
            source = futures[future]
            try:
                rows = future.result()
                relevant = [row for row in rows if int(row.get("score") or 0) > 20]
                notices.extend(relevant)
                statuses.append({
                    "source": source, "ok": True, "total": len(relevant),
                    "filtered": len(rows) - len(relevant),
                })
            except Exception as exc:
                statuses.append({"source": source, "ok": False, "total": 0, "error": str(exc)})
    order = {source: index for index, (source, _) in enumerate(collectors)}
    statuses.sort(key=lambda item: order.get(item["source"], 99))
    return notices, statuses


def collect_news(law_key: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    collectors: list[tuple[str, Any]] = [("공식 건설뉴스", official_news.collect_official_news)]
    if law_key:
        collectors.append(("국가법령정보", lambda: law_news.collect_law_news(law_key)))
    items: list[dict[str, Any]] = []
    statuses: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=len(collectors), thread_name_prefix="content-source") as pool:
        futures = {pool.submit(collect): source for source, collect in collectors}
        for future in as_completed(futures):
            source = futures[future]
            try:
                rows = future.result()
                items.extend(rows)
                statuses.append({"source": source, "ok": True, "total": len(rows)})
            except Exception as exc:
                statuses.append({"source": source, "ok": False, "total": 0, "error": str(exc)})
    if not law_key:
        statuses.append({"source": "국가법령정보", "ok": False, "total": 0, "error": "API 인증값 미설정"})
    return items, statuses
