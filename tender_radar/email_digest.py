from __future__ import annotations

import html
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .db import connect, get_setting, init_db, list_digest_recipients
from .secrets_store import get_secret


SEOUL = ZoneInfo("Asia/Seoul")
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
RESEND_USER_AGENT = "QS-CONCOST/1.0 (+https://qs-concost.onrender.com/)"


def valid_email(value: str) -> bool:
    return bool(EMAIL_RE.fullmatch(value.strip()))


def build_resend_request(api_key: str, payload: bytes) -> Request:
    return Request(
        "https://api.resend.com/emails", data=payload, method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": RESEND_USER_AGENT,
        },
    )


def _rows(db_path: Path, kind: str, already_sent: bool, limit: int) -> list[dict]:
    table = "notices" if kind == "notice" else "news"
    sent_clause = "EXISTS" if already_sent else "NOT EXISTS"
    recent_clause = "" if kind == "notice" else "AND n.last_seen_at >= ?"
    params: list[object] = [kind]
    if kind == "news":
        params.append((datetime.now().astimezone() - timedelta(days=14)).isoformat(timespec="seconds"))
    params.append(limit)
    with connect(db_path) as conn:
        rows = conn.execute(
            f"""SELECT n.* FROM {table} n
            WHERE {sent_clause} (
                SELECT 1 FROM digest_delivery_items i
                JOIN digest_deliveries d ON d.id=i.delivery_id
                WHERE d.status='sent' AND i.item_kind=?
                AND i.source=n.source AND i.source_key=n.source_key
            ) {recent_clause}
            ORDER BY n.score DESC, n.published_at DESC LIMIT ?""",
            params,
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["matched_keywords"] = json.loads(item.get("matched_keywords", "[]"))
        except json.JSONDecodeError:
            item["matched_keywords"] = []
        result.append(item)
    return result


def _score_color(score: int) -> str:
    if score >= 70:
        return "#ed5b18"
    if score >= 45:
        return "#d58a13"
    return "#16745f"


def _notice_card(item: dict, is_new: bool, website_url: str) -> str:
    link = html.escape(item.get("url") or website_url, quote=True)
    title = html.escape(item.get("title", ""))
    institution = html.escape(item.get("institution", "") or "기관 미확인")
    deadline = html.escape(item.get("deadline_at", "") or "마감일 확인 필요")
    source = html.escape(item.get("source", ""))
    notice_type = html.escape(item.get("notice_type", "신규"))
    score = int(item.get("score") or 0)
    state = "NEW" if is_new else "기존 알림"
    state_bg = "#fff1e9" if is_new else "#eef2f4"
    state_color = "#ed5b18" if is_new else "#647580"
    price = item.get("estimated_price")
    price_text = f"예정금액 {price:,}원" if isinstance(price, int) and price else "금액 미정"
    return f"""
    <tr><td style="padding:0 0 12px">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #dfe5e8;border-radius:14px;background:#ffffff">
        <tr><td style="padding:18px">
          <table role="presentation" width="100%"><tr>
            <td width="64" valign="top"><div style="width:54px;height:54px;line-height:54px;text-align:center;border-radius:50%;background:{_score_color(score)};color:#fff;font-size:18px;font-weight:800">{score}</div><div style="font-size:10px;color:#7a8991;text-align:center;margin-top:5px">적합도</div></td>
            <td valign="top">
              <div style="margin-bottom:7px"><span style="display:inline-block;padding:4px 8px;border-radius:20px;background:{state_bg};color:{state_color};font-size:11px;font-weight:800">{state}</span> <span style="font-size:11px;color:#70818a">{source} · {notice_type}</span></div>
              <a href="{link}" style="font-size:16px;line-height:1.45;color:#102d3f;text-decoration:none;font-weight:800">{title}</a>
              <div style="font-size:12px;color:#687b85;margin-top:9px;line-height:1.6">{institution}<br>마감 {deadline} · {price_text}</div>
            </td>
          </tr></table>
        </td></tr>
      </table>
    </td></tr>"""


def _news_card(item: dict, is_new: bool, website_url: str) -> str:
    link = html.escape(item.get("url") or website_url, quote=True)
    title = html.escape(item.get("title", ""))
    summary = html.escape((item.get("summary") or "").strip())
    source = html.escape(item.get("source", ""))
    category = html.escape(item.get("category", ""))
    published = html.escape(item.get("published_at", ""))
    score = int(item.get("score") or 0)
    state = "NEW" if is_new else "기존 알림"
    return f"""
    <tr><td style="padding:0 0 10px">
      <a href="{link}" style="display:block;padding:16px 18px;border-left:4px solid {'#ed5b18' if is_new else '#9aa8af'};background:#f6f8f9;color:#102d3f;text-decoration:none;border-radius:8px">
        <span style="font-size:10px;font-weight:800;color:{'#ed5b18' if is_new else '#74858e'}">{state} · 관련도 {score}점</span>
        <strong style="display:block;font-size:15px;line-height:1.45;margin:5px 0">{title}</strong>
        <span style="font-size:11px;color:#71818a">{source} · {category} · {published}</span>
        {f'<span style="display:block;font-size:12px;color:#60727c;line-height:1.5;margin-top:7px">{summary[:180]}</span>' if summary else ''}
      </a>
    </td></tr>"""


def _section(title: str, subtitle: str, cards: list[str]) -> str:
    if not cards:
        return ""
    return f"""
      <tr><td style="padding:26px 24px 8px"><div style="font-size:20px;color:#102d3f;font-weight:900">{html.escape(title)}</div><div style="font-size:12px;color:#788991;margin-top:5px">{html.escape(subtitle)}</div></td></tr>
      <tr><td style="padding:0 24px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0">{''.join(cards)}</table></td></tr>"""


def build_email_digest(db_path: Path, website_url: str = "https://qs-concost.onrender.com/") -> dict:
    init_db(db_path)
    new_notices = _rows(db_path, "notice", False, 30)
    old_notices = _rows(db_path, "notice", True, 12)
    new_news = _rows(db_path, "news", False, 20)
    old_news = _rows(db_path, "news", True, 8)
    construction_new = [x for x in new_news if x.get("category") != "법규·제도 개정"]
    law_new = [x for x in new_news if x.get("category") == "법규·제도 개정"]
    construction_old = [x for x in old_news if x.get("category") != "법규·제도 개정"]
    law_old = [x for x in old_news if x.get("category") == "법규·제도 개정"]
    now = datetime.now(SEOUL)
    date_label = now.strftime("%Y년 %m월 %d일")
    subject = f"[CONCOST] {now:%Y-%m-%d} 건설 기회 브리핑 · 신규 공고 {len(new_notices)}건"
    logo_url = website_url.rstrip("/") + "/concost-logo.png"
    sections = [
        _section("신규 입찰공고", "오늘 처음 알려드리는 공고 · 적합도 높은 순", [_notice_card(x, True, website_url) for x in new_notices]),
        _section("기존 알림 프로젝트", "이전에 안내한 공고 중 계속 확인할 항목", [_notice_card(x, False, website_url) for x in old_notices]),
        _section("건설 주요뉴스", "수주·공사비·안전진단·재건축·재개발 인사이트", [_news_card(x, True, website_url) for x in construction_new] + [_news_card(x, False, website_url) for x in construction_old]),
        _section("법규·제도 개정", "조달·건설 관련 최신 법령 및 제도 변화", [_news_card(x, True, website_url) for x in law_new] + [_news_card(x, False, website_url) for x in law_old]),
    ]
    email_html = f"""<!doctype html><html lang="ko"><body style="margin:0;background:#edf1f3;font-family:Arial,'Apple SD Gothic Neo','Noto Sans KR',sans-serif;color:#102d3f">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#edf1f3"><tr><td align="center" style="padding:28px 10px">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:680px;background:#fff;border-radius:18px;overflow:hidden;box-shadow:0 8px 30px rgba(16,45,63,.09)">
      <tr><td style="padding:26px 28px;background:#20262b;border-top:5px solid #ed5b18"><img src="{html.escape(logo_url, quote=True)}" width="150" alt="CONCOST" style="display:block;background:#fff;border-radius:8px;padding:7px"><div style="color:#ff7a31;font-size:11px;letter-spacing:1.5px;font-weight:800;margin-top:20px">OPPORTUNITY INTELLIGENCE</div><div style="color:#fff;font-size:27px;font-weight:900;line-height:1.3;margin-top:5px">오늘의 건설 기회 브리핑</div><div style="color:#cbd3d7;font-size:13px;margin-top:8px">{date_label} · 입찰공고, 건설뉴스, 법규·제도 개정</div></td></tr>
      <tr><td style="padding:20px 24px 0"><table role="presentation" width="100%" style="background:#fff5ef;border-radius:12px"><tr><td style="padding:16px;text-align:center"><strong style="font-size:26px;color:#ed5b18">{len(new_notices)}</strong><br><span style="font-size:11px;color:#667984">신규 공고</span></td><td style="padding:16px;text-align:center"><strong style="font-size:26px;color:#102d3f">{len(construction_new)}</strong><br><span style="font-size:11px;color:#667984">건설뉴스</span></td><td style="padding:16px;text-align:center"><strong style="font-size:26px;color:#102d3f">{len(law_new)}</strong><br><span style="font-size:11px;color:#667984">법규·제도</span></td></tr></table></td></tr>
      {''.join(sections) or '<tr><td style="padding:40px 24px;text-align:center;color:#73858e">오늘 새로 안내할 정보가 없습니다.</td></tr>'}
      <tr><td align="center" style="padding:30px 24px"><a href="{html.escape(website_url, quote=True)}" style="display:inline-block;background:#ed5b18;color:#fff;text-decoration:none;font-weight:800;padding:14px 26px;border-radius:9px">QS_ConCost 바로가기 →</a><div style="font-size:11px;color:#809099;line-height:1.6;margin-top:18px">각 제목을 누르면 원문 공고 또는 CONCOST 사이트로 이동합니다.<br>본 메일은 관리자 주소록에 등록된 사내 수신자에게 발송됩니다.</div></td></tr>
      <tr><td style="background:#102d3f;color:#9fb0b9;padding:18px 24px;font-size:10px;text-align:center">© CONCOST · Construction Cost & Opportunity Intelligence</td></tr>
    </table></td></tr></table></body></html>"""
    text_lines = [subject, "", f"신규 입찰공고 {len(new_notices)}건"]
    for item in new_notices:
        text_lines.append(f"[{item.get('score', 0)}점] {item.get('title', '')} - {item.get('url') or website_url}")
    text_lines.extend(["", f"전체 보기: {website_url}"])
    return {
        "subject": subject,
        "html": email_html,
        "text": "\n".join(text_lines),
        "new_notices": new_notices,
        "old_notices": old_notices,
        "new_news": new_news,
        "old_news": old_news,
        "counts": {
            "new_notices": len(new_notices), "old_notices": len(old_notices),
            "new_news": len(new_news), "old_news": len(old_news),
        },
    }


def send_email_digest(db_path: Path, website_url: str = "https://qs-concost.onrender.com/") -> dict:
    digest = build_email_digest(db_path, website_url)
    recipients = [x["email"] for x in list_digest_recipients(db_path) if x["is_active"]]
    if not recipients:
        raise ValueError("활성화된 이메일 수신자가 없습니다.")
    api_key = get_secret(db_path, "resend_api_key", os.getenv("RESEND_API_KEY", ""))
    if not api_key:
        raise ValueError("Resend API 키가 설정되지 않았습니다.")
    from_email = get_setting(db_path, "digest_from_email", os.getenv("DIGEST_FROM_EMAIL", ""))
    if not from_email:
        raise ValueError("발신 이메일이 설정되지 않았습니다.")
    started = datetime.now(SEOUL).isoformat(timespec="seconds")
    counts = digest["counts"]
    with connect(db_path) as conn:
        cursor = conn.execute(
            """INSERT INTO digest_deliveries(started_at,status,subject,recipient_count,
            new_notice_count,existing_notice_count,new_news_count,existing_news_count)
            VALUES(?,'sending',?,?,?,?,?,?)""",
            (started, digest["subject"], len(recipients), counts["new_notices"],
             counts["old_notices"], counts["new_news"], counts["old_news"]),
        )
        delivery_id = int(cursor.lastrowid)
    payload = json.dumps({
        "from": from_email, "to": recipients, "subject": digest["subject"],
        "html": digest["html"], "text": digest["text"],
    }, ensure_ascii=False).encode("utf-8")
    request = build_resend_request(api_key, payload)
    try:
        with urlopen(request, timeout=30) as response:
            response_data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        error = exc.read().decode("utf-8", errors="replace")[:1000]
        if "error code: 1010" in error.lower():
            error = "Resend가 요청 식별 헤더를 차단했습니다(1010). 최신 서버 코드로 다시 실행하세요."
        with connect(db_path) as conn:
            conn.execute(
                "UPDATE digest_deliveries SET status='failed',completed_at=?,error=? WHERE id=?",
                (datetime.now(SEOUL).isoformat(timespec="seconds"), error, delivery_id),
            )
        raise RuntimeError(f"메일 API 발송 실패: {error}") from exc
    with connect(db_path) as conn:
        for kind, items, is_new in (
            ("notice", digest["new_notices"], 1), ("notice", digest["old_notices"], 0),
            ("news", digest["new_news"], 1), ("news", digest["old_news"], 0),
        ):
            conn.executemany(
                "INSERT OR IGNORE INTO digest_delivery_items(delivery_id,item_kind,source,source_key,is_new) VALUES(?,?,?,?,?)",
                [(delivery_id, kind, item["source"], item["source_key"], is_new) for item in items],
            )
        conn.execute(
            "UPDATE digest_deliveries SET status='sent',completed_at=? WHERE id=?",
            (datetime.now(SEOUL).isoformat(timespec="seconds"), delivery_id),
        )
    return {"ok": True, "delivery_id": delivery_id, "provider_id": response_data.get("id", ""),
            "recipient_count": len(recipients), **counts}
