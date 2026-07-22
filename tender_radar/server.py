from __future__ import annotations

import json
import base64
import hashlib
import hmac
import os
import re
import secrets
import threading
import time
import webbrowser
from concurrent.futures import TimeoutError, ThreadPoolExecutor
from datetime import datetime
from http.cookies import SimpleCookie
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from .config import Settings
from .db import (
    authenticate_admin, change_admin_password, get_setting, list_notices, set_setting,
    delete_digest_recipient, init_db, list_digest_deliveries, list_digest_recipients,
    list_news, prune_news, save_digest_recipient, stats, update_status, upsert_news, upsert_notice,
)
from .collector import collect_all, collect_news
from . import expressway, g2b, jiwoncok, kapt, law_news, lh, official_news
from .email_digest import build_email_digest, send_email_digest, send_test_email, valid_email
from .jiwoncok import parse_jiwoncok_email
from .scoring import MIN_NOTICE_SCORE, should_keep_notice
from .secrets_store import get_secret, migrate_secret, set_secret


STATIC_DIR = Path(__file__).resolve().parent / "static"


def collection_job_timeout_seconds() -> float:
    try:
        value = float(os.getenv("COLLECTION_JOB_TIMEOUT_SECONDS", "285"))
    except ValueError:
        return 285.0
    if value <= 0:
        return 285.0
    return min(value, 285.0)


def is_kst_weekday(now: datetime) -> bool:
    return now.weekday() < 5


def in_collect_window(now: datetime) -> bool:
    minute = now.hour * 60 + now.minute
    return is_kst_weekday(now) and 9 * 60 <= minute < 9 * 60 + 10


def in_digest_window(now: datetime) -> bool:
    minute = now.hour * 60 + now.minute
    return is_kst_weekday(now) and 10 * 60 <= minute < 10 * 60 + 10


def in_digest_send_window(now: datetime) -> bool:
    minute = now.hour * 60 + now.minute
    return is_kst_weekday(now) and 10 * 60 <= minute < 10 * 60 + 10


def storage_is_persistent(db_path: Path) -> bool:
    """Return true only when the database is on a real persistent mount.

    A /var/data path alone is not proof of persistence on Render Free because the
    directory can live on the instance's ephemeral root filesystem.
    """
    override = os.getenv("PERSISTENT_STORAGE", "").strip().lower()
    if override in {"1", "true", "yes"}:
        return True
    if override in {"0", "false", "no"}:
        return False
    if os.name == "nt":
        return True
    try:
        target = str(db_path.resolve())
        for line in Path("/proc/mounts").read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            mount = parts[1].replace("\\040", " ")
            if mount != "/" and (target == mount or target.startswith(mount.rstrip("/") + "/")):
                return True
    except OSError:
        pass
    return False


def auto_collect_on_start_enabled() -> bool:
    # Existing Render services can retain an old AUTO_COLLECT_ON_START=true
    # value even after render.yaml changes. Production must have exactly one
    # collector (the GitHub 09:00 workflow), so ignore the stale value there.
    if os.getenv("RENDER", "").strip().lower() == "true":
        return False
    configured = os.getenv("AUTO_COLLECT_ON_START", "").strip().lower()
    if configured:
        return configured in {"1", "true", "yes"}
    return False


def internal_scheduler_enabled() -> bool:
    # Production scheduling belongs to the dedicated Render Cron services.
    # A scheduler inside every web process can duplicate delivery after deploys,
    # restarts, or scale-out regardless of local SQLite bookkeeping.
    if os.getenv("RENDER", "").strip().lower() == "true":
        return False
    return os.getenv("SCHEDULE_JOBS", "1").strip().lower() in {"1", "true", "yes"}


