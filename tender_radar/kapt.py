from __future__ import annotations

import re
from datetime import datetime, timedelta
from html.parser import HTMLParser
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .scoring import score_notice


LIST_URL = "https://www.k-apt.go.kr/bid/bidList.do"
DETAIL_URL = "https://www.k-apt.go.kr/bid/bidDetail.do"
SOURCE = "공동주택관리정보시스템"


class KaptError(RuntimeError):
    pass


class _BidTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict[str, Any]] = []
        self._in_row = False
        self._in_cell = False
        self._cells: list[str] = []
        self._text: list[str] = []
        self._bid_num = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "tr":
            self._in_row = True
            self._cells = []
            self._bid_num = ""
        elif tag == "td" and self._in_row:
            self._in_cell = True
            self._text = []
            onclick = values.get("onclick") or ""
            match = re.search(r"goView\(['\"]([^'\"]+)", onclick)
            if match and not self._bid_num:
                self._bid_num = match.group(1)
        elif tag == "br" and self._in_cell:
            self._text.append(" ")

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self._in_cell:
            self._cells.append(" ".join("".join(self._text).split()))
            self._in_cell = False
        elif tag == "tr" and self._in_row:
            if self._bid_num and len(self._cells) >= 8:
                self.rows.append({"bid_num": self._bid_num, "cells": self._cells[:8]})
            self._in_row = False


def parse_list(html: str) -> list[dict[str, str]]:
    parser = _BidTableParser()
    parser.feed(html)
    result: list[dict[str, str]] = []
    for row in parser.rows:
        cells = row["cells"]
        result.append({
            "bid_num": row["bid_num"],
            "method": cells[1],
            "award_method": cells[2],
            "title": cells[3],
            "deadline_at": cells[4],
            "status": cells[5],
            "apartment": cells[6],
            "published_at": cells[7],
        })
    return result


def _category(title: str) -> str:
    if any(word in title for word in ("공사", "보수", "도장", "방수", "교체", "개선", "설치")):
        return "공사"
    return "용역"


def normalize_item(item: dict[str, str]) -> dict[str, Any]:
    title = item.get("title", "제목 없음")
    apartment = item.get("apartment", "")
    region_match = re.match(r"\[([^]]+)]", title)
    region = region_match.group(1) if region_match else "전국"
    status = item.get("status", "")
    notice_type = {
        "수정공고": "개정", "재공고": "재공고", "마감공고": "마감"
    }.get(status, "신규")
    category = _category(title)
    score, matched = score_notice(title, apartment, region, category, "공동주택 아파트")
    if category == "공사":
        score = min(100, score + 8)
        matched.append("공동주택:공사")
    bid_num = item.get("bid_num", "")
    return {
        "source": SOURCE,
        "source_key": bid_num,
        "category": category,
        "title": title,
        "institution": apartment,
        "published_at": item.get("published_at", ""),
        "deadline_at": item.get("deadline_at", ""),
        "estimated_price": None,
        "region": region,
        "notice_type": notice_type,
        "change_reason": status if notice_type != "신규" else "",
        "changed_at": item.get("published_at", "") if notice_type != "신규" else "",
        "url": f"{DETAIL_URL}?{urlencode({'bidNum': bid_num})}",
        "score": score,
        "matched_keywords": matched,
        "raw": item,
    }


def _fetch_page(page: int) -> list[dict[str, str]]:
    query = urlencode({"pageNo": page, "pageSelect": 100})
    request = Request(
        f"{LIST_URL}?{query}",
        headers={"User-Agent": "CONCOST-QS-Radar/1.0", "Accept": "text/html"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            html = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        raise KaptError(f"HTTP {exc.code}: K-apt 공개목록 요청이 거절되었습니다.") from exc
    except URLError as exc:
        raise KaptError(f"K-apt 공개목록 연결 실패: {exc.reason}") from exc
    rows = parse_list(html)
    if not rows and "전국 입찰공고" not in html:
        raise KaptError("K-apt 공개목록 형식이 변경되어 공고를 읽지 못했습니다.")
    return rows


def collect_recent(lookback_hours: int = 48) -> list[dict[str, Any]]:
    cutoff = datetime.now() - timedelta(hours=max(1, lookback_hours))
    result: list[dict[str, Any]] = []
    for page in range(1, 11):
        rows = _fetch_page(page)
        if not rows:
            break
        reached_cutoff = False
        for item in rows:
            try:
                published = datetime.fromisoformat(item.get("published_at", ""))
            except ValueError:
                published = datetime.now()
            if published < cutoff:
                reached_cutoff = True
                continue
            result.append(normalize_item(item))
        if reached_cutoff:
            break
    return result
