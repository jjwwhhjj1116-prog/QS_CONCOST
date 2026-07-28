from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .scoring import MIN_NOTICE_SCORE, score_notice


BASE_URL = "https://apis.data.go.kr/B500001/ebid/tndr3"


class KwaterError(RuntimeError):
    pass


def _money(value: Any) -> int | None:
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return None


def normalize_item(item: dict[str, Any], category: str) -> dict[str, Any]:
    title = str(item.get("tndrPblancNm") or "제목 없음")
    institution = str(item.get("cntrctDeptNm") or "한국수자원공사")
    score, matched = score_notice(title, institution, "", category, "K-water 전자조달")
    notice_no = str(item.get("tndrPbanno") or "")
    return {
        "source": "K-water",
        "source_key": notice_no,
        "category": category,
        "title": title,
        "institution": institution,
        "published_at": str(item.get("tndrPblancDe") or ""),
        "deadline_at": str(item.get("tndrPblancEnddt") or ""),
        "estimated_price": _money(item.get("tndrPlnprc")),
        "region": "",
        "notice_type": "신규",
        "change_reason": "",
        "changed_at": "",
        "url": "https://ebid.kwater.or.kr/",
        "score": score,
        "matched_keywords": matched,
        "raw": item,
    }


def _fetch_month(service_key: str, operation: str, month: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for page in range(1, 3):
        # The service returns an empty body when numOfRows is too large even
        # though its response header says JSON. Keep each page at 100 rows.
        params = {
            "serviceKey": service_key,
            "pageNo": page,
            "numOfRows": 100,
            "_type": "json",
            "searchDt": month,
        }
        request = Request(
            f"{BASE_URL}/{operation}?{urlencode(params)}",
            headers={"User-Agent": "CONCOST-Kwater/1.0"},
        )
        try:
            with urlopen(request, timeout=14) as response:
                raw = response.read()
                if not raw:
                    raise KwaterError("K-water API가 빈 응답을 반환했습니다.")
                payload = json.loads(raw.decode("utf-8-sig"))
        except HTTPError as exc:
            raise KwaterError(f"K-water API HTTP {exc.code}") from exc
        except (URLError, TimeoutError) as exc:
            raise KwaterError("K-water API 연결 시간 초과") from exc
        except json.JSONDecodeError as exc:
            raise KwaterError("K-water API 응답 형식 오류") from exc
        response = payload.get("response", payload)
        header = response.get("header", {})
        if str(header.get("resultCode", "00")) not in {"00", "0"}:
            raise KwaterError(
                f"K-water API {header.get('resultCode')}: {header.get('resultMsg', '')}"
            )
        body = response.get("body", {})
        items = body.get("items", [])
        if isinstance(items, dict):
            items = items.get("item", items)
        if isinstance(items, dict):
            items = [items]
        items = list(items or [])
        result.extend(items)
        total = int(body.get("totalCount", len(result)) or 0)
        if not items or len(result) >= total:
            break
    return result


def collect_recent(service_key: str, lookback_hours: int = 72) -> list[dict[str, Any]]:
    if not service_key:
        raise KwaterError("공공데이터포털 인증키가 비어 있습니다.")
    cutoff = datetime.now() - timedelta(hours=max(24, lookback_hours))
    months = {datetime.now().strftime("%Y%m"), cutoff.strftime("%Y%m")}
    result: list[dict[str, Any]] = []
    for operation, category in (("cntrwkList", "공사"), ("servcList", "용역")):
        for month in months:
            for item in _fetch_month(service_key, operation, month):
                published_text = str(item.get("tndrPblancDe") or "")
                try:
                    published = datetime.strptime(published_text[:8], "%Y%m%d")
                except ValueError:
                    published = datetime.now()
                if published < cutoff:
                    continue
                normalized = normalize_item(item, category)
                if normalized["score"] >= MIN_NOTICE_SCORE:
                    result.append(normalized)
    deduped = {(row["source"], row["source_key"]): row for row in result}
    return list(deduped.values())
