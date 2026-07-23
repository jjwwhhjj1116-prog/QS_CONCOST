import json
import threading
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from pathlib import Path

from tender_radar.db import (
    connect, delete_digest_recipient, list_digest_recipients, save_digest_recipient,
    get_setting, init_db, list_notices, set_setting, upsert_news, upsert_notice,
)
from tender_radar.email_digest import SEOUL, build_email_digest, build_resend_request, send_test_email
from tender_radar.collector import collect_all
from tender_radar.config import Settings
from tender_radar.g2b import fetch_category, normalize_item
from tender_radar.expressway import normalize_item as normalize_ex_item
from tender_radar.lh import normalize_item as normalize_lh_item
from tender_radar.kapt import normalize_item as normalize_kapt_item, parse_list as parse_kapt_list
from tender_radar.industry_news import parse_cerik, parse_constimes, parse_ricon
from tender_radar.jiwoncok import (
    active_source_pages, collect_recent_with_status, discover_board_urls,
    parse_jiwoncok_email, parse_source_page,
)
from tender_radar.scoring import MIN_NOTICE_SCORE, score_notice, should_keep_notice
from tender_radar.secrets_store import get_secret
from tender_radar.server import (
    Handler, auto_collect_on_start_enabled, internal_scheduler_enabled, in_collect_window,
    in_digest_send_window, in_digest_window,
)


