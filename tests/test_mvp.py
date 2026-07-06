import tempfile
import unittest
from pathlib import Path

from tender_radar.db import list_notices, upsert_notice
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


if __name__ == "__main__":
    unittest.main()
