import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tender_radar.db import (
    connect, delete_digest_recipient, list_digest_recipients, save_digest_recipient,
    get_setting, init_db, list_notices, set_setting, upsert_notice,
)
from tender_radar.email_digest import build_email_digest, build_resend_request, send_test_email
from tender_radar.g2b import normalize_item
from tender_radar.expressway import normalize_item as normalize_ex_item
from tender_radar.lh import normalize_item as normalize_lh_item
from tender_radar.scoring import score_notice


class MVPTests(unittest.TestCase):
    def test_qs_notice_scores_high(self):
        score, matched = score_notice("청사 신축공사 공사비 검증 및 VE 용역", "서울시")
        self.assertGreaterEqual(score, 60)
        self.assertTrue(any("공사비" in item for item in matched))

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