class MVPTests(unittest.TestCase):
    def test_render_does_not_start_a_duplicate_collector(self):
        with patch.dict("os.environ", {"RENDER": "true", "AUTO_COLLECT_ON_START": "true"}, clear=True):
            self.assertFalse(auto_collect_on_start_enabled())
        with patch.dict("os.environ", {"RENDER": "true", "AUTO_COLLECT_ON_START": "false"}, clear=True):
            self.assertFalse(auto_collect_on_start_enabled())

    def test_render_web_process_never_runs_internal_scheduler(self):
        with patch.dict("os.environ", {"RENDER": "true", "SCHEDULE_JOBS": "true"}, clear=True):
            self.assertFalse(internal_scheduler_enabled())
        with patch.dict("os.environ", {"SCHEDULE_JOBS": "true"}, clear=True):
            self.assertTrue(internal_scheduler_enabled())

    def test_resend_request_accepts_daily_idempotency_key(self):
        request = build_resend_request("re_test", b"{}", "concost-daily-digest-2026-07-20")
        self.assertEqual(
            request.get_header("Idempotency-key"),
            "concost-daily-digest-2026-07-20",
        )

    def test_startup_collection_job_is_visible_to_admin_polling(self):
        with Handler.collection_jobs_lock:
            Handler.collection_jobs = {}
        Handler.collection_lock_owner = None
        job_id = Handler._create_collection_job("서버 시작 자동 복구")
        try:
            running = Handler._latest_running_collection_job()
            self.assertEqual(running["id"], job_id)
            self.assertEqual(running["status"], "running")
            self.assertIn("자동 복구", running["message"])
        finally:
            with Handler.collection_jobs_lock:
                Handler.collection_jobs = {}
            Handler.collection_lock_owner = None

    def test_unreadable_optional_secret_does_not_abort_collection(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ", {"APP_SECRET_KEY": "test-master-key-that-is-long-enough"}, clear=True
        ):
            db = Path(tmp) / "test.db"
            init_db(db)
            set_setting(db, "law_api_oc", "portable:not-valid-encrypted-data")
            self.assertEqual(get_secret(db, "law_api_oc", ""), "")

    def test_signed_admin_session_survives_process_memory_reset(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ", {"APP_SECRET_KEY": "stable-render-session-secret-123456"}, clear=True
        ):
            db = Path(tmp) / "test.db"
            init_db(db)
            handler = object.__new__(Handler)
            handler.settings = Settings("", 48, db, "127.0.0.1", 0)
            token = handler._make_signed_session("concost", time.time() + 600)
            handler.headers = {"Cookie": f"qs_admin_session={token}"}
            with Handler.session_lock:
                Handler.sessions = {}
            self.assertEqual(handler._admin_username(), "concost")

    def test_scheduled_automation_runs_without_admin_session(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ", {"DIGEST_TRIGGER_TOKEN": "automation-token"}, clear=True
        ), patch("tender_radar.server.is_kst_weekday", return_value=True), patch(
            "tender_radar.server.in_collect_window", return_value=True
        ), patch(
            "tender_radar.server.in_digest_send_window", return_value=True
        ), patch(
            "tender_radar.server.Handler._create_collection_job", return_value="scheduled-job"
        ), patch(
            "tender_radar.server.threading.Thread"
        ) as thread_mock, patch(
            "tender_radar.server.Handler._get_collection_job", return_value={"ok": True, "status": "complete"}
        ):
            db = Path(tmp) / "test.db"
            init_db(db)
            settings = Settings("", 48, db, "127.0.0.1", 0)

            collect_responses = []
            collect_handler = object.__new__(Handler)
            collect_handler.settings = settings
            collect_handler.path = "/api/automation/collect"
            collect_handler.headers = {
                "Authorization": "Bearer automation-token",
                "X-Collect-Scheduled": "true",
                "X-Collect-Scopes": "g2b,jiwoncok",
            }
            collect_handler._json = lambda payload, status=200: collect_responses.append((payload, status))
            collect_handler.do_POST()
            self.assertTrue(collect_responses[0][0]["ok"])
            self.assertEqual(collect_responses[0][1], 202)
            thread_mock.return_value.start.assert_called_once()
            self.assertEqual(thread_mock.call_args.kwargs["args"][-1], {"g2b", "jiwoncok"})
            Handler.collection_lock_owner = None
            if Handler.collection_lock.locked():
                Handler.collection_lock.release()

            digest_responses = []
            digest_handler = object.__new__(Handler)
            digest_handler.settings = settings
            digest_handler.path = "/api/automation/digest"
            digest_handler.headers = {
                "Authorization": "Bearer automation-token",
                "X-Digest-Scheduled": "true",
                "X-Resend-Api-Key": "re_test",
                "X-Digest-From-Email": "CONCOST <news@con-cost.co.kr>",
                "X-Digest-Recipients": "team@con-cost.co.kr",
            }
            digest_handler._json = lambda payload, status=200: digest_responses.append((payload, status))
            with patch("tender_radar.server.build_email_digest", return_value={
                "counts": {"new_notices": 1, "old_notices": 0, "new_news": 0, "old_news": 0}
            }), patch("tender_radar.server.send_email_digest", return_value={"ok": True}) as send_mock, patch(
                "tender_radar.cli.collect"
            ) as collect_mock:
                digest_handler.do_POST()
            self.assertTrue(digest_responses[0][0]["ok"])
            self.assertTrue(send_mock.call_args.kwargs["idempotency_key"].startswith("concost-daily-digest-"))
            collect_mock.assert_not_called()

    def test_qs_notice_scores_high(self):
        score, matched = score_notice("청사 신축공사 공사비 검증 및 VE 용역", "서울시")
        self.assertGreaterEqual(score, 70)
        self.assertTrue(any("공사비" in item for item in matched))

    def test_scheduled_digest_never_sends_an_empty_email(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ", {"DIGEST_TRIGGER_TOKEN": "automation-token"}, clear=True
        ), patch("tender_radar.server.in_digest_send_window", return_value=True), patch(
            "tender_radar.server.build_email_digest", return_value={
                "counts": {"new_notices": 0, "old_notices": 0, "new_news": 0, "old_news": 0}
            }
        ), patch("tender_radar.server.send_email_digest") as send_mock:
            db = Path(tmp) / "test.db"
            init_db(db)
            handler = object.__new__(Handler)
            handler.settings = Settings("", 48, db, "127.0.0.1", 0)
            handler.path = "/api/automation/digest"
            handler.headers = {
                "Authorization": "Bearer automation-token",
                "X-Digest-Scheduled": "true",
                "X-Resend-Api-Key": "re_test",
                "X-Digest-From-Email": "CONCOST <news@con-cost.co.kr>",
                "X-Digest-Recipients": "team@con-cost.co.kr",
            }
            responses = []
            handler._json = lambda payload, status=200: responses.append((payload, status))
            handler.do_POST()
            self.assertTrue(responses[0][0]["skipped"])
            self.assertIn("0건", responses[0][0]["reason"])
            send_mock.assert_not_called()

    def test_english_abbreviation_does_not_match_inside_unrelated_word(self):
        score, matched = score_notice(
            "테라헤르츠 이미지센서 ROIC의 DRC Waiver 적합성 검토 및 제조 전 설계검증 연구용역"
        )
        self.assertLess(score, MIN_NOTICE_SCORE)
        self.assertFalse(any("(VE)" in item for item in matched))

    def test_g2b_retries_one_transient_read_timeout(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self):
                return json.dumps({
                    "response": {"header": {"resultCode": "00"}, "body": {
                        "totalCount": 1,
                        "items": {"item": [{
                            "bidNtceNo": "retry-1", "bidNtceOrd": "00",
                            "bidNtceNm": "공사비 검증 용역", "ntceInsttNm": "서울특별시",
                        }]},
                    }}
                }).encode("utf-8")

        with patch("tender_radar.g2b.urlopen", side_effect=[TimeoutError(), Response()]) as mocked:
            rows = fetch_category("key", "용역", datetime(2026, 7, 19), datetime(2026, 7, 20))
        self.assertEqual(len(rows), 1)
        self.assertEqual(mocked.call_count, 2)

    def test_concost_specialties_score_high(self):
        cases = (
            "재개발 정비사업 공사비 검증 용역",
            "공사 계약금액 물가변동 ES 검토 용역",
            "설계변경 정산 및 클레임 검토 용역",
            "공동주택 정밀안전진단 용역",
            "실시설계 개산견적 및 BOQ 작성 용역",
            "해외 FED 내역서 및 구조 BIM 산출 용역",
        )
        for title in cases:
            with self.subTest(title=title):
                self.assertGreaterEqual(score_notice(title)[0], 65)

    def test_direct_construction_is_not_a_concost_opportunity(self):
        for title in (
            "아파트 균열보수 및 재도장 공사업체 선정 공고",
            "옥상 방수공사 시공업체 선정",
            "승강기 교체공사 사업자 선정",
            "청사 건축공사 입찰공고",
        ):
            with self.subTest(title=title):
                self.assertLess(score_notice(title)[0], 20)

    def test_cost_consulting_about_a_construction_still_scores_high(self):
        score, matched = score_notice("아파트 재도장공사 공사비 산정 및 원가검토 용역")
        self.assertGreaterEqual(score, 50)
        self.assertTrue(any("직접시공 감점" in item for item in matched))

    def test_safety_diagnosis_notices_are_limited_to_seoul(self):
        seoul_notice = {
            "title": "공동주택 정밀안전진단 용역",
            "institution": "서울특별시 강남구",
            "region": "강남구",
            "score": 70,
            "matched_keywords": ["전문업무:안전·구조진단(정밀안전진단)"],
        }
        non_seoul_notice = {
            **seoul_notice,
            "institution": "부산광역시",
            "region": "부산",
        }
        self.assertTrue(should_keep_notice(seoul_notice))
        self.assertFalse(should_keep_notice(non_seoul_notice))

    def test_collection_keeps_only_score_40_or_higher(self):
        high = {"source": "테스트", "title": "공사비 검증", "score": MIN_NOTICE_SCORE}
        seoul_safety = {
            "source": "테스트", "title": "정밀안전진단 용역", "institution": "서울특별시",
            "region": "서울", "score": 70,
        }
        non_seoul_safety = {
            "source": "테스트", "title": "정밀안전진단 용역", "institution": "부산광역시",
            "region": "부산", "score": 70,
        }
        low = {"source": "테스트", "title": "도장 시공", "score": MIN_NOTICE_SCORE - 1}
        with patch("tender_radar.collector.g2b.collect_recent", return_value=[high, seoul_safety, non_seoul_safety, low]), patch(
            "tender_radar.collector.lh.collect_recent", return_value=[]
        ), patch("tender_radar.collector.expressway.collect_recent", return_value=[]), patch(
            "tender_radar.collector.kapt.collect_recent", return_value=[]
        ), patch("tender_radar.collector.jiwoncok.collect_recent", return_value=[]
        ):
            notices, statuses = collect_all("key", 48)
        self.assertEqual(notices, [high, seoul_safety])
        self.assertEqual(statuses[0]["filtered"], 2)

    def test_collection_timeout_does_not_fail_fast_sources(self):
        def slow_source(*_):
            time.sleep(0.2)
            return [{"source": "나라장터", "title": "공사비 검증", "score": 70}]

        with patch("tender_radar.collector.g2b.collect_recent", side_effect=slow_source), patch(
            "tender_radar.collector.lh.collect_recent", return_value=[]
        ), patch("tender_radar.collector.expressway.collect_recent", return_value=[]), patch(
            "tender_radar.collector.kapt.collect_recent", return_value=[]
        ), patch("tender_radar.collector.jiwoncok.collect_recent", return_value=[]
        ):
            notices, statuses = collect_all("key", 48, source_timeout_seconds=0.05)
        self.assertEqual(notices, [])
        self.assertFalse(statuses[0]["ok"])
        self.assertIn("제한시간", statuses[0]["error"])
        self.assertTrue(all(status["ok"] for status in statuses[1:]))

    def test_daily_collection_includes_jiwoncok_sources(self):
        row = {
            "source": "지원COK", "source_key": "source-1", "category": "용역",
            "title": "공사비 검증 용역", "institution": "서울특별시",
            "score": 70, "matched_keywords": ["공사비 검증"],
        }
        with patch("tender_radar.collector.g2b.collect_recent", return_value=[]), patch(
            "tender_radar.collector.lh.collect_recent", return_value=[]
        ), patch("tender_radar.collector.expressway.collect_recent", return_value=[]), patch(
            "tender_radar.collector.kapt.collect_recent", return_value=[]
        ), patch("tender_radar.collector.jiwoncok.collect_recent", return_value=[row]):
            notices, statuses = collect_all("key", 48)
        self.assertEqual(notices, [row])
        self.assertEqual(statuses[-1]["source"], "지원COK")
        self.assertTrue(statuses[-1]["ok"])

    def test_server_collection_job_finishes_with_partial_timeout(self):
        def slow_source(*_):
            time.sleep(0.5)
            return [{"source": "LH", "title": "공사비 검증", "score": 70}]

        def fast_notice(*_):
            return [{
                "source": "테스트", "source_key": "fast", "category": "용역",
                "title": "공사비 검증", "institution": "테스트기관",
                "published_at": "2026-07-13", "deadline_at": "2026-07-15",
                "estimated_price": None, "region": "", "notice_type": "신규",
                "change_reason": "", "changed_at": "", "url": "https://example.com",
                "score": 70, "matched_keywords": ["공사비"], "raw": {},
            }]

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ", {"COLLECTION_JOB_TIMEOUT_SECONDS": "0.25"}
        ), patch("tender_radar.server.g2b.collect_recent", side_effect=fast_notice), patch(
            "tender_radar.server.lh.collect_recent", side_effect=slow_source
        ), patch("tender_radar.server.expressway.collect_recent", return_value=[]), patch(
            "tender_radar.server.kapt.collect_recent", return_value=[]
        ), patch("tender_radar.server.jiwoncok.collect_recent", return_value=[]), patch(
            "tender_radar.server.official_news.collect_official_news", return_value=[]
        ):
            db = Path(tmp) / "test.db"
            init_db(db)
            handler = object.__new__(Handler)
            handler.settings = Settings("", 48, db, "127.0.0.1", 0)
            job_id = "timeout-test"
            now = datetime.now().astimezone().isoformat(timespec="seconds")
            self.assertTrue(Handler.collection_lock.acquire(blocking=False))
            Handler.collection_lock_owner = job_id
            try:
                with Handler.collection_jobs_lock:
                    Handler.collection_jobs = {
                        job_id: {
                            "id": job_id, "status": "running", "ok": True, "partial": False,
                            "started_at": now, "updated_at": now, "completed_at": "",
                            "percent": 0, "message": "테스트", "sources": [], "source_total": 0,
                            "total": 0, "inserted": 0, "updated": 0, "unchanged": 0,
                            "news_inserted": 0, "news_updated": 0,
                        }
                    }
                handler._run_collection_job(job_id, "key", "", 48)
                job = Handler._get_collection_job(job_id)
                self.assertEqual(job["status"], "complete")
                self.assertEqual(job["percent"], 100)
                self.assertTrue(job["partial"])
                self.assertTrue(any(source.get("ok") for source in job["sources"]))
                self.assertTrue(any("제한시간" in source.get("error", "") for source in job["sources"]))
            finally:
                with Handler.collection_jobs_lock:
                    Handler.collection_jobs = {}
                if Handler.collection_lock.acquire(blocking=False):
                    Handler.collection_lock.release()
                else:
                    Handler.collection_lock_owner = None
                    Handler.collection_lock.release()

    def test_stale_collection_job_can_be_expired_and_unlocks(self):
        job_id = "stale-test"
        old = (datetime.now().astimezone() - timedelta(seconds=310)).isoformat(timespec="seconds")
        self.assertTrue(Handler.collection_lock.acquire(blocking=False))
        Handler.collection_lock_owner = job_id
        try:
            with Handler.collection_jobs_lock:
                Handler.collection_jobs = {
                    job_id: {
                        "id": job_id, "status": "running", "ok": True, "partial": False,
                        "started_at": old, "updated_at": old, "completed_at": "",
                        "percent": 43, "message": "테스트", "sources": [
                            {"source": "나라장터", "ok": True, "total": 1}
                        ], "source_total": 7,
                    }
                }
            job = Handler._get_collection_job(job_id)
            self.assertTrue(Handler._collection_job_is_stale(job))
            expired = Handler._expire_collection_job(job_id)
            self.assertEqual(expired["status"], "complete")
            self.assertEqual(expired["percent"], 100)
            self.assertTrue(expired["partial"])
            self.assertTrue(any("제한시간" in source.get("error", "") for source in expired["sources"]))
            self.assertTrue(Handler.collection_lock.acquire(blocking=False))
            Handler.collection_lock.release()
        finally:
            with Handler.collection_jobs_lock:
                Handler.collection_jobs = {}
            Handler.collection_lock_owner = None
            if Handler.collection_lock.acquire(blocking=False):
                Handler.collection_lock.release()
            else:
                Handler.collection_lock.release()

    def test_recipient_environment_seed_survives_empty_database(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ", {"DIGEST_RECIPIENTS": "one@con-cost.co.kr,two@con-cost.co.kr"}
        ):
            db = Path(tmp) / "test.db"
            init_db(db)
            self.assertEqual(
                {row["email"] for row in list_digest_recipients(db)},
                {"one@con-cost.co.kr", "two@con-cost.co.kr"},
            )

    def test_parse_cerik_industry_news(self):
        page = '''<div class="document-preview-slide-wrap"><div class="title">건설공사비 동향</div>
        <b>출판일</b><span>2026-07-08</span><a href="/report/briefing/3102">요약보기</a></div>'''
        rows = parse_cerik(page, "CERIK 동향브리핑", "https://www.cerik.re.kr/report/briefing")
        self.assertEqual(rows[0]["source_key"], "3102")
        self.assertEqual(rows[0]["published_at"], "2026-07-08")
        self.assertIn("공사비", rows[0]["title"])

    def test_parse_ricon_industry_news(self):
        page = '''<table><tr><td class="col_sbj"><a href="/board/view.php?no=6141&amp;cate=7">
        <span class="bo_ca">건설시장과 이슈</span><strong class="bo_sbj">2026년 건설수주동향</strong></a></td>
        <td class="col_date">2026-06-30</td></tr></table>'''
        rows = parse_ricon(page, "RICON 건설시장", "https://www.ricon.re.kr/")
        self.assertEqual(rows[0]["source_key"], "6141")
        self.assertEqual(rows[0]["published_at"], "2026-06-30")

    def test_parse_constimes_keeps_links_not_article_body(self):
        page = '''<a href="https://www.constimes.co.kr/news/articleView.html?idxno=312238">
        <h2>재건축 공사비 검증 제도 본격화</h2></a>
        <a href="https://www.constimes.co.kr/news/articleView.html?idxno=312237"><h2>임직원 채용 공고</h2></a>'''
        rows = parse_constimes(page)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_key"], "312238")
        self.assertEqual(rows[0]["summary"], "")

    def test_parse_jiwoncok_forwarded_mail(self):
        body = """
        [평택시 토진어연처리분구 어연 소규모 하수처리시설 증설공사 공법선정위원회 평가위원 모집](https://track.example/L0/https:%2F%2Fjiwonkok.com%2F11167%2F%3Fsource=member-email/1)

        🏢
        공고기관: 평택시

        📅
        접수기간: 2026-07-13 ~ 2026-07-15

        🏷️
        모집분야: 상하수도, 환경, 기계, 전기
        """
        rows = parse_jiwoncok_email(body, published_at="2026-07-13")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "지원COK")
        self.assertEqual(rows[0]["source_key"], "11167")
        self.assertEqual(rows[0]["category"], "평가위원 모집")
        self.assertEqual(rows[0]["published_at"], "2026-07-13")
        self.assertEqual(rows[0]["deadline_at"], "2026-07-15")
        self.assertGreaterEqual(rows[0]["score"], MIN_NOTICE_SCORE)

    def test_parse_jiwoncok_source_page(self):
        page = """
        <table><tr><td>2026-07-13</td><td>
        <a href="/notice/view.do?seq=777">토진어연처리분구 소규모 하수처리시설 증설공사 공법선정위원회 평가위원 모집</a>
        </td><td>접수기간 2026-07-13 ~ 2026-07-15</td></tr>
        <tr><td><a href="/notice/view.do?seq=778">일반 행사 안내</a></td></tr></table>
        """
        rows = parse_source_page(page, "https://www.example.go.kr/notice/list.do", "평택시")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "지원COK")
        self.assertEqual(rows[0]["institution"], "평택시")
        self.assertEqual(rows[0]["deadline_at"], "2026-07-15")
        self.assertTrue(rows[0]["url"].startswith("https://www.example.go.kr/notice/"))

    def test_parse_jiwoncok_source_page_includes_concost_service_keywords(self):
        page = """
        <table><tr><td>2026-07-20</td><td>
        <a href="/notice/view.do?seq=880">서울 공동주택 정밀안전진단 및 공사비 검증 용역</a>
        </td><td>접수기간 2026-07-20 ~ 2026-07-25</td></tr>
        <tr><td><a href="/notice/view.do?seq=881">부산 공동주택 정밀안전진단 용역</a></td></tr></table>
        """
        rows = parse_source_page(page, "https://www.example.go.kr/notice/list.do", "")
        kept = [row for row in rows if should_keep_notice(row)]
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["category"], "용역")
        self.assertGreaterEqual(kept[0]["score"], MIN_NOTICE_SCORE)

    def test_parse_jiwoncok_source_page_ignores_navigation_and_results(self):
        page = """
        <a href="/finance">공공기관 정산</a>
        <a href="/result?seq=1">계약심의위원회 위원 공개모집 결과 공고</a>
        <a href="/notice?seq=2">서울 청사 공사비 검증 용역 공고</a>
        """
        rows = parse_source_page(page, "https://www.example.go.kr/", "서울특별시")
        self.assertEqual([row["title"] for row in rows], ["서울 청사 공사비 검증 용역 공고"])

    def test_discover_jiwoncok_board_urls(self):
        page = """
        <a href="/intro">기관소개</a>
        <a href="/saeol/gosi/list.do">고시공고</a>
        <a href="https://outside.example.com/notice">외부공지</a>
        <a href="/board/notice/list.do">공지사항</a>
        """
        urls = discover_board_urls(page, "https://www.example.go.kr/")
        self.assertIn("https://www.example.go.kr/saeol/gosi/list.do", urls)
        self.assertIn("https://www.example.go.kr/board/notice/list.do", urls)
        self.assertTrue(all("outside.example.com" not in url for url in urls))

    def test_jiwoncok_defaults_to_small_core_source_set(self):
        with patch.dict("os.environ", {}, clear=True):
            rows = active_source_pages()
        self.assertLessEqual(len(rows), 8)
        self.assertIn("경기신용보증재단", {row["institution"] for row in rows})
        self.assertIn("서울교통공사", {row["institution"] for row in rows})

    def test_jiwoncok_extended_mode_can_restore_full_source_set(self):
        with patch.dict("os.environ", {"JIWONCOK_SOURCE_MODE": "extended"}, clear=True):
            rows = active_source_pages()
        self.assertGreater(len(rows), 40)

    def test_jiwoncok_source_failures_return_partial_status(self):
        good = {"institution": "서울교통공사", "url": "https://good.example"}
        bad = {"institution": "느린기관", "url": "https://bad.example"}

        def fake_source(source):
            if source["institution"] == "느린기관":
                raise RuntimeError("timeout")
            return [{
                "source": "지원COK", "source_key": "good-1", "category": "평가위원 모집",
                "title": "공법선정위원회 평가위원 모집", "institution": source["institution"],
                "published_at": datetime.now().astimezone().date().isoformat(), "deadline_at": "2026-07-21",
                "estimated_price": None, "region": "", "notice_type": "신규",
                "change_reason": "", "changed_at": "", "url": "https://good.example/1",
                "score": 70, "matched_keywords": ["평가위원"], "raw": {},
            }]

        with patch("tender_radar.jiwoncok.active_source_pages", return_value=[good, bad]), patch(
            "tender_radar.jiwoncok.collect_source_page", side_effect=fake_source
        ):
            rows, statuses = collect_recent_with_status()
        self.assertEqual(len(rows), 1)
        self.assertTrue(any(status["ok"] for status in statuses))
        self.assertTrue(any(not status["ok"] for status in statuses))

    def test_jiwoncok_core_sources_start_in_parallel(self):
        sources = [
            {"institution": f"기관-{index}", "url": f"https://example{index}.go.kr"}
            for index in range(8)
        ]
        barrier = threading.Barrier(8, timeout=2)

        def fake_source(_source):
            barrier.wait()
            return []

        with patch.dict("os.environ", {}, clear=True), patch(
            "tender_radar.jiwoncok.active_source_pages", return_value=sources
        ), patch("tender_radar.jiwoncok.collect_source_page", side_effect=fake_source):
            rows, statuses = collect_recent_with_status()
        self.assertEqual(rows, [])
        self.assertEqual(len(statuses), 8)
        self.assertTrue(all(status["ok"] for status in statuses))

    def test_parse_jiwoncok_source_page_ignores_committee_menu_labels(self):
        page = """
        <nav><a href="/committee">경기도 건축위원회</a></nav>
        <a href="/notice/100">건축위원회 위원 공개모집 공고</a><span>2026-07-21</span>
        """
        rows = parse_source_page(page, "https://www.gg.go.kr/", "경기도")
        self.assertEqual(len(rows), 1)
        self.assertIn("공개모집", rows[0]["title"])

    def test_parse_jiwoncok_source_page_prefers_date_inside_list_link(self):
        page = """
        <a href="/notice/200">정산 용역 제안서 평가위원 후보자 모집 2026-07-07</a>
        <span>다음 게시물 2026-07-21</span>
        """
        rows = parse_source_page(page, "https://example.go.kr/", "서울특별시")
        self.assertEqual(rows[0]["published_at"], "2026-07-07")
        self.assertNotIn("2026-07-07", rows[0]["title"])

    def test_jiwoncok_fetch_keeps_received_html_when_server_does_not_close(self):
        from tender_radar.jiwoncok import _fetch

        class Headers:
            @staticmethod
            def get_content_charset():
                return "utf-8"

        class Response:
            headers = Headers()

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self, _size):
                if not hasattr(self, "sent"):
                    self.sent = True
                    return "<a href='/notice'>공사비 검증 용역</a>".encode()
                raise TimeoutError("keep-alive")

        with patch("tender_radar.jiwoncok.urlopen", return_value=Response()):
            page = _fetch("https://example.go.kr")
        self.assertIn("공사비 검증 용역", page)

    def test_normalize_g2b_item(self):
        notice = normalize_item({
            "bidNtceNo": "R26TEST", "bidNtceOrd": "000",
            "bidNtceNm": "건축공사 물량산출 용역", "ntceInsttNm": "테스트기관",
            "presmptPrce": "123000000",
        }, "용역")
        self.assertEqual(notice["source_key"], "R26TEST-000")
        self.assertEqual(notice["estimated_price"], 123000000)
        self.assertEqual(notice["notice_type"], "신규")

    def test_normalize_changed_notice(self):
        notice = normalize_item({
            "bidNtceNo": "R26CHANGE", "bidNtceOrd": "001",
            "bidNtceNm": "변경 공고", "ntceKindNm": "변경공고",
            "chgNtceRsn": "마감일 변경", "chgDt": "2026-07-03 10:00:00",
        }, "공사")
        self.assertEqual(notice["notice_type"], "개정")
        self.assertEqual(notice["change_reason"], "마감일 변경")

    def test_normalize_lh_notice(self):
        notice = normalize_lh_item({"bidNum": "2600001", "bidnmKor": "공사비 검증 용역", "cstrtnJobGbNm": "용역", "bidKind": "정정공고"})
        self.assertEqual(notice["source"], "LH")
        self.assertEqual(notice["notice_type"], "개정")

    def test_parse_and_normalize_kapt_notice(self):
        html = """<table><tbody><tr class='notice-row'>
        <td onclick=\"goView('20260707125216962')\">1</td><td>K-APT</td><td>최저 낙찰</td>
        <td>[울산] 균열보수 및 재도장공사업체 선정 공고</td><td>2026-07-20 17:00:00</td>
        <td>신규공고</td><td>달동현대3차</td><td>2026-07-07 12:55:22</td>
        </tr></tbody></table>"""
        rows = parse_kapt_list(html)
        self.assertEqual(len(rows), 1)
        notice = normalize_kapt_item(rows[0])
        self.assertEqual(notice["source"], "공동주택관리정보시스템")
        self.assertEqual(notice["region"], "울산")
        self.assertEqual(notice["category"], "공사")
        self.assertLess(notice["score"], 20)
        self.assertIn("bidNum=20260707125216962", notice["url"])
        self.assertEqual(notice["notice_type"], "신규")

    def test_normalize_ex_notice(self):
        notice = normalize_ex_item({"noti_no": "2026001", "bid_rev": 1, "noti_nm": "도로 설계용역", "noti_date": "20260703"}, "용역")
        self.assertEqual(notice["source"], "도로공사")
        self.assertEqual(notice["category"], "용역")

    def test_upsert_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.db"
            notice = normalize_item({"bidNtceNo": "X", "bidNtceNm": "토목 적산 용역"}, "용역")
            self.assertEqual(upsert_notice(db, notice), "inserted")
            self.assertEqual(upsert_notice(db, notice), "unchanged")
            self.assertEqual(len(list_notices(db)), 1)

    def test_digest_recipient_address_book(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.db"
            saved = save_digest_recipient(db, "TEAM@con-cost.co.kr", "견적팀")
            self.assertEqual(saved["email"], "team@con-cost.co.kr")
            self.assertEqual(len(list_digest_recipients(db)), 1)
            self.assertTrue(delete_digest_recipient(db, saved["id"]))
            self.assertEqual(list_digest_recipients(db), [])

    def test_old_digest_schedule_migrates_to_ten(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.db"
            set_setting(db, "digest_schedule_time", "08:30")
            init_db(db)
            self.assertEqual(get_setting(db, "digest_schedule_time"), "10:00")

    def test_weekday_automation_windows(self):
        friday_collect = datetime(2026, 7, 10, 9, 0)
        friday_late_collect = datetime(2026, 7, 10, 9, 10)
        friday_digest = datetime(2026, 7, 10, 10, 0)
        friday_late_window_digest = datetime(2026, 7, 10, 10, 10)
        friday_late_digest = datetime(2026, 7, 10, 12, 57)
        saturday_collect = datetime(2026, 7, 11, 9, 0)
        self.assertTrue(in_collect_window(friday_collect))
        self.assertTrue(in_collect_window(friday_late_collect))
        self.assertFalse(in_collect_window(friday_digest))
        self.assertTrue(in_digest_window(friday_digest))
        self.assertTrue(in_digest_send_window(friday_digest))
        self.assertTrue(in_digest_send_window(friday_late_window_digest))
        self.assertFalse(in_digest_send_window(friday_late_digest))
        self.assertFalse(in_collect_window(saturday_collect))
        self.assertFalse(in_digest_window(saturday_collect.replace(hour=10)))

    def test_branded_digest_separates_new_and_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.db"
            notice = normalize_item({
                "bidNtceNo": "MAIL-1", "bidNtceNm": "공사비 검증 용역",
                "ntceInsttNm": "테스트기관", "bidClseDt": "20260710",
            }, "용역")
            upsert_notice(db, notice)
            first = build_email_digest(db)
            self.assertEqual(first["counts"]["new_notices"], 1)
            self.assertIn("적합도", first["html"])
            self.assertIn("QS_ConCost 바로가기", first["html"])
            with connect(db) as conn:
                delivery_id = conn.execute(
                    "INSERT INTO digest_deliveries(started_at,status,subject) VALUES('now','sent','test')"
                ).lastrowid
                conn.execute(
                    "INSERT INTO digest_delivery_items(delivery_id,item_kind,source,source_key,is_new) VALUES(?,?,?,?,1)",
                    (delivery_id, "notice", notice["source"], notice["source_key"]),
                )
            second = build_email_digest(db)
            self.assertEqual(second["counts"]["new_notices"], 0)
            self.assertEqual(second["counts"]["old_notices"], 1)
            self.assertIn("기존 알림 프로젝트", second["html"])

    def test_digest_includes_only_today_news(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.db"
            today = datetime.now(SEOUL).date()
            yesterday = today - timedelta(days=1)
            upsert_news(db, {
                "source": "테스트뉴스", "source_key": "today-news",
                "category": "건설 주요뉴스", "title": "오늘 공사비 검증 뉴스",
                "summary": "", "published_at": today.isoformat(), "url": "https://example.com/today",
                "score": 80, "matched_keywords": ["공사비"],
            })
            upsert_news(db, {
                "source": "테스트뉴스", "source_key": "old-news",
                "category": "건설 주요뉴스", "title": "어제 공사비 검증 뉴스",
                "summary": "", "published_at": yesterday.isoformat(), "url": "https://example.com/old",
                "score": 95, "matched_keywords": ["공사비"],
            })
            digest = build_email_digest(db)
            self.assertEqual(digest["counts"]["new_news"], 1)
            self.assertIn("오늘 공사비 검증 뉴스", digest["html"])
            self.assertNotIn("어제 공사비 검증 뉴스", digest["html"])

    def test_resend_request_has_required_user_agent(self):
        request = build_resend_request("test-key", b"{}")
        self.assertIn("QS-CONCOST", request.get_header("User-agent"))
        self.assertEqual(request.get_header("Accept"), "application/json")

    def test_send_test_email_uses_first_active_recipient(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self):
                return b'{"id":"mail-test-1"}'

        with patch("tender_radar.email_digest.list_digest_recipients", return_value=[
            {"email": "team@con-cost.co.kr", "is_active": 1}
        ]), patch("tender_radar.email_digest.get_secret", return_value="re_test"), patch(
            "tender_radar.email_digest.get_setting", return_value="CONCOST <news@con-cost.co.kr>"
        ), patch("tender_radar.email_digest.urlopen", return_value=Response()):
            result = send_test_email(Path("unused.db"))
        self.assertEqual(result["recipient"], "team@con-cost.co.kr")
        self.assertEqual(result["provider_id"], "mail-test-1")


if __name__ == "__main__":
    unittest.main()
