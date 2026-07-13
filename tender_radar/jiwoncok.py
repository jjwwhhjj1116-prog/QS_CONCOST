from __future__ import annotations

import re
import hashlib
import html
import os
from concurrent.futures import TimeoutError, ThreadPoolExecutor, as_completed
from datetime import datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import unquote, urljoin
from urllib.request import Request, urlopen

from .scoring import MIN_NOTICE_SCORE, score_notice


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
BOARD_HINTS = (
    "고시", "공고", "공지", "알림", "입찰", "계약", "채용", "모집",
    "새소식", "소식", "행정", "정보공개", "board", "notice", "bbs",
    "gosi", "eminwon", "saeol",
)

# 1차 MVP 소스. 지원콕 공개 목록/알림메일에서 반복 등장한 기관을 시작점으로 두고,
# 정확한 게시판 URL을 아는 곳은 URL을 직접, 모르는 곳은 홈페이지에서 게시판 후보를 찾아 들어간다.
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
    {"institution": "의성군", "url": "https://www.usc.go.kr/"},
    {"institution": "안산시", "url": "https://www.ansan.go.kr/"},
    {"institution": "상주시", "url": "https://www.sangju.go.kr/"},
    {"institution": "남원시", "url": "https://www.namwon.go.kr/"},
    {"institution": "장흥군", "url": "https://www.jangheung.go.kr/"},
    {"institution": "포천시", "url": "https://www.pocheon.go.kr/"},
    {"institution": "음성군", "url": "https://www.eumseong.go.kr/"},
    {"institution": "청도군", "url": "https://www.cheongdo.go.kr/"},
    {"institution": "진도군", "url": "https://www.jindo.go.kr/"},
    {"institution": "경남투자경제진흥원", "url": "https://www.giba.or.kr/"},
    {"institution": "김포시", "url": "https://www.gimpo.go.kr/"},
    {"institution": "곡성군", "url": "https://www.gokseong.go.kr/"},
    {"institution": "청송군", "url": "https://www.cs.go.kr/"},
    {"institution": "예천군", "url": "https://www.ycg.kr/"},
    {"institution": "경기도", "url": "https://www.gg.go.kr/"},
    {"institution": "강서구시설관리공단", "url": "https://www.gssi.or.kr/"},
    {"institution": "경기도수원월드컵경기장관리재단", "url": "https://www.suwonworldcup.or.kr/"},
    {"institution": "전라남도정보문화산업진흥원", "url": "https://www.jcia.or.kr/"},
    {"institution": "경기주택도시공사", "url": "https://www.gh.or.kr/"},
    {"institution": "부산광역시 부산진구", "url": "https://www.busanjin.go.kr/"},
    {"institution": "화성시체육회", "url": "https://www.hssports.or.kr/"},
    {"institution": "제주특별자치도교육청", "url": "https://www.jje.go.kr/"},
    {"institution": "아산시", "url": "https://www.asan.go.kr/"},
    {"institution": "국가신약개발재단", "url": "https://www.kddf.org/"},
    {"institution": "경상북도문화관광공사", "url": "https://www.gtc.co.kr/"},
    {"institution": "광양시", "url": "https://www.gwangyang.go.kr/"},
    {"institution": "가평군", "url": "https://www.gp.go.kr/"},
    {"institution": "화성시인재육성재단", "url": "https://www.hstree.org/"},
    {"institution": "진주문화관광재단", "url": "https://www.jjct.or.kr/"},
    {"institution": "철원군", "url": "https://www.cwg.go.kr/"},
    {"institution": "강원특별자치도 소방본부", "url": "https://fire.gwd.go.kr/"},
    {"institution": "구로구", "url": "https://www.guro.go.kr/"},
    {"institution": "완도군", "url": "https://www.wando.go.kr/"},
    {"institution": "고흥군", "url": "https://www.goheung.go.kr/"},
    {"institution": "충청남도", "url": "https://www.chungnam.go.kr/"},
    {"institution": "용인시", "url": "https://www.yongin.go.kr/"},
    {"institution": "울산광역시", "url": "https://www.ulsan.go.kr/"},
    {"institution": "구미전자정보기술원", "url": "https://www.geri.re.kr/"},
    {"institution": "한국정신문화재단", "url": "https://www.kfce.or.kr/"},
    {"institution": "경상남도 고성군", "url": "https://www.goseong.go.kr/"},
    {"institution": "홍천군", "url": "https://www.hongcheon.go.kr/"},
    {"institution": "여수시", "url": "https://www.yeosu.go.kr/"},
    {"institution": "양평군", "url": "https://www.yp21.go.kr/"},
    {"institution": "광주광역시도시공사", "url": "https://www.gmcc.co.kr/"},
    {"institution": "시흥시", "url": "https://www.siheung.go.kr/"},
    {"institution": "영광군", "url": "https://www.yeonggwang.go.kr/"},
    {"institution": "전북특별자치도", "url": "https://www.jeonbuk.go.kr/"},
    {"institution": "동두천시", "url": "https://www.ddc.go.kr/"},
    {"institution": "세종특별자치시", "url": "https://www.sejong.go.kr/"},
    {"institution": "울산광역시 울주군", "url": "https://www.ulju.ulsan.kr/"},
    {"institution": "안양시", "url": "https://www.anyang.go.kr/"},
    {"institution": "안성시", "url": "https://www.anseong.go.kr/"},
    {"institution": "공주시", "url": "https://www.gongju.go.kr/"},
    {"institution": "대구광역시 남구", "url": "https://nam.daegu.kr/"},
    {"institution": "강원특별자치도교육청", "url": "https://www.gwe.go.kr/"},
    {"institution": "제주연구원", "url": "https://www.jri.re.kr/"},
    {"institution": "천안도시공사", "url": "https://www.cfmc.or.kr/"},
    {"institution": "광주광역시 서구", "url": "https://www.seogu.gwangju.kr/"},
    {"institution": "부천시", "url": "https://www.bucheon.go.kr/"},
    {"institution": "성남시", "url": "https://www.seongnam.go.kr/"},
    {"institution": "춘천시", "url": "https://www.chuncheon.go.kr/"},
    {"institution": "영덕군", "url": "https://www.yd.go.kr/"},
    {"institution": "안산시청소년재단", "url": "https://www.ansanyouth.or.kr/"},
    {"institution": "사천시", "url": "https://www.sacheon.go.kr/"},
    {"institution": "광주인재평생교육진흥원", "url": "https://www.gie.kr/"},
    {"institution": "정읍시", "url": "https://www.jeongeup.go.kr/"},
    {"institution": "고양시 일산동구", "url": "https://www.goyang.go.kr/ilsandong/"},
    {"institution": "서울특별시 동대문구", "url": "https://www.ddm.go.kr/"},
    {"institution": "화성시복지재단", "url": "https://www.hswf.or.kr/"},
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


