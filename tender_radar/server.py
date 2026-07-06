from __future__ import annotations

import json
import secrets
import threading
import time
import webbrowser
from http.cookies import SimpleCookie
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .config import Settings
from .db import (
    authenticate_admin, change_admin_password, get_setting, list_notices, set_setting,
    list_news, prune_news, stats, update_status, upsert_news, upsert_notice,
)
from .official_news import collect_official_news
from .law_news import collect_law_news
from .collector import collect_all
from .digest import build_daily_digest
from .secrets_store import get_secret, migrate_secret, set_secret


STATIC_DIR = Path(__file__).resolve().parent / "static"


class Handler(BaseHTTPRequestHandler):
    settings: Settings
    sessions: dict[str, tuple[str, float]] = {}
    session_lock = threading.Lock()
    login_failures: dict[str, list[float]] = {}

    def _json(self, value: object, status: int = 200) -> None:
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict:
        length = min(int(self.headers.get("Content-Length", "0")), 20_000)
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
            if not session or session[1] < time.time():
                self.sessions.pop(token, None)
                return None
        return session[0]

    def _require_admin(self) -> str | None:
        username = self._admin_username()
        if not username:
            self._json({"error": "관리자 로그인이 필요합니다."}, 401)
        return username

    def _session_cookie(self, token: str, max_age: int = 28_800) -> None:
        self.send_header(
            "Set-Cookie",
            f"qs_admin_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}",
        )

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
            self._json(stats(self.settings.db_path))
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
                "api_key": get_secret(self.settings.db_path, "public_data_api_key"),
                "law_api_configured": bool(get_secret(self.settings.db_path, "law_api_oc")),
            })
            return
        if parsed.path == "/api/admin/digest-preview":
            if not self._require_admin():
                return
            self._json(build_daily_digest(self.settings.db_path))
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
            token = secrets.token_urlsafe(32)
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
        notices, sources = collect_all(service_key, lookback_hours)
        counts = {"inserted": 0, "updated": 0, "unchanged": 0}
        for notice in notices:
            counts[upsert_notice(self.settings.db_path, notice)] += 1
        news_counts = {"inserted": 0, "updated": 0}
        try:
            news_items = collect_official_news()
            for item in news_items:
                news_counts[upsert_news(self.settings.db_path, item)] += 1
            prune_news(self.settings.db_path, news_items)
            sources.append({"source": "공식 건설뉴스", "ok": True, "total": len(news_items)})
        except Exception as exc:
            sources.append({"source": "공식 건설뉴스", "ok": False, "total": 0, "error": str(exc)})
        law_key = get_secret(self.settings.db_path, "law_api_oc")
        if law_key:
            try:
                law_items = collect_law_news(law_key)
                for item in law_items:
                    news_counts[upsert_news(self.settings.db_path, item)] += 1
                prune_news(self.settings.db_path, law_items)
                sources.append({"source": "국가법령정보", "ok": True, "total": len(law_items)})
            except Exception as exc:
                sources.append({"source": "국가법령정보", "ok": False, "total": 0, "error": str(exc)})
        else:
            sources.append({"source": "국가법령정보", "ok": False, "total": 0, "error": "API 인증값 미설정"})
        self._json({
            "ok": any(item["ok"] for item in sources), "total": len(notices),
            "sources": sources, "news": news_counts, **counts,
        }, 200 if any(item["ok"] for item in sources) else 502)

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
                set_secret(self.settings.db_path, "public_data_api_key", api_key)
            if law_api_key:
                set_secret(self.settings.db_path, "law_api_oc", law_api_key)
            self._json({"ok": True, "law_api_configured": bool(get_secret(self.settings.db_path, "law_api_oc"))})
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
    migrate_secret(settings.db_path, "public_data_api_key", settings.service_key)
    handler = type("ConfiguredHandler", (Handler,), {"settings": settings})
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
