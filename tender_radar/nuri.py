from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .scoring import MIN_NOTICE_SCORE, score_notice


BASE_URL = "https://apis.data.go.kr/1230000/ao/PrvtBidNtceService"
DETAIL_URL = "https://www.g2b.go.kr/link/PNPE027_01/single/"
OPERATIONS = {
    "용역": "getPrvtBidPblancListInfoServc",
    "공사": "getPrvtBidPblancListInfoCnstwk",
    "기타": "getPrvtBidPblancListInfoEtc",
}


class NuriError(RuntimeError):
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


def _detail_url(item: dict[str, Any], notice_no: str, order: str) -> str:
    supplied = str(_pick(item, "bidNtceDtlUrl", "bidNtceUrl", "bidPbancDtlUrl"))
    if supplied:
        return supplied
    if not notice_no or notice_no == "unknown":
        return ""
    # The private-bid API frequently omits its URL even though the notice is
    # published in the same next-generation G2B detail viewer. The stable
    # notice number/order link resolves both public and Nuri private notices.
    return f"{DETAIL_URL}?{urlencode({'bidPbancNo': notice_no, 'bidPbancOrd': order})}"


def normalize_item(item: dict[str, Any], category: str) -> dict[str, Any]:
    notice_no = str(_pick(item, "bidNtceNo", "bidPbancNo", default="unknown"))
    order = str(_pick(item, "bidNtceOrd", "bidPbancOrd", default="00"))
    title = str(_pick(item, "bidNtceNm", "bidPbancNm", "ntceNm", default="제목 없음"))
    institution = str(_pick(
        item, "ntceInsttNm", "dminsttNm", "orderInsttNm",
        "prvtBidNtceInsttNm", "bizNm",
    ))
    region = str(_pick(
        item, "prtcptPsblRgnNm", "jntcontrctDutyRgnNm", "cnstrtsiteRgnNm",
        "prtcptPsblRgnNm1",
    ))
    official_kind = str(_pick(item, "ntceKindNm", default="등록공고"))
    notice_type = {
        "변경공고": "개정",
        "재공고": "재공고",
        "취소공고": "취소",
    }.get(official_kind, "신규")
    score, matched = score_notice(title, institution, region, "누리장터 민간입찰")
    return {
        "source": "누리장터",
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
        "url": _detail_url(item, notice_no, order),
        "score": score,
        "matched_keywords": matched,
        "raw": item,
    }


def _extract_payload(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    response = payload.get("response", payload)
    header = response.get("header", {})
    code = str(header.get("resultCode", "00"))
    if code not in {"00", "0"}:
        raise NuriError(f"API 오류 {code}: {header.get('resultMsg', '알 수 없는 오류')}")
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
    service_key: str,
    category: str,
    start: datetime,
    end: datetime,
    rows: int = 300,
    max_pages: int = 6,
) -> list[dict[str, Any]]:
    operation = OPERATIONS[category]
    collected: list[dict[str, Any]] = []
    fetched = 0
    for page in range(1, max_pages + 1):
        params = {
            "serviceKey": service_key,
            "pageNo": page,
            "numOfRows": rows,
            "type": "json",
            "inqryDiv": "1",
            "inqryBgnDt": start.strftime("%Y%m%d%H%M"),
            "inqryEndDt": end.strftime("%Y%m%d%H%M"),
        }
        request = Request(
            f"{BASE_URL}/{operation}?{urlencode(params)}",
            headers={"User-Agent": "QS-Tender-Radar/0.1"},
        )
        try:
            with urlopen(request, timeout=12) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            if exc.code in {401, 403}:
                raise NuriError(
                    "누리장터 민간입찰공고서비스 활용 미승인입니다. "
                    "공공데이터포털에서 해당 API를 별도로 활용신청하세요."
                ) from exc
            raise NuriError(f"누리장터 HTTP {exc.code}") from exc
        except (TimeoutError, URLError) as exc:
            raise NuriError(f"누리장터 API 연결 실패: {exc}") from exc
        try:
            items, total = _extract_payload(json.loads(raw))
        except json.JSONDecodeError as exc:
            raise NuriError(f"누리장터 JSON 응답 오류: {raw[:160]}") from exc
        fetched += len(items)
        for item in items:
            normalized = normalize_item(item, category)
            if normalized["score"] >= MIN_NOTICE_SCORE:
                collected.append(normalized)
        if not items or fetched >= total:
            break
    return collected


def collect_recent(service_key: str, lookback_hours: int = 48) -> list[dict[str, Any]]:
    if not service_key:
        raise NuriError("DATA_GO_KR_SERVICE_KEY가 비어 있습니다.")
    end = datetime.now()
    start = end - timedelta(hours=max(1, lookback_hours))
    result: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=len(OPERATIONS), thread_name_prefix="nuri-category") as pool:
        for rows in pool.map(
            lambda category: fetch_category(service_key, category, start, end),
            OPERATIONS,
        ):
            result.extend(rows)
    return result
