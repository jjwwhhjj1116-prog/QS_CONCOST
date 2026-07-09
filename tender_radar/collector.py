from __future__ import annotations

from concurrent.futures import TimeoutError, ThreadPoolExecutor, as_completed
from typing import Any

from . import expressway, g2b, kapt, law_news, lh, official_news


def _collect_with_deadline(
    collectors: tuple[tuple[str, Any], ...],
    timeout_seconds: float,
    thread_prefix: str,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """Run independent collectors without letting one slow source block the whole refresh."""
    items_by_source: dict[str, list[dict[str, Any]]] = {}
    statuses: list[dict[str, Any]] = []
    order = {source: index for index, (source, _) in enumerate(collectors)}
    pool = ThreadPoolExecutor(max_workers=len(collectors), thread_name_prefix=thread_prefix)
    futures = {pool.submit(collect): source for source, collect in collectors}
    pending = set(futures)
    try:
        for future in as_completed(futures, timeout=timeout_seconds):
            pending.discard(future)
            source = futures[future]
            try:
                rows = future.result()
                items_by_source[source] = rows
                statuses.append({"source": source, "ok": True, "total": len(rows)})
            except Exception as exc:
                statuses.append({"source": source, "ok": False, "total": 0, "error": str(exc)})
    except TimeoutError:
        pass
    finally:
        for future in pending:
            source = futures[future]
            future.cancel()
            statuses.append({
                "source": source, "ok": False, "total": 0,
                "error": f"{int(timeout_seconds)}초 제한시간 초과",
            })
        pool.shutdown(wait=False, cancel_futures=True)
    statuses.sort(key=lambda item: order.get(item["source"], 99))
    return items_by_source, statuses


def collect_all(
    service_key: str,
    lookback_hours: int,
    source_timeout_seconds: float = 70,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    notices: list[dict[str, Any]] = []
    collectors = (
        ("나라장터", lambda: g2b.collect_recent(service_key, lookback_hours)),
        ("LH", lambda: lh.collect_recent(service_key, lookback_hours)),
        ("도로공사", lambda: expressway.collect_recent(lookback_hours)),
        ("공동주택관리정보시스템", lambda: kapt.collect_recent(lookback_hours)),
    )
    rows_by_source, statuses = _collect_with_deadline(collectors, source_timeout_seconds, "bid-source")
    by_source = {source: {"kept": 0, "filtered": 0} for source, _ in collectors}
    for source, rows in rows_by_source.items():
        for row in rows:
            score = int(row.get("score") or 0)
            if score > 20:
                notices.append(row)
                by_source[source]["kept"] += 1
            else:
                by_source[source]["filtered"] += 1
    for status in statuses:
        if status["ok"]:
            counts = by_source.get(status["source"], {"kept": status["total"], "filtered": 0})
            status["total"] = counts["kept"]
            status["filtered"] = counts["filtered"]
    return notices, statuses


def collect_news(
    law_key: str,
    source_timeout_seconds: float = 45,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    collectors: list[tuple[str, Any]] = [("공식 건설뉴스", official_news.collect_official_news)]
    if law_key:
        collectors.append(("국가법령정보", lambda: law_news.collect_law_news(law_key)))
    items_by_source, statuses = _collect_with_deadline(tuple(collectors), source_timeout_seconds, "content-source")
    items = [item for rows in items_by_source.values() for item in rows]
    if not law_key:
        statuses.append({"source": "국가법령정보", "ok": False, "total": 0, "error": "API 인증값 미설정"})
    return items, statuses
