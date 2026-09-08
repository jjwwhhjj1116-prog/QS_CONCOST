import json
import tempfile
import unittest
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from tender_radar.db import init_db, stats, get_setting
from tender_radar.public_snapshot import merge_snapshot, publish_snapshot
from tender_radar.scoring import score_notice
from tender_radar.server import restore_public_bid_snapshot


class RecoveryTests(unittest.TestCase):
    def row(self, key="1"):
        return {"source": "나라장터", "source_key": key, "category": "용역",
                "title": "부산 재개발 공사비 검증 용역", "region": "부산",
                "published_at": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(),
                "raw": {"secret": "must-not-publish"}, "workflow_status": "review"}

    def test_public_snapshot_whitelists_and_merges(self):
        old = merge_snapshot({}, [self.row()], ["나라장터"])
        new = merge_snapshot(old, [self.row("2")], ["지원COK"])
        self.assertEqual(len(new["rows"]), 2)
        self.assertEqual(set(new["source_dates"]), {"나라장터", "지원COK"})
        self.assertNotIn("raw", new["rows"][0])
        self.assertNotIn("workflow_status", new["rows"][0])
        self.assertNotIn("secret", json.dumps(new))

    def test_empty_or_failed_source_does_not_erase_previous_rows(self):
        previous = merge_snapshot({}, [self.row()], ["나라장터"])
        self.assertEqual(len(merge_snapshot(previous, [], ["지원COK"])["rows"]), 1)

    def test_restart_restores_rows_and_verified_source_date(self):
        snapshot = merge_snapshot({}, [self.row()], ["나라장터"])
        for _ in range(2):
            with tempfile.TemporaryDirectory() as tmp:
                db = Path(tmp) / "new-process.db"
                init_db(db)
                with patch("tender_radar.server.urlopen", return_value=BytesIO(json.dumps(snapshot).encode())):
                    restore_public_bid_snapshot(db)
                self.assertEqual(stats(db)["total"], 1)
                self.assertEqual(get_setting(db, "last_public_bid_collect"), snapshot["source_dates"]["나라장터"])

    def test_old_source_date_cannot_become_today_after_restart(self):
        now = datetime.now(ZoneInfo("Asia/Seoul"))
        previous = merge_snapshot({}, [self.row()], ["나라장터"], now - timedelta(days=1))
        self.assertEqual(merge_snapshot(previous, [], [], now)["source_dates"], {})

    def test_requires_backup_token_on_github(self):
        with patch.dict("os.environ", {"GITHUB_ACTIONS": "true"}, clear=True):
            with self.assertRaises(RuntimeError):
                publish_snapshot([], [])

    def test_place_names_and_closed_evaluation_are_not_opportunities(self):
        for title in ("원적산 무장애나눔길 조성사업", "향적산 치유의 숲 보완공사",
                      "거제 수정산성 종합정비계획 수립 용역",
                      "공공주택지구 용역 제안서 평가위원회 개최 결과",
                      "공공주택지구 용역 제안서 평가위원회 일정 변경 공지"):
            self.assertLess(score_notice(title)[0], 40, title)
        for title in ("건축적산 용역", "공사비정산 용역", "재개발 공사비 검증 용역"):
            self.assertGreaterEqual(score_notice(title)[0], 40, title)