def _has_board_hint(text: str) -> bool:
    lowered = text.lower()
    return any(hint.lower() in lowered for hint in BOARD_HINTS)


def _same_site(url: str, base_url: str) -> bool:
    try:
        from urllib.parse import urlparse
        target_host = urlparse(url).netloc.lower().removeprefix("www.")
        base_host = urlparse(base_url).netloc.lower().removeprefix("www.")
    except Exception:
        return False
    return bool(target_host and base_host and target_host == base_host)


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


def discover_board_urls(html_text: str, base_url: str, limit: int = 3) -> list[str]:
    parser = _AnchorParser()
    parser.feed(html_text or "")
    candidates: list[tuple[int, str]] = []
    seen: set[str] = set()
    for link in parser.links:
        title = _clean(link["title"])
        target = urljoin(base_url, link["href"])
        if target in seen or not target.lower().startswith("http") or not _same_site(target, base_url):
            continue
        combined = f"{title} {target}"
        if not _has_board_hint(combined):
            continue
        seen.add(target)
        score = 0
        if any(word in combined for word in ("고시", "공고", "입찰", "계약")):
            score += 8
        if any(word in combined for word in ("공지", "알림", "새소식", "모집")):
            score += 5
        if any(word in combined.lower() for word in ("gosi", "eminwon", "saeol", "notice", "board")):
            score += 3
        candidates.append((score, target))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [url for _, url in candidates[:limit]]


def source_pages_from_env() -> list[dict[str, str]]:
    """Allow operations to add more agencies without code changes.

    Format:
    JIWONCOK_SOURCE_PAGES="기관명|https://example.go.kr,다른기관|https://example.or.kr"
    """
    configured = os.getenv("JIWONCOK_SOURCE_PAGES", "")
    result: list[dict[str, str]] = []
    for chunk in configured.split(","):
        if "|" not in chunk:
            continue
        institution, url = [part.strip() for part in chunk.split("|", 1)]
        if institution and url.startswith("http"):
            result.append({"institution": institution, "url": url})
    return result


def collect_source_page(source: dict[str, str]) -> list[dict[str, Any]]:
    root = _fetch(source["url"])
    urls = [source["url"], *discover_board_urls(root, source["url"])]
    result: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for url in urls:
        if url in seen_urls:
            continue
        seen_urls.add(url)
        try:
            page = root if url == source["url"] else _fetch(url)
        except Exception:
            continue
        result.extend(parse_source_page(page, url, source.get("institution", "")))
    return result


def collect_recent(lookback_hours: int = 48) -> list[dict[str, Any]]:
    del lookback_hours  # 기관 게시판은 표준 날짜 필터가 없어 최신 목록에서 키워드로 선별한다.
    result: list[dict[str, Any]] = []
    sources = [*SOURCE_PAGES, *source_pages_from_env()]
    max_workers = max(1, min(8, len(sources)))
    try:
        timeout_seconds = max(5.0, min(float(os.getenv("JIWONCOK_TIMEOUT_SECONDS", "35")), 240.0))
    except ValueError:
        timeout_seconds = 35.0
    pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="jiwoncok-source")
    futures = [pool.submit(collect_source_page, source) for source in sources]
    try:
        for future in as_completed(futures, timeout=timeout_seconds):
            try:
                result.extend(future.result())
            except Exception:
                continue
    except TimeoutError:
        # 기관 수가 많아도 특정 지자체 사이트 하나 때문에 전체 수집이 멈추지 않게 한다.
        # 제한시간 안에 모인 결과만 먼저 반환하고 다음 예약 수집에서 보강한다.
        pass
    finally:
        for future in futures:
            future.cancel()
        pool.shutdown(wait=False, cancel_futures=True)
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in result:
        key = (item["source"], item["source_key"])
        if key in seen:
            continue
        seen.add(key)
        if int(item.get("score") or 0) >= MIN_NOTICE_SCORE:
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
