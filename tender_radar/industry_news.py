from __future__ import annotations

import html
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse
from urllib.request import Request, urlopen

from .scoring import score_notice


CERIK_PAGES = (
    ("CERIK 이슈포커스", "https://www.cerik.re.kr/report/issue"),
    ("CERIK 동향브리핑", "https://www.cerik.re.kr/report/briefing"),
    ("CERIK 건설동향", "https://www.cerik.re.kr/material/prospect"),
)
RICON_PAGES = (
    ("RICON 건설시장", "https://www.ricon.re.kr/board/list.php?cate=7&group=issue&page=market_issue"),
    ("RICON 건설브리프", "https://www.ricon.re.kr/board/list.php?cate=8&group=issue&page=ricon_brief"),
    ("RICON 수주동향", "https://www.ricon.re.kr/board/list.php?cate=9&group=issue&page=economic_index"),
    ("RICON 주택시장", "https://www.ricon.re.kr/board/list.php?cate=14&group=issue&page=house_market_trends"),
    ("RICON 최신건설정보", "https://www.ricon.re.kr/board/list.php?cate=10&group=issue&page=construction_info"),
)
CONSTIMES_URL = "https://www.constimes.co.kr/news/articleList.html?view_type=sm"

NEWS_KEYWORDS = (
    "건설", "건축", "토목", "주택", "부동산", "SOC", "인프라", "공사비", "사업비",
    "원가", "물가", "자재", "수주", "입찰", "계약", "발주", "설계", "BIM", "안전",
    "시설물", "정비사업", "재건축", "재개발", "리모델링", "모아타운", "도시정비",
    "클레임", "분쟁", "하자", "착공", "공급", "분양", "PF", "엔지니어링",
)
EXCLUDE_WORDS = (
    "채용", "인사", "부고", "결혼", "봉사활동", "기부", "장학금", "수상", "표창",
    "이벤트", "신제품 출시", "모델하우스 오픈", "견본주택 개관",
)


def _get(url: str) -> str:
    request = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; CONCOST-News-Radar/1.0)",
        "Accept": "text/html,application/xhtml+xml",
    })
    with urlopen(request, timeout=10) as response:
        raw = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, errors="replace")


def _clean(value: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", value)).split())


def _item(source: str, key: str, title: str, published_at: str, url: str,
          summary: str = "", authoritative: bool = True) -> dict[str, Any] | None:
    title = _clean(title)
    summary = _clean(summary)[:350]
    if len(title) < 8 or any(word.lower() in title.lower() for word in EXCLUDE_WORDS):
        return None
    keyword = next((word for word in NEWS_KEYWORDS if word.lower() in f"{title} {summary}".lower()), "")
    if not authoritative and not keyword:
        return None
    score, matched = score_notice(title, summary, source)
    if keyword and score < 25:
        score = 25
        matched.append(f"산업동향:{keyword}")
    elif authoritative and score < 20:
        score = 20
        matched.append("전문기관:건설산업 연구자료")
    return {
        "source": source,
        "source_key": key,
        "category": "건설 주요뉴스",
        "title": title,
        "summary": summary,
        "published_at": published_at,
        "url": url,
        "score": score,
        "matched_keywords": matched,
    }


def parse_cerik(html_text: str, source: str, base_url: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for chunk in html_text.split('<div class="document-preview-slide-wrap">')[1:]:
        title_match = re.search(r'<div class="title">([\s\S]*?)</div>', chunk, re.I)
        date_match = re.search(r'<b>\s*출판일\s*</b>\s*<span>([^<]+)</span>', chunk, re.I)
        link_match = re.search(r'href="(/(?:report|material)/[^"?#]+/\d+)"', chunk, re.I)
        if not title_match or not link_match:
            continue
        href = link_match.group(1)
        key_match = re.search(r"/(\d+)$", href)
        item = _item(
            source, key_match.group(1) if key_match else href, title_match.group(1),
            _clean(date_match.group(1)) if date_match else "", urljoin(base_url, href),
        )
        if item:
            result.append(item)
    return result


def parse_ricon(html_text: str, source: str, base_url: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in re.findall(r"<tr[^>]*>([\s\S]*?)</tr>", html_text, re.I):
        link_match = re.search(r'href="([^"]*/board/view\.php\?[^"#]+)"', row, re.I)
        title_match = re.search(r'<strong class="bo_sbj">([\s\S]*?)</strong>', row, re.I)
        if not link_match or not title_match:
            continue
        href = html.unescape(link_match.group(1))
        query = parse_qs(urlparse(href).query)
        key = (query.get("no") or [href])[0]
        date_match = re.search(r'<td class="col_date">\s*([^<]+)', row, re.I)
        category_match = re.search(r'<span class="bo_ca">([\s\S]*?)</span>', row, re.I)
        item = _item(
            source, key, title_match.group(1), _clean(date_match.group(1)) if date_match else "",
            urljoin(base_url, href), _clean(category_match.group(1)) if category_match else "",
        )
        if item:
            result.append(item)
    return result


def parse_constimes(html_text: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    pattern = re.compile(
        r'<a[^>]+href="(https?://www\.constimes\.co\.kr/news/articleView\.html\?idxno=\d+)"[^>]*>'
        r'([\s\S]*?)</a>', re.I,
    )
    for href, body in pattern.findall(html_text):
        key = (parse_qs(urlparse(html.unescape(href)).query).get("idxno") or [""])[0]
        if not key or key in seen:
            continue
        seen.add(key)
        item = _item("건설타임즈", key, body, "", html.unescape(href), authoritative=False)
        if item:
            result.append(item)
        if len(result) >= 40:
            break
    return result


def collect_industry_news() -> list[dict[str, Any]]:
    jobs: list[tuple[str, str, str]] = []
    jobs.extend(("cerik", source, url) for source, url in CERIK_PAGES)
    jobs.extend(("ricon", source, url) for source, url in RICON_PAGES)
    jobs.append(("constimes", "건설타임즈", CONSTIMES_URL))
    result: list[dict[str, Any]] = []

    def collect_one(job: tuple[str, str, str]) -> list[dict[str, Any]]:
        kind, source, url = job
        page = _get(url)
        if kind == "cerik":
            return parse_cerik(page, source, url)
        if kind == "ricon":
            return parse_ricon(page, source, url)
        return parse_constimes(page)

    with ThreadPoolExecutor(max_workers=6, thread_name_prefix="industry-news") as pool:
        futures = [pool.submit(collect_one, job) for job in jobs]
        for future in as_completed(futures):
            try:
                result.extend(future.result())
            except Exception:
                continue
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in result:
        key = (item["source"], item["source_key"])
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    return deduped
