from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import unquote

from .scoring import score_notice


SEOUL_TZ = datetime.now().astimezone().tzinfo
LINK_RE = re.compile(r"\[([^\]]{8,240})\]\((https?://[^\s)]+)\)")
DATE_RE = re.compile(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})")


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip(" \t\r\n:-")


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
            "source": "지원COK",
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
