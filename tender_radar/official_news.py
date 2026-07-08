from __future__ import annotations

import html
import http.cookiejar
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import urljoin
from urllib.request import HTTPCookieProcessor, Request, build_opener

from .scoring import score_notice
from .industry_news import collect_industry_news


PPS_BOARDS = (
    ("조달청 보도자료", "00634", "건설 주요뉴스"),
    ("조달청 훈령", "00029", "법규·제도 개정"),
    ("조달청 고시", "00030", "법규·제도 개정"),
    ("조달청 행정예고", "01265", "법규·제도 개정"),
    ("조달청 시설공사 자료", "00036", "법규·제도 개정"),
)
MOLIT_LIST = "https://www.molit.go.kr/USR/NEWS/m_71/lst.jsp?lcmspage=1"
NEWS_RELEVANT = (
    "건설", "건축", "토목", "시설공사", "주택", "도시", "정비", "재건축", "재개발",
    "안전진단", "정밀안전", "건설안전", "시설물 안전", "진단", "공사비", "원가",
    "도로", "설계", "감리", "BIM", "스마트건설",
    "기술형입찰", "대형사업", "적격심사",
)
RULE_RELEVANT = NEWS_RELEVANT + (
    "입찰", "계약", "일반용역", "기술용역", "종합심사", "사업수행능력",
    "집행기준", "심사기준", "업무처리규정", "특수조건",
)
RULE_WORDS = ("개정", "법령", "법규", "규정", "기준", "고시", "지침", "제도", "시행령", "시행규칙")
RULE_EXCLUDE = ("성희롱", "인사", "비축", "원자재", "다수공급자", "내자구매", "물품", "혁신제품")
CONSTRUCTION_CORE = ("시설공사", "건설", "건축", "토목", "주택", "설계", "감리", "공사입찰", "기술용역")


def _get(url: str) -> str:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 CONCOST-Radar/1.0", "Accept": "text/html"})
    opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
    with opener.open(request, timeout=10) as response:
        raw = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, errors="replace")


def _clean(value: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", value)).split())


def _item(source: str, key: str, title: str, date: str, url: str,
          forced_category: str = "", summary: str = "", match_summary: bool = True) -> dict[str, Any] | None:
    category = forced_category or ("법규·제도 개정" if any(word in title for word in RULE_WORDS) else "건설 주요뉴스")
    relevant = RULE_RELEVANT if category == "법규·제도 개정" else NEWS_RELEVANT
    if category == "법규·제도 개정" and any(word in title for word in RULE_EXCLUDE):
        if not any(word in title for word in CONSTRUCTION_CORE):
            return None
    filter_text = f"{title} {summary}" if match_summary else title
    if not any(word.lower() in filter_text.lower() for word in relevant):
        return None
    score, matched = score_notice(title, source, summary)
    return {"source": source, "source_key": key, "category": category, "title": title,
            "summary": summary, "published_at": date, "url": url,
            "score": score, "matched_keywords": matched}


def collect_pps_board(source: str, board_key: str, category: str) -> list[dict[str, Any]]:
    list_url = f"https://www.pps.go.kr/kor/bbs/list.do?key={board_key}"
    page, result = _get(list_url), []
    for block in re.findall(r"<tr[^>]*>([\s\S]*?)</tr>", page, re.I):
        match = re.search(r"goView\('([^']+)'[^)]*\)[^>]*>([\s\S]*?)</a>", block, re.I)
        if not match:
            continue
        key, title = match.group(1), _clean(match.group(2))
        date_match = re.search(r"20\d{2}-\d{2}-\d{2}", block)
        cells = [_clean(cell) for cell in re.findall(r"<td[^>]*>([\s\S]*?)</td>", block, re.I)]
        summary = " · ".join(value for value in cells[2:-2] if value and "첨부파일" not in value)[:300]
        item = _item(source, key, title, date_match.group(0) if date_match else "",
                     f"https://www.pps.go.kr/kor/bbs/view.do?bbsSn={key}&key={board_key}",
                     category, summary)
        if item:
            result.append(item)
    return result


def collect_pps() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    def collect_board(args: tuple[str, str, str]) -> list[dict[str, Any]]:
        try:
            return collect_pps_board(*args)
        except Exception:
            return []
    with ThreadPoolExecutor(max_workers=len(PPS_BOARDS), thread_name_prefix="pps-board") as pool:
        for rows in pool.map(collect_board, PPS_BOARDS):
            result.extend(rows)
    return result


def collect_molit() -> list[dict[str, Any]]:
    page, result = _get(MOLIT_LIST), []
    for block in re.findall(r"<tr[^>]*>([\s\S]*?)</tr>", page, re.I):
        match = re.search(r'href="([^"]*dtl\.jsp\?[^"]*id=(\d+)[^"]*)"[^>]*>([\s\S]*?)</a>', block, re.I)
        if not match:
            continue
        href, key, title = html.unescape(match.group(1)), match.group(2), _clean(match.group(3))
        date_match = re.search(r"20\d{2}[.-]\d{2}[.-]\d{2}", block)
        field_match = re.search(r'class="bd_field"[^>]*>([\s\S]*?)</td>', block, re.I)
        summary = _clean(field_match.group(1)) if field_match else ""
        item = _item("국토교통부 보도자료", key, title, date_match.group(0) if date_match else "",
                     urljoin(MOLIT_LIST, href), "건설 주요뉴스", summary, match_summary=False)
        if item:
            result.append(item)
    return result


def collect_official_news() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    collectors = (collect_pps, collect_molit, collect_industry_news)
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="official-news") as pool:
        futures = [pool.submit(collector) for collector in collectors]
        for future in futures:
            try:
                result.extend(future.result())
            except Exception:
                continue
    deduped: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    for item in result:
        title_key = re.sub(r"\s+", "", item["title"]).lower()
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)
        deduped.append(item)
    return deduped
