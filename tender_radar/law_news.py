from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Any
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

from .scoring import score_notice


LAW_SEARCH_URL = "https://www.law.go.kr/DRF/lawSearch.do"
RELEVANT = (
    "건설", "건축", "주택", "도시", "정비", "재건축", "재개발", "시설물", "안전진단",
    "공사", "입찰", "계약", "조달", "건설산업", "건축사", "엔지니어링", "도로", "설계",
)
LAW_QUERIES = ("건설", "건축", "주택", "도시정비", "시설물", "국가계약", "지방계약")


def _pick(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is not None:
            return str(value).strip()
    return ""


def collect_law_news(oc: str, days: int = 90) -> list[dict[str, Any]]:
    if not oc:
        return []
    today = date.today()
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    def fetch_query(query: str) -> list[dict[str, Any]]:
        params = {
            "OC": oc,
            "target": "law",
            "type": "JSON",
            "display": "100",
            "sort": "ddes",
            "query": query,
            "ancYd": f"{today - timedelta(days=days):%Y%m%d}~{today:%Y%m%d}",
        }
        request = Request(
            LAW_SEARCH_URL + "?" + urlencode(params),
            headers={"User-Agent": "CONCOST-Opportunity-Radar/1.0"},
        )
        with urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8-sig"))
        root = payload.get("LawSearch", payload)
        query_rows = root.get("law", []) if isinstance(root, dict) else []
        if isinstance(query_rows, dict):
            query_rows = [query_rows]
        return [row for row in query_rows if isinstance(row, dict)]
    with ThreadPoolExecutor(max_workers=len(LAW_QUERIES), thread_name_prefix="law-query") as pool:
        for query_rows in pool.map(fetch_query, LAW_QUERIES):
            rows.extend(query_rows)
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = _pick(row, "법령명한글", "법령명")
        department = _pick(row, "소관부처명", "소관부처")
        if not any(word in f"{title} {department}" for word in RELEVANT):
            continue
        revision = _pick(row, "제개정구분명", "제개정구분")
        effective = _pick(row, "시행일자")
        link = _pick(row, "법령상세링크")
        url = urljoin("https://www.law.go.kr", link) if link else "https://www.law.go.kr"
        summary = " · ".join(value for value in (revision, department, f"시행 {effective}" if effective else "") if value)
        score, matched = score_notice(title, summary)
        source_key = _pick(row, "법령일련번호", "법령ID") or f"{title}:{_pick(row, '공포일자')}"
        if source_key in seen:
            continue
        seen.add(source_key)
        result.append({
            "source": "국가법령정보센터",
            "source_key": source_key,
            "category": "법규·제도 개정",
            "title": title,
            "summary": summary,
            "published_at": _pick(row, "공포일자"),
            "url": url,
            "score": score,
            "matched_keywords": matched,
        })
    return result
