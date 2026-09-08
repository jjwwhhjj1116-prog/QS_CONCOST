"""Parity with the existing policy, without duplicating expected rule values."""
import json
import shutil
import sqlite3
import subprocess
import unittest
from pathlib import Path
from tender_radar import scoring

ROOT = Path(__file__).resolve().parent.parent


class FreeWorkerTest(unittest.TestCase):
    def test_exported_rules_equal_python_source(self):
        rules = json.loads((ROOT / 'cloudflare/src/scoring-rules.json').read_text(encoding='utf-8'))
        for key, value in rules.items():
            expected = getattr(scoring, key)
            self.assertEqual(value, list(expected) if isinstance(expected, tuple) else expected, key)

    def test_js_matches_python_scores_and_reasons(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is required for cross-runtime parity')
        titles = [word for group in scoring.SERVICE_GROUPS.values() for word in group]
        titles += ['부산 공사비 검증 용역', '서울 정밀안전진단 용역', '향적산 보수공사',
                   '공사비정산 용역', '외벽 재도장 공사', 'waiver', '평가위원회 개최 결과',
                   '개인정보 영향평가 용역', '용역업체 선정을 위한 입찰공고(추정분담금)']
        code = "import {scoreNotice} from './cloudflare/src/scoring.js'; let text=''; for await (const chunk of process.stdin) text+=chunk; console.log(JSON.stringify(JSON.parse(text).map(x=>scoreNotice(x))));"
        process = subprocess.run([node, '--input-type=module', '-e', code], cwd=ROOT,
                                 input=json.dumps(titles), capture_output=True, encoding='utf-8', check=True)
        results = json.loads(process.stdout)
        for title, result in zip(titles, results):
            expected, reasons = scoring.score_notice(title)
            self.assertEqual(result, {'score': expected, 'matched_keywords': reasons}, title)

    def test_day_slices_and_budget_constraints(self):
        conn = sqlite3.connect(':memory:')
        for path in sorted((ROOT / 'cloudflare/migrations').glob('*.sql')):
            conn.executescript(path.read_text(encoding='utf-8'))
        for day in ('day1', 'day2'):
            conn.execute("INSERT INTO collection_jobs (id,run_id,source_id,label,created_at,deadline,updated_at) VALUES (?,'r','g2b-service','test',1,2,1)", (day,))
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM collection_jobs').fetchone()[0], 2)
        conn.execute("INSERT INTO trial_budget VALUES ('day',1000)")
        result = conn.execute("INSERT INTO trial_budget VALUES ('day',1) ON CONFLICT(day) DO UPDATE SET pages=pages+1 WHERE pages<1000")
        self.assertEqual(result.rowcount, 0)
        conn.close()
