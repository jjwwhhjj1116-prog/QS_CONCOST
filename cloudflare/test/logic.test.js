import test from 'node:test';
import assert from 'node:assert/strict';
import { filterRows, jobState, scheduleAction, todayDigest, validateResult } from '../src/logic.js';

test('KST weekday windows reject weekend and late mail', () => {
  const action = s => scheduleAction(Date.parse(s));
  assert.equal(action('2026-09-08T09:00:00+09:00'), 'collect');
  assert.equal(action('2026-09-08T09:54:00+09:00'), 'collect');
  assert.equal(action('2026-09-08T09:55:00+09:00'), null);
  assert.equal(action('2026-09-08T10:00:00+09:00'), 'digest');
  assert.equal(action('2026-09-08T10:01:00+09:00'), null);
  assert.equal(action('2026-09-12T10:00:00+09:00'), null);
});
test('only dated today items, no old/undated/cancelled digest entries', () => {
  const rows = [{ published_at: '2026-09-08', title: 'today' },
    { published_at: '2026-09-07' }, { published_at: '' },
    { published_at: '2026-09-08', notice_type: '취소' }];
  assert.deepEqual(todayDigest(rows, Date.parse('2026-09-08T10:00:00+09:00')), [rows[0]]);
});
test('old in-memory-style running state expires instead of permanent progress', () => {
  assert.equal(jobState({ state: 'running', deadline: 10 }, 11), 'expired');
  assert.equal(jobState({ state: 'succeeded', deadline: 10 }, 11), 'succeeded');
});
test('filter preserves Nuri and relevance selection', () => {
  const row = { title: '공사비 검증', source: '누리장터', score: 90 };
  assert.deepEqual(filterRows([row, { ...row, score: 20 }], new URLSearchParams('min_score=40&source=누리장터')), [row]);
});
test('failure differs from verified zero, invalid response cannot be saved', () => {
  validateResult({ source_id: 'g2b', ok: false, rows: [], error: 'timeout' }, 'g2b');
  validateResult({ source_id: 'g2b', ok: true, kind: 'notice', rows: [], kept: 0, candidates: 0 }, 'g2b');
  assert.throws(() => validateResult({ source_id: 'g2b', ok: true, rows: [] }, 'g2b'));
  assert.throws(() => validateResult({ source_id: 'g2b', ok: true, kind: 'notice',
    rows: [{ source: 'a', source_key: 'a', title: 'x', score: 90, url: 'javascript:alert(1)' }], kept: 1, candidates: 1 }, 'g2b'));
});
