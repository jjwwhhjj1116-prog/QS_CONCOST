from __future__ import annotations

import json
import re
from datetime import datetime
from http.cookiejar import CookieJar
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener

from .scoring import score_notice


HOME_URL = "https://ebid.ex.co.kr/default.do"
LIST_API = "https://ebid.ex.co.kr/findPagingPortalBidNotiList.do"


class ExpresswayError(RuntimeError):
    pass


def normalize_item(item: dict[str, Any], category: str) -> dict[str, Any]:
    title = str(item.get("noti_nm") or "제목 없음")
    revision = int(item.get("bid_rev") or 1)
    notice_type = "개정" if revision > 1 or any(x in title for x in ("변경", "정정")) else "신규"
    published = str(item.get("noti_date") or "")
    score, matched = score_notice(title, "한국도로공사", "전국")
    menu_id = "NPRO11001" if category == "공사" else "NPRO12001"
    detail_query = urlencode({
        "menuId": menu_id, "portal_yn": "Y",
        "noti_cont_id": item.get("noti_cont_id", ""), "noti_id": item.get("noti_id", ""),
        "noti_no": item.get("noti_no", ""), "bid_no": item.get("bid_no", ""),
        "bid_rev": revision,
    })
    return {
        "source": "도로공사",
        "source_key": f"{item.get('noti_no', title)}-{revision}",
        "category": category,
        "title": title,
        "institution": "한국도로공사",
        "published_at": published,
        "deadline_at": "",
        "estimated_price": None,
        "region": "전국",
        "notice_type": notice_type,
        "change_reason": "정정·변경 공고" if notice_type == "개정" else "",
        "changed_at": published if notice_type == "개정" else "",
        "url": f"{HOME_URL}?{detail_query}",
        "score": score,
        "matched_keywords": matched,
        "raw": item,
    }


def collect_recent(lookback_hours: int = 48) -> list[dict[str, Any]]:
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    try:
        with opener.open(Request(HOME_URL, headers={"User-Agent": "QS-Tender-Radar/0.2"}), timeout=30) as response:
            html = response.read().decode("utf-8", errors="replace")
        match = re.search(r'name="_csrf" content="([^"]+)"', html)
        if not match:
            raise ExpresswayError("도로공사 공개목록 보안 토큰을 찾지 못했습니다.")
        result: list[dict[str, Any]] = []
        cutoff = datetime.now().timestamp() - lookback_hours * 3600
        for code, category in (("CT", "공사"), ("SV", "용역")):
            body = json.dumps({"noti_cls": code}).encode("utf-8")
            request = Request(LIST_API, data=body, method="POST", headers={
                "User-Agent": "QS-Tender-Radar/0.2", "Content-Type": "application/json;charset=UTF-8",
                "Accept": "application/json", "X-CSRF-TOKEN": match.group(1), "menucode": "HOME",
            })
            with opener.open(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
            for item in payload.get("result_list", []):
                date_text = str(item.get("noti_date") or "")
                try:
                    item_time = datetime.strptime(date_text, "%Y%m%d").timestamp()
                except ValueError:
                    item_time = cutoff
                if item_time >= cutoff - 86400:
                    result.append(normalize_item(item, category))
        return result
    except ExpresswayError:
        raise
    except HTTPError as exc:
        raise ExpresswayError(f"HTTP {exc.code}: 도로공사 공개목록 요청이 거절되었습니다.") from exc
    except (URLError, json.JSONDecodeError) as exc:
        raise ExpresswayError(f"도로공사 공개목록 연결 실패: {exc}") from exc
