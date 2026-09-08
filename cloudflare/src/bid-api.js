import { scoreNotice, shouldKeep } from './scoring.js';

export const PAGE_SIZE = 20;
export const SOURCES = {
  'g2b-service': ['나라장터', '용역', 'ad/BidPublicInfoService/getBidPblancListInfoServc'],
  'g2b-construction': ['나라장터', '공사', 'ad/BidPublicInfoService/getBidPblancListInfoCnstwk'],
  'nuri-service': ['누리장터', '용역', 'ao/PrvtBidNtceService/getPrvtBidPblancListInfoServc'],
  'nuri-construction': ['누리장터', '공사', 'ao/PrvtBidNtceService/getPrvtBidPblancListInfoCnstwk'],
  'nuri-other': ['누리장터', '기타', 'ao/PrvtBidNtceService/getPrvtBidPblancListInfoEtc'],
};
const pick = (row, ...keys) => keys.map(k => row[k]).find(v => v !== undefined && v !== null && v !== '') ?? '';
function money(value) {
  if (value === '') return null;
  const n = Number(String(value).replaceAll(',', ''));
  return Number.isFinite(n) ? Math.trunc(n) : null;
}
export function normalize(row, sourceId) {
  const [source, category] = SOURCES[sourceId];
  const number = String(pick(row, 'bidNtceNo', 'bidPbancNo'));
  if (!number) throw new Error('missing_notice_id');
  const order = String(pick(row, 'bidNtceOrd', 'bidPbancOrd') || '00');
  const title = String(pick(row, 'bidNtceNm', 'bidPbancNm', 'ntceNm'));
  if (!title) throw new Error('missing_notice_title');
  const institution = String(pick(row, 'ntceInsttNm', 'dminsttNm', 'orderInsttNm', 'prvtBidNtceInsttNm', 'bizNm'));
  const region = String(pick(row, 'prtcptPsblRgnNm', 'jntcontrctDutyRgnNm', 'cnstrtsiteRgnNm', 'prtcptPsblRgnNm1'));
  const notice_type = { '변경공고': '개정', '재공고': '재공고', '취소공고': '취소' }[row.ntceKindNm] || '신규';
  const link = String(pick(row, 'bidNtceDtlUrl', 'bidNtceUrl', 'bidPbancDtlUrl'));
  const fallback = 'https://www.g2b.go.kr/link/PNPE027_01/single/?' + new URLSearchParams({ bidPbancNo: number, bidPbancOrd: order });
  return { source, source_key: `${number}-${order}`, category, title, institution, region, notice_type,
    id: `${sourceId}-${number}-${order}`, workflow_status: 'new',
    published_at: String(pick(row, 'bidNtceDt', 'bidNtceDate', 'rgstDt')),
    deadline_at: String(pick(row, 'bidClseDt', 'bidClseDate', 'opengDt')),
    estimated_price: money(pick(row, 'presmptPrce', 'asignBdgtAmt', 'bsisAmount')),
    change_reason: String(row.chgNtceRsn || ''),
    changed_at: notice_type === '신규' ? '' : String(pick(row, 'chgDt', 'rgstDt')),
    url: /^https?:\/\//i.test(link) ? link : fallback,
    ...scoreNotice(title, institution, region, source === '누리장터' ? '누리장터 민간입찰' : ''),
  };
}

export function extract(payload) {
  const response = payload?.response;
  const code = String(response?.header?.resultCode ?? 'missing');
  if (['03','3'].includes(code) && response.header.resultMsg === 'NODATA_ERROR') return { items: [], total: 0 };
  if (!['00','0'].includes(code)) throw new Error('upstream_api_error');
  const body = response.body;
  if (!body || !Object.hasOwn(body, 'totalCount')) throw new Error('invalid_api_body');
  let items = body.items?.item ?? body.items ?? [];
  if (items === '') items = [];
  if (!Array.isArray(items) && typeof items === 'object') items = [items];
  const total = Number(body.totalCount);
  if (!Array.isArray(items) || !Number.isInteger(total) || total < 0) throw new Error('invalid_api_items');
  return { items, total };
}

export function initialJobs(now, lookback) {
  const jobs = [];
  const fmt = ms => new Date(ms + 9 * 3600000).toISOString().replace(/[-:T]/g, '').slice(0, 12);
  for (let start = now - lookback * 3600000; start < now; start += 86400000) {
    const end = Math.min(start + 86400000, now);
    for (const [source_id, [source, category]] of Object.entries(SOURCES))
      jobs.push({ source_id, label: `${source} ${category}`, start_date: fmt(start), end_date: fmt(end) });
  }
  return jobs;
}

export async function collectPage(job, credential, fetcher = fetch) {
  if (!credential) throw new Error('missing_api_key');
  if (!SOURCES[job.source_id]) throw new Error('invalid_source');
  const url = new URL(`https://apis.data.go.kr/1230000/${SOURCES[job.source_id][2]}`);
  url.search = new URLSearchParams({ serviceKey: credential, type: 'json', inqryDiv: '1',
    inqryBgnDt: job.start_date, inqryEndDt: job.end_date, numOfRows: PAGE_SIZE, pageNo: job.next_page });
  let response;
  try { response = await fetcher(url, { signal: AbortSignal.timeout(15000) }); }
  catch { throw new Error('upstream_timeout_or_network'); }
  if (!response.ok) throw new Error(`upstream_http_${response.status}`);
  let payload;
  try { payload = await response.json(); } catch { throw new Error('upstream_non_json'); }
  const { items, total } = extract(payload);
  if (items.length > PAGE_SIZE) throw new Error('upstream_ignored_page_size');
  const offset = (job.next_page - 1) * PAGE_SIZE;
  if (!items.length && total > offset) throw new Error('upstream_missing_page');
  const rows = items.map(item => normalize(item, job.source_id)).filter(shouldKeep);
  return { rows, total, candidates: items.length, filtered: items.length - rows.length,
    more: offset + items.length < total };
}
