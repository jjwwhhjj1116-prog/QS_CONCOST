import json
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tender_radar.isolated_collector import collect_source, run_command, run_isolated, sources
from tender_radar.jiwoncok import parse_source_page


class CloudflareCollectionTest(unittest.TestCase):
    def test_jiwon_missing_date_is_not_today_or_deadline(self):
        rows = parse_source_page('<a href="/view?id=1">재건축 공사비 검증 용역 공고</a>', 'https://example.org', '조합')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['published_at'], '')
        self.assertEqual(rows[0]['deadline_at'], '')

    def test_jiwon_single_publication_date_is_not_a_deadline(self):
        rows = parse_source_page('<a href="/view?id=1">재건축 공사비 검증 용역 공고 2026-09-08</a>', 'https://example.org', '조합')
        self.assertEqual(rows[0]['published_at'], '2026-09-08')
        self.assertEqual(rows[0]['deadline_at'], '')

    def test_real_hanging_process_is_killed_and_next_job_runs(self):
        started = time.monotonic()
        result = run_command([sys.executable, '-c', 'import time; time.sleep(60)'], 0.25)
        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], 'source_deadline_exceeded')
        self.assertLess(time.monotonic() - started, 3)
        result = run_command([sys.executable, '-c', 'print(\'{"ok":true,"rows":[]}\')'], 3)
        self.assertTrue(result['ok'])

    def test_invalid_response_and_crash_are_not_zero_success(self):
        for code in ['print("not json")', 'print("[]")', 'raise RuntimeError("serviceKey=secret")']:
            result = run_command([sys.executable, '-c', code], 3)
            self.assertFalse(result['ok'])
            self.assertNotIn('secret', json.dumps(result))

    def test_recompute_score_filter_and_remove_raw_secrets(self):
        rows = [
            {'source': '누리장터', 'source_key': 'a', 'title': '재건축 공사비 검증 용역',
             'score': 0, 'raw_json': {'serviceKey': 'secret'}, 'url': 'https://example.org/bid/a'},
            {'source': '나라장터', 'source_key': 'b', 'title': '외벽 재도장 공사', 'score': 100},
            {'source': '나라장터', 'source_key': 'c', 'title': '부산 정밀안전진단 용역', 'score': 100},
        ]
        with patch('tender_radar.isolated_collector.sources', return_value={
            'fixture': ('notice', 'test', lambda: rows)}):
            result = collect_source('fixture')
        self.assertEqual(result['candidates'], 3)
        self.assertEqual(result['kept'], 1)
        self.assertEqual(result['filtered'], 2)
        self.assertGreaterEqual(result['rows'][0]['score'], 40)
        self.assertNotIn('secret', json.dumps(result))

    def test_empty_response_is_explicit_success(self):
        with patch('tender_radar.isolated_collector.sources', return_value={
            'fixture': ('notice', 'test', lambda: [])}):
            result = collect_source('fixture')
        self.assertTrue(result['ok'])
        self.assertEqual((result['candidates'], result['kept']), (0, 0))

    def test_jiwon_agencies_are_independent_tasks(self):
        manifest = sources()
        self.assertGreater(len([k for k in manifest if k.startswith('jiwon-')]), 1)
        self.assertNotIn('jiwoncok', manifest)
        with self.assertRaises(ValueError):
            run_isolated('https://user-supplied-host.invalid')

    def test_trial_schema_persists_items_and_jobs_after_reopen(self):
        schema = Path('cloudflare/migrations/0001_trial.sql').read_text(encoding='utf-8')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'trial.db'
            with sqlite3.connect(path) as conn:
                conn.executescript(schema)
                conn.execute("INSERT INTO items VALUES ('notice','test','1','','2026-09-08','{}','2026-09-08')")
                conn.execute("INSERT INTO collection_jobs (id,run_id,source_id,label,created_at,deadline,updated_at) VALUES ('r:a','r','a','test',1,2,1)")
            conn.close()
            with sqlite3.connect(path) as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM items').fetchone()[0], 1)
                self.assertEqual(conn.execute('SELECT state FROM collection_jobs').fetchone()[0], 'queued')
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute("INSERT INTO items VALUES ('notice','test','1','','2026-09-08','{}','2026-09-08')")
            conn.close()


if __name__ == '__main__':
    unittest.main()
