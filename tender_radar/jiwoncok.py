from __future__ import annotations

import re
import hashlib
import html
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import unquote, urljoin
from urllib.request import Request, urlopen

from .scoring import score_notice


SEOUL_TZ = datetime.now().astimezone().tzinfo
SOURCE = "지원COK"
LINK_RE = re.compile(r"\[([^\]]{8,240})\]\((https?://[^\s)]+)\)")
DATE_RE = re.compile(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})")
WATCH_KEYWORDS = (
    "제안서 평가위원", "평가위원 후보자", "평가위원 모집", "제안서평가위원",
    "예비평가위원", "심사위원 모집", "위원 공개모집", "선정심의위원회",
    "공법선정위원회", "공법선정", "건축위원회", "건설기술심의",
    "기술입찰", "설계공모 심사", "정비사업", "도시계획위원회",
)

# 1차 MVP 소스. 지원콕 메일에서 반복 등장한 기관을 시작점으로 두고,
# 실제 운영 중 발견되는 기관 게시판 URL을 계속 추가하는 구조다.
SOURCE_PAGES = (
    {"institution": "부산광역시", "url": "https://www.busan.go.kr/nbgosi"},
    {"institution": "서울교통공사", "url": "https://www.seoulmetro.co.kr/kr/board.do?menuIdx=546"},
    {"institution": "창원시", "url": "https://www.changwon.go.kr/cwportal/10310/10438/10439.web"},
    {"institution": "김해시", "url": "https://www.gimhae.go.kr/03360/00023/00024.web"},
    {"institution": "구미시", "url": "https://www.gumi.go.kr/portal/saeol/gosi/list.do"},
    {"institution": "평택시", "url": "https://www.pyeongtaek.go.kr/pyeongtaek/saeol/gosiList.do"},
    {"institution": "청주시", "url": "https://www.cheongju.go.kr/www/selectEminwonList.do?key=279"},
    {"institution": "인천광역시", "url": "https://www.incheon.go.kr/IC010301"},
    {"institution": "경기신용보증재단", "url": "https://www.gcgf.or.kr/"},
    {"institution": "경북연구원", "url": "https://www.gi.re.kr/"},
    {"institution": "포항테크노파크", "url": "https://www.ptp.or.kr/"},
)


class JiwonCokError(RuntimeError):
    pass


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, str]] = []
        self._href = ""
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        values = dict(attrs)
        href = values.get("href") or ""
        if href and not href.lower().startswith(("javascript:", "#", "mailto:", "tel:")):
            self._href = href
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href:
            text = _clean(" ".join(self._text))
            if text:
                self.links.append({"href": self._href, "title": text})
            self._href = ""
            self._text = []


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip(" \t\r\n:-")


def _direct_jiwoncok_url(url: str) -> tuple[str, str]:
    decoded = unquote(url)
    match = re.search(r"https?://jiwonkok\.com/(\d+)/?", decoded)
    if match:
        notice_id = match.group(1)
        return f"https://jiwonkok.com/{notice_id}/?source=concost", notice_id
    return url, re.sub(r"\W+", "-", url)[-80:]


def _field(segment: str, label: str) -> str:
    match = re.search(rf"{re.escape(label)}\s*:\s*(.+)", segment)
    if not match:
        return ""
    value = match.group(1).strip()
    # Emoji-separated mail blocks put each field on its own line. Keep only that line.
    return _clean(value.splitlines()[0])


def _deadline(period: str) -> str:
    if not period or "미정" in period:
        return period or ""
    dates = DATE_RE.findall(period)
    if not dates:
        return period
    year, month, day = dates[-1]
    return f"{year}-{int(month):02d}-{int(day):02d}"


def _fetch(url: str) -> str:
    request = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; CONCOST-JiwonCOK-Radar/1.0)",
        "Accept": "text/html,application/xhtml+xml",
    })
    with urlopen(request, timeout=10) as response:
        raw = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, errors="replace")


def _page_context(html_text: str, title: str) -> str:
    plain = _clean(re.sub(r"<[^>]+>", " ", html_text))
    index = plain.find(title)
    if index < 0:
        return ""
    return plain[max(0, index - 140):index + len(title) + 180]