class Handler(BaseHTTPRequestHandler):
    settings: Settings
    sessions: dict[str, tuple[str, float]] = {}
    session_lock = threading.Lock()
    login_failures: dict[str, list[float]] = {}
    digest_lock = threading.Lock()
    collection_lock = threading.Lock()
    collection_lock_owner: str | None = None
    collection_jobs: dict[str, dict] = {}
    collection_jobs_lock = threading.Lock()

    def _json(self, value: object, status: int = 200) -> None:
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self, max_bytes: int = 20_000) -> dict:
        length = min(int(self.headers.get("Content-Length", "0")), max_bytes)
        value = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(value, dict):
            raise ValueError("JSON object required")
        return value

    def _admin_username(self) -> str | None:
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookie.get("qs_admin_session")
        if not morsel:
            return None
        token = morsel.value
        with self.session_lock:
            session = self.sessions.get(token)
            if session and session[1] >= time.time():
                return session[0]
            if session:
                self.sessions.pop(token, None)
        return self._verify_signed_session(token)

    def _session_signing_key(self) -> bytes:
        value = os.getenv("APP_SECRET_KEY", "").encode("utf-8")
        return value if len(value) >= 24 else b""

    def _make_signed_session(self, username: str, expires_at: float) -> str:
        key = self._session_signing_key()
        if not key:
            return ""
        version = get_setting(self.settings.db_path, "admin_session_version", "1")
        payload = f"{username}|{int(expires_at)}|{version}|{secrets.token_urlsafe(8)}".encode("utf-8")
        encoded = base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")
        signature = hmac.new(key, encoded.encode("ascii"), hashlib.sha256).hexdigest()
        return f"v1.{encoded}.{signature}"

    def _verify_signed_session(self, token: str) -> str | None:
        key = self._session_signing_key()
        if not key or not token.startswith("v1."):
            return None
        try:
            _, encoded, supplied_signature = token.split(".", 2)
            expected = hmac.new(key, encoded.encode("ascii"), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, supplied_signature):
                return None
            padded = encoded + "=" * (-len(encoded) % 4)
            username, expires, version, _ = base64.urlsafe_b64decode(padded).decode("utf-8").split("|", 3)
            if int(expires) < int(time.time()):
                return None
            if version != get_setting(self.settings.db_path, "admin_session_version", "1"):
                return None
            return username
        except (ValueError, UnicodeError):
            return None

    def _require_admin(self) -> str | None:
        username = self._admin_username()
        if not username:
            self._json({"error": "관리자 로그인이 필요합니다."}, 401)
        return username

    def _session_cookie(self, token: str, max_age: int = 28_800) -> None:
        secure = "; Secure" if os.getenv("COOKIE_SECURE", "").lower() in {"1", "true", "yes"} else ""
        self.send_header(
            "Set-Cookie",
            f"qs_admin_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}{secure}",
        )

    @classmethod
    def _update_collection_job(cls, job_id: str, **updates: object) -> None:
        with cls.collection_jobs_lock:
            job = cls.collection_jobs.get(job_id)
            if not job:
                return
            job.update(updates)
            job["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")

    @classmethod
    def _create_collection_job(cls, message: str = "수집 작업을 시작했습니다. 수집되는 자료부터 바로 저장합니다.") -> str:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        job_id = secrets.token_urlsafe(12)
        cls.collection_lock_owner = job_id
        with cls.collection_jobs_lock:
            cls.collection_jobs[job_id] = {
                "id": job_id, "status": "running", "ok": True, "partial": False,
                "started_at": now, "updated_at": now, "completed_at": "",
                "message": message,
                "percent": 1, "source_total": 1, "sources": [],
                "total": 0, "inserted": 0, "updated": 0, "unchanged": 0,
                "news_inserted": 0, "news_updated": 0,
            }
        return job_id

    @classmethod
    def _append_collection_source(cls, job_id: str, source_status: dict) -> None:
        with cls.collection_jobs_lock:
            job = cls.collection_jobs.get(job_id)
            if not job:
                return
            if job.get("status") != "running":
                return
            job["sources"].append(source_status)
            done = len(job["sources"])
            total = max(1, int(job.get("source_total") or 1))
            job["percent"] = min(99, round(done / total * 100))
            job["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")

    @classmethod
    def _get_collection_job(cls, job_id: str) -> dict | None:
        with cls.collection_jobs_lock:
            job = cls.collection_jobs.get(job_id)
            return json.loads(json.dumps(job, ensure_ascii=False)) if job else None

    @classmethod
    def _latest_running_collection_job(cls) -> dict | None:
        with cls.collection_jobs_lock:
            running = [job for job in cls.collection_jobs.values() if job.get("status") == "running"]
            if not running:
                return None
            return json.loads(json.dumps(sorted(running, key=lambda item: item["started_at"])[-1], ensure_ascii=False))

    @classmethod
    def _collection_job_is_stale(cls, job: dict | None) -> bool:
        if not job or job.get("status") != "running":
            return False
        try:
            started = datetime.fromisoformat(str(job.get("started_at", "")))
            if started.tzinfo is None:
                started = started.astimezone()
        except ValueError:
            return False
        max_age = 300.0
        return (datetime.now().astimezone() - started).total_seconds() > max_age

    @classmethod
    def _expire_collection_job(cls, job_id: str, reason: str = "제한시간 초과") -> dict | None:
        with cls.collection_jobs_lock:
            job = cls.collection_jobs.get(job_id)
            if not job or job.get("status") != "running":
                return json.loads(json.dumps(job, ensure_ascii=False)) if job else None
            known_sources = {source.get("source") for source in job.get("sources", [])}
            for source in ("나라장터", "LH", "도로공사", "공동주택관리정보시스템", "공식 건설뉴스", "국가법령정보"):
                if source not in known_sources:
                    job["sources"].append({"source": source, "ok": False, "total": 0, "error": reason})
            job.update({
                "status": "complete",
                "ok": any(source.get("ok") for source in job.get("sources", [])),
                "partial": True,
                "percent": 100,
                "message": "제한시간 안에 수집된 자료만 먼저 반영했습니다. 느린 소스는 다음 예약 수집에서 재시도합니다.",
                "completed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            })
            snapshot = json.loads(json.dumps(job, ensure_ascii=False))
        if cls.collection_lock_owner == job_id:
            cls.collection_lock_owner = None
            try:
                cls.collection_lock.release()
            except RuntimeError:
                pass
        return snapshot

    def _run_collection_job(
        self,
        job_id: str,
        service_key: str,
        law_key: str,
        lookback_hours: int,
        scopes: set[str] | None = None,
    ) -> None:
        def save_notices(rows: list[dict]) -> dict[str, int]:
            counts = {"inserted": 0, "updated": 0, "unchanged": 0}
            for notice in rows:
                counts[upsert_notice(self.settings.db_path, notice)] += 1
            return counts

        def save_news(rows: list[dict]) -> dict[str, int]:
            counts = {"inserted": 0, "updated": 0}
            for item in rows:
                counts[upsert_news(self.settings.db_path, item)] += 1
            if rows:
                prune_news(self.settings.db_path, rows)
            return counts

        notice_jobs = (
            ("g2b", "나라장터", lambda: g2b.collect_recent(service_key, lookback_hours)),
            ("lh", "LH", lambda: lh.collect_recent(service_key, lookback_hours)),
            ("expressway", "도로공사", lambda: expressway.collect_recent(lookback_hours)),
            ("kapt", "공동주택관리정보시스템", lambda: kapt.collect_recent(lookback_hours)),
            ("jiwoncok", "지원COK", lambda: jiwoncok.collect_recent(lookback_hours)),
        )
        news_jobs: list[tuple[str, str, object]] = [
            ("content", "공식 건설뉴스", official_news.collect_official_news)
        ]
        if law_key:
            news_jobs.append(("content", "국가법령정보", lambda: law_news.collect_law_news(law_key)))
        jobs = [
            ("notice", source, collect)
            for scope, source, collect in notice_jobs
            if scopes is None or scope in scopes
        ]
        jobs.extend(
            ("news", source, collect)
            for scope, source, collect in news_jobs
            if scopes is None or scope in scopes
        )
        missing_law = not law_key and (scopes is None or "content" in scopes)
        self._update_collection_job(job_id, source_total=len(jobs) + (1 if missing_law else 0))
        if missing_law:
            self._append_collection_source(job_id, {
                "source": "국가법령정보", "ok": False, "total": 0, "error": "API 인증값 미설정",
            })
        totals = {
            "total": 0, "inserted": 0, "updated": 0, "unchanged": 0,
            "news_inserted": 0, "news_updated": 0,
        }

        def collect_with_timeout(source: str, collect: object, timeout_seconds: float) -> tuple[list[dict], str]:
            pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"source-{source}")
            future = pool.submit(collect)
            try:
                return list(future.result(timeout=timeout_seconds) or []), ""
            except TimeoutError:
                future.cancel()
                return [], f"{int(timeout_seconds)}초 제한시간 초과"
            except Exception as exc:
                return [], str(exc)
            finally:
                pool.shutdown(wait=False, cancel_futures=True)

        try:
            deadline = time.monotonic() + collection_job_timeout_seconds()
            for index, (kind, source, collect) in enumerate(jobs):
                remaining = deadline - time.monotonic()
                if remaining <= 0.2:
                    self._append_collection_source(job_id, {
                        "source": source, "ok": False, "total": 0,
                        "error": "전체 5분 제한시간 초과",
                    })
                    continue
                per_source_timeout = min(35.0 if kind == "notice" else 25.0, remaining)
                self._update_collection_job(
                    job_id,
                    **totals,
                    message=f"{source} 수집 중입니다. {int(per_source_timeout)}초 안에 응답이 없으면 패스합니다.",
                )
                rows, error = collect_with_timeout(source, collect, per_source_timeout)
                if error:
                    self._append_collection_source(job_id, {
                        "source": source, "ok": False, "total": 0, "error": error,
                    })
                    self._update_collection_job(job_id, **totals, message=f"{source} 지연으로 패스하고 다음 소스로 넘어갑니다.")
                    continue
                if kind == "notice":
                    relevant = [row for row in rows if should_keep_notice(row)]
                    counts = save_notices(relevant)
                    totals["total"] += len(relevant)
                    totals["inserted"] += counts["inserted"]
                    totals["updated"] += counts["updated"]
                    totals["unchanged"] += counts["unchanged"]
                    self._append_collection_source(job_id, {
                        "source": source, "ok": True, "total": len(relevant),
                        "filtered": len(rows) - len(relevant),
                    })
                else:
                    counts = save_news(rows)
                    totals["news_inserted"] += counts["inserted"]
                    totals["news_updated"] += counts["updated"]
                    self._append_collection_source(job_id, {
                        "source": source, "ok": True, "total": len(rows),
                    })
                self._update_collection_job(job_id, **totals, message="수집된 자료부터 화면에 반영하고 있습니다.")
        except Exception as exc:
            self._append_collection_source(job_id, {
                "source": "수집 작업", "ok": False, "total": 0, "error": str(exc),
            })
        finally:
            job = self._get_collection_job(job_id) or {}
            ok_count = sum(1 for source in job.get("sources", []) if source.get("ok"))
            self._update_collection_job(
                job_id,
                status="complete",
                ok=ok_count > 0,
                partial=any(not source.get("ok") for source in job.get("sources", [])),
                percent=100,
                message="수집 완료" if ok_count else "수집된 자료가 없습니다. API 승인상태와 기관 응답상태를 확인하세요.",
                completed_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                **totals,
            )
            if type(self).collection_lock_owner == job_id:
                type(self).collection_lock_owner = None
                try:
                    self.collection_lock.release()
                except RuntimeError:
                    pass

    def _run_scheduled_collection_job(
        self,
        job_id: str,
        service_key: str,
        law_key: str,
        lookback_hours: int,
        scheduled_date: str,
        scopes: set[str] | None = None,
    ) -> None:
        """Run one bounded sweep without holding the automation request open."""
        self._run_collection_job(job_id, service_key, law_key, lookback_hours, scopes)
        job = self._get_collection_job(job_id) or {}
        if job.get("ok"):
            set_setting(self.settings.db_path, "last_scheduled_collect", scheduled_date)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/notices":
            params = parse_qs(parsed.query)
            try:
                min_score = int(params.get("min_score", ["0"])[0])
            except ValueError:
                min_score = 0
            self._json(list_notices(
                self.settings.db_path,
                query=params.get("q", [""])[0],
                category=params.get("category", [""])[0],
                source=params.get("source", [""])[0],
                notice_type=params.get("notice_type", [""])[0],
                min_score=min_score,
            ))
            return
        if parsed.path == "/api/stats":
            summary = stats(self.settings.db_path)
            summary["app_version"] = os.getenv("RENDER_GIT_COMMIT", "local")[:7]
            self._json(summary)
            return
        if parsed.path == "/api/news":
            params = parse_qs(parsed.query)
            self._json(list_news(
                self.settings.db_path,
                category=params.get("category", [""])[0],
                query=params.get("q", [""])[0],
            ))
            return
        if parsed.path == "/api/admin/session":
            username = self._admin_username()
            self._json({"authenticated": bool(username), "username": username or ""})
            return
        if parsed.path == "/api/admin/settings":
            if not self._require_admin():
                return
            self._json({
                "api_key_configured": bool(get_secret(self.settings.db_path, "public_data_api_key")),
                "law_api_configured": bool(get_secret(self.settings.db_path, "law_api_oc")),
            })
            return
        if parsed.path == "/api/admin/digest-preview":
            if not self._require_admin():
                return
            preview = build_email_digest(self.settings.db_path)
            self._json({
                "subject": preview["subject"], "html": preview["html"],
                "text": preview["text"], "counts": preview["counts"],
            })
            return
        if parsed.path == "/api/admin/email-settings":
            if not self._require_admin():
                return
            persistent_db = storage_is_persistent(self.settings.db_path)
            resend_env = bool(os.getenv("RESEND_API_KEY", "").strip())
            from_env = bool(os.getenv("DIGEST_FROM_EMAIL", "").strip())
            recipients_env = bool(os.getenv("DIGEST_RECIPIENTS", "").strip())
            environment_backed = all(os.getenv(key, "").strip() for key in (
                "RESEND_API_KEY", "DIGEST_FROM_EMAIL", "DIGEST_RECIPIENTS"
            ))
            recipients = list_digest_recipients(self.settings.db_path)
            provider_configured = bool(get_secret(self.settings.db_path, "resend_api_key"))
            from_email = get_setting(
                self.settings.db_path, "digest_from_email", os.getenv("DIGEST_FROM_EMAIL", "")
            )
            self._json({
                "recipients": recipients,
                "deliveries": list_digest_deliveries(self.settings.db_path),
                "provider_configured": provider_configured,
                "from_email": from_email,
                "enabled": get_setting(self.settings.db_path, "digest_enabled", "1") == "1",
                "schedule_time": get_setting(self.settings.db_path, "digest_schedule_time", "10:00"),
                "timezone": "Asia/Seoul",
                "storage_persistent": persistent_db,
                "environment_backed": environment_backed,
                "resend_environment_backed": resend_env,
                "from_environment_backed": from_env,
                "recipients_environment_backed": recipients_env,
                "resend_permanent": provider_configured and (resend_env or persistent_db),
                "from_permanent": bool(from_email) and (from_env or persistent_db),
                "recipients_permanent": bool(recipients) and (recipients_env or persistent_db),
            })
            return
        if parsed.path.startswith("/api/collect/status/"):
            if not self._require_admin():
                return
            job_id = parsed.path.rsplit("/", 1)[-1]
            job = self._get_collection_job(job_id)
            if not job:
                self._json({"error": "수집 작업을 찾을 수 없습니다."}, 404)
                return
            if self._collection_job_is_stale(job):
                job = self._expire_collection_job(job_id) or job
            self._json(job)
            return
        if parsed.path in {"/", "/index.html"}:
            path = STATIC_DIR / "index.html"
            data = path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if parsed.path == "/concost-logo.png":
            data = (STATIC_DIR / "concost-logo.png").read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        media_types = {
            "/concost-app-icon.png": ("concost-app-icon.png", "image/png"),
            "/hero-construction.mp4": ("hero-construction.mp4", "video/mp4"),
            "/hero-construction.jpg": ("hero-construction.jpg", "image/jpeg"),
        }
        if parsed.path in media_types:
            filename, content_type = media_types[parsed.path]
            data = (STATIC_DIR / filename).read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "public, max-age=86400")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/admin/login":
            try:
                payload = self._read_json()
            except (ValueError, json.JSONDecodeError):
                self._json({"error": "입력값을 확인하세요."}, 400)
                return
            ip = self.client_address[0]
            now = time.time()
            failures = [stamp for stamp in self.login_failures.get(ip, []) if stamp > now - 300]
            if len(failures) >= 5:
                self._json({"error": "로그인 시도가 너무 많습니다. 5분 후 다시 시도하세요."}, 429)
                return
            username, password = str(payload.get("username", "")), str(payload.get("password", ""))
            if not authenticate_admin(self.settings.db_path, username, password):
                failures.append(now)
                self.login_failures[ip] = failures
                self._json({"error": "아이디 또는 비밀번호가 올바르지 않습니다."}, 401)
                return
            self.login_failures.pop(ip, None)
            token = self._make_signed_session(username, now + 28_800) or secrets.token_urlsafe(32)
            with self.session_lock:
                self.sessions[token] = (username, now + 28_800)
            data = json.dumps({"ok": True, "username": username}, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._session_cookie(token)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if parsed.path == "/api/admin/logout":
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            morsel = cookie.get("qs_admin_session")
            if morsel:
                with self.session_lock:
                    self.sessions.pop(morsel.value, None)
            data = json.dumps({"ok": True}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._session_cookie("", 0)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if parsed.path == "/api/automation/collect":
            expected = os.getenv("DIGEST_TRIGGER_TOKEN", "")
            supplied = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
            if not expected or not secrets.compare_digest(expected, supplied):
                self._json({"error": "인증되지 않은 자동화 요청입니다."}, 401)
                return
        if parsed.path == "/api/automation/digest":
            expected = os.getenv("DIGEST_TRIGGER_TOKEN", "")
            supplied = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
            supplied_resend = self.headers.get("X-Resend-Api-Key", "").strip()
            token_ok = bool(expected and secrets.compare_digest(expected, supplied))
            resend_header_ok = bool(supplied_resend.startswith("re_"))
            if not token_ok and not resend_header_ok:
                self._json({"error": "인증되지 않은 자동화 요청입니다."}, 401)
                return
        if parsed.path == "/api/automation/collect":
            now_kst = datetime.now(ZoneInfo("Asia/Seoul"))
            if self.headers.get("X-Collect-Scheduled", "").strip().lower() == "true" and not is_kst_weekday(now_kst):
                self._json({
                    "ok": True, "skipped": True,
                    "reason": f"주말 예약 수집은 실행하지 않습니다. 현재 한국시간 {now_kst:%Y-%m-%d %H:%M}",
                })
                return
            if not self.collection_lock.acquire(blocking=False):
                running = self._latest_running_collection_job()
                self._json({
                    "ok": True,
                    "already_running": True,
                    "job_id": (running or {}).get("id", ""),
                    "job": running or {},
                }, 409)
                return
            try:
                service_key = get_secret(
                    self.settings.db_path,
                    "public_data_api_key",
                    self.settings.service_key,
                )
                law_key = get_secret(self.settings.db_path, "law_api_oc")
                allowed_scopes = {"g2b", "lh", "expressway", "kapt", "jiwoncok", "content"}
                requested_scopes = {
                    value.strip().lower()
                    for value in self.headers.get("X-Collect-Scopes", "").split(",")
                    if value.strip()
                }
                scopes = requested_scopes & allowed_scopes or None
                job_id = type(self)._create_collection_job("09:00 예약 자료 수집을 시작했습니다.")
                worker = threading.Thread(
                    target=self._run_scheduled_collection_job,
                    args=(
                        job_id,
                        service_key,
                        law_key,
                        self.settings.lookback_hours,
                        now_kst.date().isoformat(),
                        scopes,
                    ),
                    name=f"scheduled-collection-{job_id}",
                    daemon=True,
                )
                worker.start()
                self._json({
                    "ok": True,
                    "accepted": True,
                    "job_id": job_id,
                    "job": self._get_collection_job(job_id),
                }, 202)
            except Exception as exc:
                type(self).collection_lock_owner = None
                try:
                    self.collection_lock.release()
                except RuntimeError:
                    pass
                self._json({"error": str(exc)}, 502)
            return
        if parsed.path == "/api/automation/digest":
            if get_setting(self.settings.db_path, "digest_enabled", "1") != "1":
                self._json({"ok": True, "skipped": True, "reason": "예약 발송 꺼짐"})
                return
            now_kst = datetime.now(ZoneInfo("Asia/Seoul"))
            if self.headers.get("X-Digest-Scheduled", "").strip().lower() == "true" and not is_kst_weekday(now_kst):
                self._json({
                    "ok": True, "skipped": True,
                    "reason": f"주말 예약 메일은 발송하지 않습니다. 현재 한국시간 {now_kst:%Y-%m-%d %H:%M}",
                })
                return
            today = now_kst.date().isoformat()
            if get_setting(self.settings.db_path, "last_automation_digest", "") == today:
                self._json({"ok": True, "skipped": True, "reason": "오늘 예약 메일은 이미 발송되었습니다."})
                return
            if not self.digest_lock.acquire(blocking=False):
                self._json({"error": "이미 발송 작업이 진행 중입니다."}, 409)
                return
            try:
                # Render 무료 웹서비스는 절전/재시작 시 로컬 SQLite가 초기화될 수 있다.
                # 09시 수집 뒤 DB가 사라졌더라도 빈 메일을 보내지 않도록 10시 발송 직전에
                # 당일 수집 이력을 확인하고 필요하면 자료를 다시 채운다.
                last_collect = get_setting(self.settings.db_path, "last_scheduled_collect", "")
                if last_collect != today or stats(self.settings.db_path).get("total", 0) == 0:
                    if self.collection_lock.acquire(blocking=False):
                        try:
                            from .cli import collect
                            collect_result = collect()
                            if collect_result == 0:
                                set_setting(self.settings.db_path, "last_scheduled_collect", today)
                        finally:
                            self.collection_lock.release()
                recipients = [
                    email.strip().lower()
                    for email in self.headers.get("X-Digest-Recipients", "").split(",")
                    if valid_email(email.strip().lower())
                ]
                result = send_email_digest(
                    self.settings.db_path,
                    api_key_override=self.headers.get("X-Resend-Api-Key", "").strip(),
                    from_email_override=self.headers.get("X-Digest-From-Email", "").strip(),
                    recipients_override=recipients or None,
                    idempotency_key=f"concost-daily-digest-{today}",
                )
                set_setting(self.settings.db_path, "last_automation_digest", today)
                self._json(result)
            except Exception as exc:
                self._json({"error": str(exc)}, 502)
            finally:
                self.digest_lock.release()
            return
        if parsed.path == "/api/admin/recipients":
            if not self._require_admin():
                return
            try:
                payload = self._read_json()
                email = str(payload.get("email", "")).strip().lower()
                name = str(payload.get("name", "")).strip()
                if not valid_email(email):
                    raise ValueError("올바른 이메일 주소를 입력하세요.")
                self._json({"ok": True, "recipient": save_digest_recipient(self.settings.db_path, email, name)})
            except (ValueError, json.JSONDecodeError) as exc:
                self._json({"error": str(exc)}, 400)
            return
        if parsed.path == "/api/admin/send-digest":
            if not self._require_admin():
                return
            if not self.digest_lock.acquire(blocking=False):
                self._json({"error": "이미 발송 작업이 진행 중입니다."}, 409)
                return
            try:
                self._json(send_email_digest(self.settings.db_path))
            except Exception as exc:
                self._json({"error": str(exc)}, 502)
            finally:
                self.digest_lock.release()
            return
        if parsed.path == "/api/admin/test-email":
            if not self._require_admin():
                return
            try:
                self._json(send_test_email(self.settings.db_path))
            except Exception as exc:
                self._json({"error": str(exc)}, 502)
            return
        if parsed.path == "/api/admin/collect-news":
            if not self._require_admin():
                return
            law_key = get_secret(self.settings.db_path, "law_api_oc")
            news_items, news_sources = collect_news(law_key)
            news_counts = {"inserted": 0, "updated": 0}
            for item in news_items:
                news_counts[upsert_news(self.settings.db_path, item)] += 1
            if news_items:
                prune_news(self.settings.db_path, news_items)
            self._json({
                "ok": any(item["ok"] for item in news_sources),
                "total": len(news_items),
                "sources": news_sources,
                **news_counts,
            }, 200 if any(item["ok"] for item in news_sources) else 502)
            return
        if parsed.path == "/api/admin/collect-jiwoncok":
            if not self._require_admin():
                return
            if not self.collection_lock.acquire(blocking=False):
                self._json({"error": "전체 자료수집이 진행 중입니다. 완료 후 다시 실행하세요."}, 409)
                return
            try:
                payload = self._read_json()
                lookback_hours = max(1, min(int(payload.get("lookback_hours", 48)), 168))
            except (ValueError, TypeError, json.JSONDecodeError):
                lookback_hours = 48
            try:
                rows, statuses = jiwoncok.collect_recent_with_status(lookback_hours)
                counts = {"inserted": 0, "updated": 0, "unchanged": 0}
                for notice in rows:
                    counts[upsert_notice(self.settings.db_path, notice)] += 1
                self._json({
                    "ok": True,
                    "partial": any(not item.get("ok") for item in statuses),
                    "total": len(rows),
                    "sources": statuses,
                    **counts,
                })
            except Exception as exc:
                self._json({"error": str(exc)}, 502)
            finally:
                self.collection_lock.release()
            return
        if parsed.path == "/api/admin/import-jiwoncok":
            if not self._require_admin():
                return
            try:
                payload = self._read_json(250_000)
                text = str(payload.get("text", ""))
                parsed_rows = parse_jiwoncok_email(text)
                notices = [row for row in parsed_rows if should_keep_notice(row)]
                counts = {"inserted": 0, "updated": 0, "unchanged": 0}
                for notice in notices:
                    counts[upsert_notice(self.settings.db_path, notice)] += 1
                self._json({
                    "ok": True,
                    "parsed": len(parsed_rows),
                    "total": len(notices),
                    "filtered": len(parsed_rows) - len(notices),
                    **counts,
                })
            except (ValueError, json.JSONDecodeError) as exc:
                self._json({"error": str(exc)}, 400)
            except Exception as exc:
                self._json({"error": str(exc)}, 502)
            return
        if parsed.path != "/api/collect":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not self._require_admin():
            return
        try:
            payload = self._read_json()
            service_key = get_secret(self.settings.db_path, "public_data_api_key", self.settings.service_key)
            lookback_hours = max(1, min(int(payload.get("lookback_hours", 48)), 168))
        except (ValueError, TypeError, json.JSONDecodeError):
            self._json({"error": "입력값을 확인하세요."}, 400)
            return
        if not self.collection_lock.acquire(blocking=False):
            running = self._latest_running_collection_job()
            if running:
                if self._collection_job_is_stale(running):
                    self._expire_collection_job(running["id"])
                    if self.collection_lock.acquire(blocking=False):
                        running = None
                    else:
                        self._json({"error": "이전 수집 작업 정리 중입니다. 잠시 후 다시 시도하세요."}, 409)
                        return
                if running:
                    self._json({"ok": True, "already_running": True, "job_id": running["id"], "job": running})
                    return
            else:
                self._json({"error": "이미 수집 작업이 진행 중입니다."}, 409)
                return
        # Lock acquired for the new job from here. Never leave it held when DB
        # configuration loading fails between acquisition and worker startup.
        try:
            law_key = get_secret(self.settings.db_path, "law_api_oc")
            job_id = type(self)._create_collection_job()
        except Exception as exc:
            type(self).collection_lock_owner = None
            try:
                self.collection_lock.release()
            except RuntimeError:
                pass
            self._json({"error": f"수집 작업 준비 실패: {exc}"}, 502)
            return
        worker = threading.Thread(
            target=self._run_collection_job,
            args=(job_id, service_key, law_key, lookback_hours),
            name=f"collection-{job_id}",
            daemon=True,
        )
        worker.start()
        self._json({"ok": True, "job_id": job_id, "job": self._get_collection_job(job_id)}, 202)
        return

    def do_PUT(self) -> None:
        parsed = urlparse(self.path)
        username = self._require_admin()
        if not username:
            return
        try:
            payload = self._read_json()
        except (ValueError, json.JSONDecodeError):
            self._json({"error": "입력값을 확인하세요."}, 400)
            return
        if parsed.path == "/api/admin/settings":
            api_key = str(payload.get("api_key", "")).strip()
            law_api_key = str(payload.get("law_api_key", "")).strip()
            if not api_key and not law_api_key:
                self._json({"error": "변경할 API 인증값을 입력하세요."}, 400)
                return
            if api_key:
                try:
                    set_secret(self.settings.db_path, "public_data_api_key", api_key)
                except RuntimeError as exc:
                    self._json({"error": str(exc)}, 500)
                    return
            if law_api_key:
                try:
                    set_secret(self.settings.db_path, "law_api_oc", law_api_key)
                except RuntimeError as exc:
                    self._json({"error": str(exc)}, 500)
                    return
            self._json({
                "ok": True,
                "api_key_configured": bool(get_secret(self.settings.db_path, "public_data_api_key")),
                "law_api_configured": bool(get_secret(self.settings.db_path, "law_api_oc")),
            })
            return
        if parsed.path == "/api/admin/email-settings":
            resend_api_key = str(payload.get("resend_api_key", "")).strip()
            from_email = str(payload.get("from_email", "")).strip()
            schedule_time = str(payload.get("schedule_time", "10:00")).strip()
            if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", schedule_time):
                self._json({"error": "발송 시간 형식이 올바르지 않습니다."}, 400)
                return
            if from_email and not valid_email(from_email.split("<")[-1].rstrip("> ")):
                self._json({"error": "발신 이메일 형식을 확인하세요."}, 400)
                return
            if resend_api_key:
                try:
                    set_secret(self.settings.db_path, "resend_api_key", resend_api_key)
                except RuntimeError as exc:
                    self._json({"error": str(exc)}, 500)
                    return
            if from_email:
                set_setting(self.settings.db_path, "digest_from_email", from_email)
            set_setting(self.settings.db_path, "digest_enabled", "1" if payload.get("enabled", True) else "0")
            set_setting(self.settings.db_path, "digest_schedule_time", schedule_time)
            self._json({"ok": True, "provider_configured": bool(get_secret(self.settings.db_path, "resend_api_key"))})
            return
        if parsed.path == "/api/admin/password":
            current = str(payload.get("current_password", ""))
            new = str(payload.get("new_password", ""))
            if not authenticate_admin(self.settings.db_path, username, current):
                self._json({"error": "현재 비밀번호가 올바르지 않습니다."}, 400)
                return
            if len(new) < 10 or not any(c.isalpha() for c in new) or not any(c.isdigit() for c in new):
                self._json({"error": "새 비밀번호는 영문과 숫자를 포함해 10자 이상이어야 합니다."}, 400)
                return
            change_admin_password(self.settings.db_path, username, new)
            set_setting(self.settings.db_path, "admin_session_version", str(time.time_ns()))
            with self.session_lock:
                self.sessions.clear()
            data = json.dumps({"ok": True, "login_required": True}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._session_cookie("", 0)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        if not self._require_admin():
            return
        parts = parsed.path.strip("/").split("/")
        if len(parts) == 4 and parts[:3] == ["api", "admin", "recipients"]:
            try:
                recipient_id = int(parts[3])
            except ValueError:
                self._json({"error": "잘못된 수신자 번호입니다."}, 400)
                return
            if delete_digest_recipient(self.settings.db_path, recipient_id):
                self._json({"ok": True})
            else:
                self._json({"error": "수신자를 찾을 수 없습니다."}, 404)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_PATCH(self) -> None:
        parsed = urlparse(self.path)
        parts = parsed.path.strip("/").split("/")
        if len(parts) == 3 and parts[:2] == ["api", "notices"]:
            try:
                notice_id = int(parts[2])
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, json.JSONDecodeError):
                self._json({"error": "잘못된 요청"}, 400)
                return
            if update_status(self.settings.db_path, notice_id, payload.get("status", "")):
                self._json({"ok": True})
            else:
                self._json({"error": "상태 또는 공고를 확인하세요"}, 400)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: object) -> None:
        return


def serve(settings: Settings, open_browser: bool = False) -> None:
    init_db(settings.db_path)
    migrate_secret(settings.db_path, "public_data_api_key", settings.service_key)
    interval = max(0, int(os.getenv("AUTO_COLLECT_INTERVAL_MINUTES", "0") or "0"))
    if interval:
        def auto_collect() -> None:
            from .cli import collect
            while True:
                try:
                    collect()
                except Exception as exc:
                    print(f"자동수집 실패: {exc}")
                time.sleep(interval * 60)
        worker = threading.Thread(target=auto_collect, name="auto-collector", daemon=True)
        worker.start()
    handler = type("ConfiguredHandler", (Handler,), {"settings": settings})
    if auto_collect_on_start_enabled():
        def restore_ephemeral_database() -> None:
            # 서버가 먼저 응답 가능 상태가 된 뒤, 비어 있는 임시 DB만 백그라운드에서 복구한다.
            time.sleep(1)
            try:
                if stats(settings.db_path).get("total", 0) > 0:
                    return
                if not handler.collection_lock.acquire(blocking=False):
                    return
                service_key = get_secret(settings.db_path, "public_data_api_key", settings.service_key)
                law_key = get_secret(settings.db_path, "law_api_oc")
                job_id = handler._create_collection_job("서버 시작 후 비어 있는 자료를 자동 복구하고 있습니다.")
                # _run_collection_job only needs the configured settings; using the
                # same tracked job path lets the admin UI attach instead of seeing
                # an unexplained lock/409 failure during startup recovery.
                runner = object.__new__(handler)
                runner.settings = settings
                runner._run_collection_job(job_id, service_key, law_key, settings.lookback_hours)
                job = handler._get_collection_job(job_id) or {}
                if job.get("ok"):
                    today = datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
                    set_setting(settings.db_path, "last_scheduled_collect", today)
            except Exception as exc:
                print(f"시작 시 자료복구 실패: {exc}")
                handler.collection_lock_owner = None
                try:
                    handler.collection_lock.release()
                except RuntimeError:
                    pass

        restore_worker = threading.Thread(
            target=restore_ephemeral_database,
            name="startup-data-restore",
            daemon=True,
        )
        restore_worker.start()
    if internal_scheduler_enabled():
        def scheduled_jobs() -> None:
            from .cli import collect
            timezone = ZoneInfo("Asia/Seoul")
            while True:
                now = datetime.now(timezone)
                today = now.date().isoformat()
                if in_collect_window(now):
                    last = get_setting(settings.db_path, "last_scheduled_collect", "")
                    if last != today and Handler.collection_lock.acquire(blocking=False):
                        try:
                            set_setting(settings.db_path, "last_scheduled_collect", today)
                            collect()
                        except Exception as exc:
                            print(f"09:00 예약수집 실패: {exc}")
                        finally:
                            Handler.collection_lock.release()
                if in_digest_window(now):
                    last = get_setting(settings.db_path, "last_automation_digest", "")
                    enabled = get_setting(settings.db_path, "digest_enabled", "1") == "1"
                    if enabled and last != today and Handler.digest_lock.acquire(blocking=False):
                        try:
                            send_email_digest(
                                settings.db_path,
                                idempotency_key=f"concost-daily-digest-{today}",
                            )
                            set_setting(settings.db_path, "last_automation_digest", today)
                        except Exception as exc:
                            print(f"10:00 예약메일 실패: {exc}")
                        finally:
                            Handler.digest_lock.release()
                time.sleep(20)
        scheduler = threading.Thread(target=scheduled_jobs, name="kst-scheduler", daemon=True)
        scheduler.start()
    server = ThreadingHTTPServer((settings.host, settings.port), handler)
    url = f"http://{settings.host}:{settings.port}"
    print(f"QS 입찰 레이더: {url}")
    print("종료하려면 Ctrl+C")
    if open_browser:
        timer = threading.Timer(0.7, lambda: webbrowser.open(url))
        timer.daemon = True
        timer.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
