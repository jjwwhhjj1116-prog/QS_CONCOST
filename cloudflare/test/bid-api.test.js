import test from 'node:test';
import assert from 'node:assert/strict';
import { collectPage, extract, initialJobs, normalize, PAGE_SIZE } from '../src/bid-api.js';
import { scoreNotice, shouldKeep } from '../src/scoring.js';

test('seven-day collection creates 35 distinct day/category jobs', () => {
  const jobs = initialJobs(Date.parse('2026-09-08T10:00:00+09:00'), 168);
  assert.equal(jobs.length, 35);
  assert.equal(new Set(jobs.map(x => x.source_id + x.start_date)).size, 35);
  assert.equal(jobs[0].start_date, '202609011000');
  assert.equal(jobs.at(-1).end_date, '202609081000');
});
test('API error is not a valid no-data result', () => {
  assert.throws(() => extract({}), /upstream_api_error/);
  assert.throws(() => extract({ response: { header: { resultCode: '00' } } }), /invalid_api_body/);
  assert.deepEqual(extract({ response: { header: { resultCode: '03', resultMsg: 'NODATA_ERROR' } } }), { items: [], total: 0 });
});
test('Nuri private owner, link and score are retained', () => {
  const row = normalize({ bidNtceNo: 'R2026TEST', bidNtceNm: '정비사업 공사비 검증 용역', prvtBidNtceInsttNm: '재개발조합' }, 'nuri-service');
  assert.equal(row.institution, '재개발조합');
  assert.equal(row.source, '누리장터');
  assert.ok(row.url.includes('bidPbancNo=R2026TEST'));
  assert.ok(row.score >= 40);
  assert.ok(!('raw' in row));
});
test('one response page is small and continuation explicit', async () => {
  const item = { bidNtceNo: 'a', bidNtceNm: '외벽 재도장 공사' };
  const fetcher = async url => {
    assert.equal(url.searchParams.get('numOfRows'), String(PAGE_SIZE));
    return Response.json({ response: { header: { resultCode: '00' }, body: {
      totalCount: 21, items: Array.from({ length: 20 }, (_, n) => ({ ...item, bidNtceNo: String(n) })) } } });
  };
  const result = await collectPage({ source_id: 'g2b-service', start_date: '202609080000', end_date: '202609081000', next_page: 1 }, 'test', fetcher);
  assert.equal(result.candidates, 20);
  assert.equal(result.filtered, 20);
  assert.equal(result.more, true);
});
test('missing page is not silently accepted', async () => {
  await assert.rejects(collectPage({ source_id: 'g2b-service', next_page: 2 }, 'test', async () =>
    Response.json({ response: { header: { resultCode: '00' }, body: { totalCount: 50, items: [] } } })), /upstream_missing_page/);
});
test('service relevance and Seoul safety filter stay separate', () => {
  for (const title of ['부산 정밀안전진단 용역', '서울 정밀안전진단 용역']) {
    const row = { title, ...scoreNotice(title) };
    assert.ok(row.score >= 40);
    assert.equal(shouldKeep(row), title.startsWith('서울'));
  }
  assert.equal(scoreNotice('향적산 보수공사').score, 0);
  assert.ok(scoreNotice('추정분담금 산정검증 용역').score >= 40);
});
