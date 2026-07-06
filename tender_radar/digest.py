from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from .db import connect


def build_daily_digest(db_path: Path, hours: int = 24, limit: int = 10) -> dict:
    cutoff = (datetime.now().astimezone() - timedelta(hours=hours)).isoformat(timespec="seconds")
    with connect(db_path) as conn:
        rows = conn.execute(
            """SELECT source,title,institution,deadline_at,score,url,notice_type
            FROM notices WHERE first_seen_at >= ?
            ORDER BY score DESC, published_at DESC LIMIT ?""",
            (cutoff, limit),
        ).fetchall()
        source_rows = conn.execute(
            "SELECT source,COUNT(*) count FROM notices WHERE first_seen_at >= ? GROUP BY source",
            (cutoff,),
        ).fetchall()
    source_counts = Counter({row["source"]: row["count"] for row in source_rows})
    total = sum(source_counts.values())
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [f"[QS 입찰 레이더] {today}", f"최근 {hours}시간 신규·갱신 공고 {total}건"]
    if source_counts:
        lines.append("출처: " + " · ".join(f"{name} {count}건" for name, count in source_counts.items()))
    lines.append("")
    for index, row in enumerate(rows, 1):
        deadline = row["deadline_at"] or "마감 확인 필요"
        lines.append(f"{index}. [{row['score']}점/{row['source']}/{row['notice_type']}] {row['title']}")
        lines.append(f"   {row['institution']} · 마감 {deadline}")
        if row["url"]:
            lines.append(f"   {row['url']}")
    if not rows:
        lines.append("새로 확인된 공고가 없습니다.")
    return {
        "subject": f"QS 입찰 레이더 {today} - 신규 {total}건",
        "text": "\n".join(lines),
        "total": total,
        "source_counts": dict(source_counts),
    }
