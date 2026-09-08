"""Stateless collection boundary for the Cloudflare migration trial.

No web application, SQLite, scheduler or mail imports. A timed-out child is
killed and reaped, unlike Future.cancel() on an already-running thread.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import apartment_api, expressway, g2b, jiwoncok, kapt, kwater, law_news, lh, nuri
from . import official_news, procurement_intelligence
from .scoring import score_notice, should_keep_notice

SEOUL = timezone(timedelta(hours=9))
PUBLIC_FIELDS = frozenset((
    "source", "source_key", "category", "title", "institution", "published_at",
    "deadline_at", "estimated_price", "region", "notice_type", "change_reason",
    "changed_at", "url", "score", "matched_keywords", "summary", "stage",
    "planned_at", "amount", "record_type", "notice_no", "base_amount",
    "award_amount", "contract_amount", "award_rate", "company", "recorded_at",
))


def sources(lookback: int = 168) -> dict:
    key = os.getenv("DATA_GO_KR_SERVICE_KEY", "")
    law_key = os.getenv("LAW_API_OC", "")

    def keyed(fn, credential):
        def collect():
            if not credential:
                raise ValueError("missing_credential")
            return fn(credential)
        return collect

    result = {
        "g2b": ("notice", "나라장터", keyed(lambda k: g2b.collect_recent(k, lookback), key)),
        "nuri": ("notice", "누리장터", keyed(lambda k: nuri.collect_recent(k, lookback), key)),
        "lh": ("notice", "LH", keyed(lambda k: lh.collect_recent(k, lookback), key)),
        "kwater": ("notice", "K-water", keyed(lambda k: kwater.collect_recent(k, lookback), key)),
        "expressway": ("notice", "도로공사", lambda: expressway.collect_recent(lookback)),
        "apartment": ("notice", "공동주택 API", keyed(lambda k: apartment_api.collect_recent(k, lookback), key)),
        "kapt": ("notice", "K-apt HTML", lambda: kapt.collect_recent(lookback)),
        "news": ("news", "건설뉴스", official_news.collect_official_news),
        "law": ("news", "법규·제도", keyed(law_news.collect_law_news, law_key)),
        "pipeline": ("pipeline", "사전 사업정보", keyed(lambda k: procurement_intelligence.collect_pipeline(k, lookback), key)),
        "cost": ("cost", "공사비 분석", keyed(lambda k: procurement_intelligence.collect_cost_records(k, max(168, lookback)), key)),
    }
    for agency in jiwoncok.active_source_pages():
        ident = hashlib.sha256(agency["url"].encode()).hexdigest()[:12]
        result[f"jiwon-{ident}"] = (
            "notice", agency["institution"],
            lambda a=agency: jiwoncok.collect_source_page(a),
        )
    return result


def collect_source(source_id: str, lookback: int = 168) -> dict:
    kind, label, collect = sources(lookback)[source_id]
    cutoff = (datetime.now(SEOUL) - timedelta(hours=lookback)).date().isoformat()
    rows = collect()
    kept = {}
    for raw in rows:
        row = {k: v for k, v in raw.items() if k in PUBLIC_FIELDS}
        if not row.get("source") or not row.get("source_key") or not row.get("title"):
            raise ValueError("invalid_source_record")
        if kind == "notice":
            row["score"], row["matched_keywords"] = score_notice(
                row["title"], row.get("institution", ""), row.get("category", ""))
            if not should_keep_notice(row):
                continue
            if row.get("published_at") and row["published_at"][:10] < cutoff:
                continue
        key = (row["source"], str(row["source_key"]), row.get("stage", row.get("record_type", "")))
        kept[key] = row
    return {"ok": True, "source_id": source_id, "label": label, "kind": kind,
            "candidates": len(rows), "kept": len(kept), "filtered": len(rows) - len(kept),
            "rows": list(kept.values())}


def run_command(command: list[str], timeout: float) -> dict:
    """Internal subprocess boundary; command is never supplied by an HTTP client."""
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            capture_output=True, timeout=timeout, encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        if completed.returncode:
            result = {"ok": False, "error": "collector_process_failed", "rows": []}
        else:
            result = json.loads(completed.stdout)
            if not isinstance(result, dict) or not isinstance(result.get("ok"), bool) or not isinstance(result.get("rows"), list):
                raise ValueError("invalid_result")
    except subprocess.TimeoutExpired:
        # subprocess.run kills AND waits for the child before raising.
        result = {"ok": False, "error": "source_deadline_exceeded", "rows": []}
    except (ValueError, OSError):
        result = {"ok": False, "error": "invalid_collector_response", "rows": []}
    return {**result, "duration_ms": round((time.monotonic() - started) * 1000)}


def run_isolated(source_id: str, lookback: int = 168, timeout: float = 180) -> dict:
    if source_id not in sources() or not 1 <= lookback <= 168:
        raise ValueError("invalid_source_or_lookback")
    timeout = min(240, max(0.1, timeout))
    result = run_command(
        [sys.executable, "-m", "tender_radar.isolated_collector", source_id, str(lookback)], timeout)
    return {**result, "source_id": source_id}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass  # No request headers, keys or upstream URLs in access logs.

    def reply(self, body, status=200):
        payload = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path == "/health":
            self.reply({"ok": True, "role": "stateless-collector", "mail_enabled": False})
        elif self.path == "/sources":
            self.reply([{"id": k, "kind": v[0], "label": v[1]} for k, v in sources().items()])
        else:
            self.reply({"error": "not_found"}, 404)

    def do_POST(self):
        # Container is only reachable through a Worker binding. No public proxy.
        if self.path != "/collect":
            self.reply({"error": "not_found"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4096:
                raise ValueError("invalid_length")
            body = json.loads(self.rfile.read(length))
            self.reply(run_isolated(body["source_id"], int(body.get("lookback", 168)),
                                    float(body.get("timeout", 180))))
        except (KeyError, ValueError, TypeError):
            self.reply({"error": "invalid_request"}, 400)


if __name__ == "__main__":
    if len(sys.argv) == 1:
        ThreadingHTTPServer(("0.0.0.0", int(os.getenv("PORT", "8080"))), Handler).serve_forever()
    else:
        try:
            # Some existing collectors print diagnostics; stdout is JSON only.
            with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink):
                result = collect_source(sys.argv[1], int(sys.argv[2]))
        except Exception as exc:
            # Exception text may contain a serviceKey in the upstream URL.
            result = {"ok": False, "error": type(exc).__name__, "rows": []}
        print(json.dumps(result, ensure_ascii=False), flush=True)
        # Existing parsers may leave timed-out threads alive. This process has
        # no DB/files/mail to flush: terminate them after the final JSON result.
        os._exit(0)
