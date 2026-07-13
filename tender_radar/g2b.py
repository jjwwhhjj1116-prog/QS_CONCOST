from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .scoring import MIN_NOTICE_SCORE, score_notice


BASE_URL = "https://apis.data.go.kr/1230000/ad/BidPublicInfoService"
OPERATIONS = {
    "공사": "getBidPblancListInfoCnstwk",
    "용역": "getBidPblancListInfoServc",
}


class G2BError(RuntimeError):
    pass


def _pick(item: dict[str, Any], *keys: str, default: Any = "") -> Any:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return value
    return default


def _money(value: Any) -> int | None:
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return None


def normalize_item(item: dict[str, Any], category: str) -> dict[str, Any]:
    notice_no = str(_pick(item, "bidNtceNo", "bidPbancNo", default="unknown"))
    order = str(_pick(item, "bidNtceOrd", "bidPbancOrd", default="00"))
    title = str(_pick(item, "bidNtceNm", "bidPbancNm", "ntceNm", default="제목 없음"))
    institution = str(_pick(item, "ntceInsttNm", "dminsttNm", "orderInsttNm"))
    region = str(_pick(item, "prtcptPsblRgnNm", "jntcontrctDutyRgnNm", "cnstrtsiteRgnNm"))
    official_kind = str(_pick(item, "ntceKindNm", default="등록공고"))
    notice_type = {
        "변경공고": "개정",
        "재공고": "재공고",
        "취소공고": "취소",
    }.get(official_kind, "신규")
    score, matched = score_notice(title, institution, region)
    return {
        "source": "나라장터",
        "source_key": f"{notice_no}-{order}",
        "category": category,
        "title": title,
        "institution": institution,
        "published_at": str(_pick(item, "bidNtceDt", "bidNtceDate", "rgstDt")),
        "deadline_at": str(_pick(item, "bidClseDt", "bidClseDate", "opengDt")),
        "estimated_price": _money(_pick(item, "presmptPrce", "asignBdgtAmt", "bsisAmount")),
        "region": region,
        "notice_type": notice_type,
        "change_reason": str(_pick(item, "chgNtceRsn")),
        "changed_at": str(_pick(item, "chgDt", "rgstDt")) if notice_type != "신규" else "",
        "url": str(_pick(item, "bidNtceDtlUrl", "bidPbancDtlUrl")),
        "score": score,
        "matched_keywords": matched,
        "raw": item,
    }


def _extract_payload(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    response = payload.get("response", payload)
    header = response.get("header", {})
    code = str(header.get("resultCode", "00"))
    if code not in {"00", "0"}:
        raise G2BError(f"API 오류 {code}: {header.get('resultMsg', '알 수 없는 오류')}")
    body = response.get("body", {})
    items_value = body.get("items", [])
    if isinstance(items_value, dict):
        items = items_value.get("item", [])
    else:
        items = items_value
    if isinstance(items, dict):
        items = [items]
    return list(items or []), int(body.get("totalCount", len(items or [])) or 0)


def fetch_category(
    service_key: str, category: str, start: datetime, end: datetime, rows: int = 500,
    max_pages: int = 6,
) -> list[dict[str, Any]]:
    if category not in OPERATIONS:
        raise ValueError(f"지원하지 않는 분야: {category}")
    collected: list[dict[str, Any]] = []
    page, fetched = 1, 0
    while page <= max_pages:
        params = {
            "serviceKey": service_key,
            "pageNo": page,
            "numOfRows": rows,
            "type": "json",
            "inqryDiv": "1",
            "inqryBgnDt": start.strftime("%Y%m%d%H%M"),
            "inqryEndDt": end.strftime("%Y%m%d%H%M"),
        }
        url = f"{BASE_URL}/{OPERATIONS[category]}?{urlencode(params)}"
        request = Request(url, headers={"User-Agent": "QS-Tender-Radar/0.1"})
        try:
            with urlopen(request, timeout=12) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:300].replace("\n", " ")
            except Exception:
                detail = "응답 본문 없음"
            if exc.code == 401:
                raise G2BError(
                    "HTTP 401: 인증키가 API 게이트웨이에 아직 반영되지 않았거나 유효하지 않습니다. "
                    "신규 승인 직후라면 잠시 후 다시 실행하세요."
                ) from exc
            raise G2BError(f"HTTP {exc.code}: API 요청이 거절되었습니다. {detail}") from exc
        except URLError as exc:
            raise G2BError(f"API 연결 실패: {exc.reason}") from exc
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            preview = raw[:180].replace("\n", " ")
            raise G2BError(f"JSON이 아닌 응답입니다: {preview}") from exc
        items, total = _extract_payload(payload)
        fetched += len(items)
        normalized = (normalize_item(item, category) for item in items)
        collected.extend(item for item in normalized if item["score"] >= MIN_NOTICE_SCORE)
        if not items or fetched >= total:
            break
        page += 1
    return collected


def collect_recent(service_key: str, lookback_hours: int = 48) -> list[dict[str, Any]]:
    if not service_key:
        raise G2BError("DATA_GO_KR_SERVICE_KEY가 비어 있습니다. .env에 Decoding 키를 입력하세요.")
    end = datetime.now()
    start = end - timedelta(hours=lookback_hours)
    result: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=len(OPERATIONS), thread_name_prefix="g2b-category") as pool:
        for rows in pool.map(lambda category: fetch_category(service_key, category, start, end), OPERATIONS):
            result.extend(rows)
    return result
