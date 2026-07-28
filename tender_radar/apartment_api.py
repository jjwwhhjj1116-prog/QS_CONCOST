from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .kapt import DETAIL_URL, SOURCE, _category
from .scoring import MIN_NOTICE_SCORE, score_notice


BASE_URL = (
    "https://apis.data.go.kr/1613000/"
    "ApHusBidPblAncInfoOfferServiceV2/getPblAncDeSearchV2"
)


class ApartmentApiError(RuntimeError):
    pass


def normalize_item(item: dict[str, Any]) -> dict[str, Any]:
    title = str(item.get("bidTitle") or "제목 없음")
    institution = str(item.get("bidKaptname") or "")
    area_code = str(item.get("bidArea") or "")
    category = _category(title)
    score, matched = score_notice(
        title, institution, area_code, category, "공동주택 아파트 민간입찰"
    )
    state = str(item.get("bidState") or "")
    notice_type = {"2": "개정", "3": "재공고"}.get(state, "신규")
    bid_num = str(item.get("bidNum") or "")
    return {
        "source": SOURCE,
        "source_key": bid_num,
        "category": category,
        "title": title,
        "institution": institution,
        "published_at": str(item.get("bidRegDate") or item.get("bidRegdate") or ""),
        "deadline_at": str(item.get("bidDeadline") or ""),
        "estimated_price": None,
        "region": area_code,
        "notice_type": notice_type,
        "change_reason": "공동주택 API 수정·재공고" if notice_type != "신규" else "",
        "changed_at": str(item.get("bidRegDate") or "") if notice_type != "신규" else "",
        "url": f"{DETAIL_URL}?{urlencode({'bidNum': bid_num})}",
        "score": score,
        "matched_keywords": matched,
        "raw": item,
    }


def collect_recent(
    service_key: str, lookback_hours: int = 72, rows: int = 300, max_pages: int = 3
) -> list[dict[str, Any]]:
    if not service_key:
        raise ApartmentApiError("공공데이터포털 인증키가 비어 있습니다.")
    end = datetime.now()
    start = end - timedelta(hours=max(24, lookback_hours))
    result: list[dict[str, Any]] = []
    fetched = 0
    for page in range(1, max_pages + 1):
        params = {
            "serviceKey": service_key,
            "startDate": start.strftime("%Y%m%d"),
            "endDate": end.strftime("%Y%m%d"),
            "pageNo": page,
            "numOfRows": rows,
        }
        request = Request(
            f"{BASE_URL}?{urlencode(params)}",
            headers={"User-Agent": "CONCOST-Apartment-API/1.0"},
        )
        try:
            with urlopen(request, timeout=14) as response:
                payload = json.loads(response.read().decode("utf-8-sig"))
        except HTTPError as exc:
            raise ApartmentApiError(f"공동주택 API HTTP {exc.code}") from exc
        except (URLError, TimeoutError) as exc:
            raise ApartmentApiError("공동주택 API 연결 시간 초과") from exc
        response = payload.get("response", payload)
        header = response.get("header", {})
        if str(header.get("resultCode", "00")) not in {"00", "0"}:
            raise ApartmentApiError(
                f"공동주택 API {header.get('resultCode')}: {header.get('resultMsg', '')}"
            )
        body = response.get("body", {})
        items = body.get("items", [])
        if isinstance(items, dict):
            items = items.get("item", items)
        if isinstance(items, dict):
            items = [items]
        items = list(items or [])
        fetched += len(items)
        for item in items:
            normalized = normalize_item(item)
            if normalized["score"] >= MIN_NOTICE_SCORE:
                result.append(normalized)
        total = int(body.get("totalCount", fetched) or 0)
        if not items or fetched >= total:
            break
    return result