def _source_key(url: str) -> str:
    decoded = unquote(url)
    for pattern in (
        r"(?:not_ancmt_mgt_no|notAncmtMgtNo|seq|idx|no|boardId|articleNo|nttNo|mgtNo)=([\w-]+)",
        r"/(\d{4,})(?:[/?#]|$)",
    ):
        match = re.search(pattern, decoded, re.I)
        if match:
            return match.group(1)
    return hashlib.sha1(decoded.encode("utf-8")).hexdigest()[:16]


def _has_watch_keyword(text: str) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in WATCH_KEYWORDS)


def parse_source_page(html_text: str, base_url: str, institution: str = "") -> list[dict[str, Any]]:
    parser = _AnchorParser()
    parser.feed(html_text or "")
    today = datetime.now(SEOUL_TZ).date().isoformat()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for link in parser.links:
        title = _clean(link["title"])
        context = _page_context(html_text, title)
        target = urljoin(base_url, link["href"])
        if target in seen or not _has_watch_keyword(title):
            continue
        seen.add(target)
        dates = DATE_RE.findall(context)
        deadline = ""
        if dates:
            year, month, day = dates[-1]
            deadline = f"{year}-{int(month):02d}-{int(day):02d}"
        score, matched = score_notice(title, institution, context, "평가위원 모집", SOURCE)
        rows.append({
            "source": SOURCE,
            "source_key": f"{institution or 'agency'}-{_source_key(target)}",
            "category": "평가위원 모집",
            "title": title,
            "institution": institution,
            "published_at": today,
            "deadline_at": deadline,
            "estimated_price": None,
            "region": "",
            "notice_type": "신규",
            "change_reason": "",
            "changed_at": "",
            "url": target,
            "score": score,
            "matched_keywords": matched,
            "raw": {"context": context, "source_page": base_url},
        })
    return rows


def collect_source_page(source: dict[str, str]) -> list[dict[str, Any]]:
    page = _fetch(source["url"])
    return parse_source_page(page, source["url"], source.get("institution", ""))


def collect_recent(lookback_hours: int = 48) -> list[dict[str, Any]]:
    del lookback_hours  # 기관 게시판은 표준 날짜 필터가 없어 최신 목록에서 키워드로 선별한다.
    result: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=6, thread_name_prefix="jiwoncok-source") as pool:
        futures = [pool.submit(collect_source_page, source) for source in SOURCE_PAGES]
        for future in as_completed(futures):
            try:
                result.extend(future.result())
            except Exception:
                continue
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in result:
        key = (item["source"], item["source_key"])
        if key in seen:
            continue
        seen.add(key)
        if int(item.get("score") or 0) > 20:
            deduped.append(item)
    return deduped


def parse_jiwoncok_email(text: str, published_at: str | None = None) -> list[dict]:
    """Parse a forwarded 지원콕 notification email into CONCOST notice rows.

    지원콕 itself should not be scraped directly for this MVP. This parser only handles
    notification emails the company already receives and imports those links into the
    internal radar as the "지원COK" source.
    """
    published = published_at or datetime.now(SEOUL_TZ).date().isoformat()
    matches = list(LINK_RE.finditer(text or ""))
    notices: list[dict] = []
    ignored_titles = ("더 많은 공고", "여기", "jiwonkok.com", "지원콕")
    for index, match in enumerate(matches):
        title = _clean(match.group(1))
        if any(title.startswith(ignored) or title == ignored for ignored in ignored_titles):
            continue
        url, source_key = _direct_jiwoncok_url(match.group(2))
        if "jiwonkok.com/" not in unquote(match.group(2)) and "jiwonkok.com/" not in url:
            continue
        segment_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        segment = text[match.end():segment_end]
        institution = _field(segment, "공고기관")
        period = _field(segment, "접수기간")
        fields = _field(segment, "모집분야")
        score, matched = score_notice(title, institution, fields, "평가위원 모집", "지원COK")
        notices.append({
            "source": SOURCE,
            "source_key": source_key,
            "category": "평가위원 모집",
            "title": title,
            "institution": institution,
            "published_at": published,
            "deadline_at": _deadline(period),
            "estimated_price": None,
            "region": "",
            "notice_type": "신규",
            "change_reason": "",
            "changed_at": "",
            "url": url,
            "score": score,
            "matched_keywords": matched,
            "raw": {"period": period, "fields": fields, "source_url": match.group(2)},
        })
    return notices
