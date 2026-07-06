from __future__ import annotations

from datetime import datetime, timedelta
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from .scoring import score_notice


BASE_URL = "http://openapi.ebid.lh.or.kr/ebid.com.openapi.service.OpenBidInfoList.dev"
LIST_URL = "https://ebid.lh.or.kr/ebid.et.tp.cmd.BidMasterListCmd.dev"


class LHError(RuntimeError):
    pass


def _money(value: Any) -> int | None:
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return None


def normalize_item(item: dict[str, str]) -> dict[str, Any]:
    title = item.get("bidnmKor", "제목 없음")
    job = item.get("cstrtnJobGbNm", "")
    category = "용역" if "용역" in job else "공사"
    bid_kind = item.get("bidKind", "일반공고")
    if any(word in bid_kind for word in ("정정", "변경")):
        notice_type = "개정"
    elif "재공고" in bid_kind:
        notice_type = "재공고"
    elif "취소" in bid_kind:
        notice_type = "취소"
    else:
        notice_type = "신규"
    region = " · ".join(filter(None, (item.get(f"zoneRstrct{i}", "") for i in range(1, 5))))
    score, matched = score_notice(title, "한국토지주택공사", region)
    return {
        "source": "LH",
        "source_key": item.get("bidNum", title),
        "category": category,
        "title": title,
        "institution": item.get("zoneHqCd", "한국토지주택공사"),
        "published_at": item.get("tndrbidRegDt", ""),
        "deadline_at": item.get("tndrdocAcptEndDtm", ""),
        "estimated_price": _money(item.get("presmtPrc")),
        "region": region,
        "notice_type": notice_type,
        "change_reason": bid_kind if notice_type != "신규" else "",
        "changed_at": item.get("tndrbidRegDt", "") if notice_type != "신규" else "",
        "url": LIST_URL,
        "score": score,
        "matched_keywords": matched,
        "raw": item,
    }


def collect_recent(service_key: str, lookback_hours: int = 48) -> list[dict[str, Any]]:
    if not service_key:
        raise LHError("공공데이터포털 API 키가 없습니다.")
    end = datetime.now()
    start = end - timedelta(hours=lookback_hours)
    rows, page, result = 100, 1, []
    while True:
        query = urlencode({
            "serviceKey": service_key, "numOfRows": rows, "pageNo": page,
            "tndrbidRegDtStart": start.strftime("%Y%m%d"),
            "tndrbidRegDtEnd": end.strftime("%Y%m%d"),
        })
        try:
            with urlopen(Request(f"{BASE_URL}?{query}", headers={"User-Agent": "QS-Tender-Radar/0.2"}), timeout=30) as response:
                raw = response.read()
        except HTTPError as exc:
            raise LHError(f"HTTP {exc.code}: LH 입찰공고 API 활용신청 또는 승인 상태를 확인하세요.") from exc
        except URLError as exc:
            raise LHError(f"LH API 연결 실패: {exc.reason}") from exc
        try:
            root = ElementTree.fromstring(raw)
        except ValueError:
            text = raw.decode("euc-kr", errors="replace")
            text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text)
            root = ElementTree.fromstring(text)
        except ElementTree.ParseError as exc:
            raise LHError("LH API가 올바른 XML을 반환하지 않았습니다.") from exc
        code = (root.findtext(".//resultCode") or "").strip()
        message = (root.findtext(".//resultMsg") or "알 수 없는 오류").strip()
        if code not in {"00", "0"}:
            raise LHError(f"API 오류 {code}: {message}")
        items = [{child.tag: (child.text or "").strip() for child in node} for node in root.findall(".//item")]
        result.extend(normalize_item(item) for item in items)
        total = int(root.findtext(".//totalCount") or len(result))
        if not items or len(result) >= total:
            break
        page += 1
    return result
