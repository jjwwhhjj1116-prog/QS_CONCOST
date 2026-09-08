import { filterRows, jobState, kstParts, scheduleAction, todayDigest } from './logic.js';
import { initialJobs, collectPage } from './bid-api.js';

const json = (data, status = 200) => Response.json(data, { status, headers: { 'Cache-Control': 'no-store' } });
const kindRoutes = { '/api/notices': 'notice', '/api/news': 'news',
  '/api/pipeline': 'pipeline', '/api/cost-records': 'cost' };

async function authorized(request, env) {
  if (!env.TRIAL_ADMIN_TOKEN) return false;
  // Constant-time digest comparison; trial token never goes into a URL or HTML.
  const hash = value => crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
  const [a, b] = await Promise.all([hash(request.headers.get('Authorization') || ''), hash(`Bearer ${env.TRIAL_ADMIN_TOKEN}`)]);
  return crypto.subtle.timingSafeEqual(a, b);
}

async function rowsFor(env, kind) {
  const { results } = await env.DB.prepare(
    'SELECT payload FROM items WHERE kind=? ORDER BY published_at DESC LIMIT 2000').bind(kind).all();
  return results.map(x => JSON.parse(x.payload));
}

async function startCollection(env, lookback = 168, scheduled = false) {
  const now = Date.now(), k = kstParts(now);
  const runId = scheduled ? `${k.date}-${k.hour}-${Math.floor(k.minute / 5)}` : crypto.randomUUID();
  const deadline = scheduled ? Date.parse(`${k.date}T09:55:00+09:00`) : now + 300000;
  if (!env.DATA_GO_KR_SERVICE_KEY) throw new Error('missing_api_key');
  const used = await env.DB.prepare('SELECT COUNT(DISTINCT run_id) AS total FROM collection_jobs WHERE created_at>=?')
    .bind(Date.parse(`${k.date}T00:00:00+09:00`)).first();
  if (used.total >= 2) throw new Error('trial_daily_run_limit');
  const manifest = initialJobs(now, lookback);
  const tasks = [];
  for (const source of manifest) {
    const id = `${runId}:${source.source_id}:${source.start_date}`;
    tasks.push({ id, ...source });
  }
  for (let i = 0; i < tasks.length; i += 20) {
    await env.DB.batch(tasks.slice(i, i + 20).map(source => env.DB.prepare(`INSERT OR IGNORE INTO collection_jobs
      (id,run_id,source_id,label,created_at,deadline,updated_at,start_date,end_date,candidates,kept,filtered)
      VALUES (?,?,?,?,?,?,?,?,?,0,0,0)`).bind(source.id, runId, source.source_id, source.label,
        now, deadline, now, source.start_date, source.end_date)));
  }
  try {
    await env.COLLECTION_QUEUE.sendBatch(tasks.map(task => ({ body: { id: task.id, page: 1 } })));
  } catch {
    await env.DB.prepare("UPDATE collection_jobs SET state='enqueue_failed',error='queue_unavailable' WHERE run_id=?")
      .bind(runId).run();
  }
  return { run_id: runId, status_url: `/api/trial/jobs?run_id=${runId}`, deadline };
}

async function consume(message, env) {
  const task = message.body;
  if (!task || typeof task.id !== 'string' || !Number.isInteger(task.page)) { message.ack(); return; }
  const persisted = await env.DB.prepare('SELECT * FROM collection_jobs WHERE id=?').bind(task.id).first();
  if (!persisted || ['succeeded','failed','expired','enqueue_failed'].includes(persisted.state)) { message.ack(); return; }
  if (Date.now() >= persisted.deadline) {
    await env.DB.prepare("UPDATE collection_jobs SET state='expired' WHERE id=? AND state<>'succeeded'").bind(task.id).run();
    message.ack(); return;
  }
  // If publishing the next page failed after commit, redelivery repairs it.
  if (task.page < persisted.next_page) {
    await env.COLLECTION_QUEUE.send({ id: task.id, page: persisted.next_page });
    message.ack(); return;
  }
  const claimed = await env.DB.prepare(`UPDATE collection_jobs SET state='running',updated_at=?,lease_until=?,attempts=attempts+1
    WHERE id=? AND next_page=? AND (state IN ('queued','retrying') OR (state='running' AND lease_until<?))`)
    .bind(Date.now(), Date.now() + 45000, task.id, task.page, Date.now()).run();
  if (!claimed.meta.changes) { message.retry({ delaySeconds: 45 }); return; }
  try {
    const budget = await env.DB.prepare(`INSERT INTO trial_budget (day,pages) VALUES (?,1)
      ON CONFLICT(day) DO UPDATE SET pages=pages+1 WHERE pages<1000`).bind(kstParts().date).run();
    if (!budget.meta.changes) throw new Error('trial_daily_page_limit');
    const result = await collectPage(persisted, env.DATA_GO_KR_SERVICE_KEY);
    if (Date.now() >= persisted.deadline) throw new Error('deadline_exceeded');
    const collectedAt = new Date().toISOString();
    // One page + checkpoint in one D1 transaction. Retrying cannot double-count.
    const writes = result.rows.map(row => env.DB.prepare(`INSERT INTO items
        (kind,source,source_key,variant,published_at,payload,collected_at) VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(kind,source,source_key,variant) DO UPDATE SET
        published_at=excluded.published_at,payload=excluded.payload,collected_at=excluded.collected_at`)
        .bind('notice', row.source, row.source_key, '', row.published_at, JSON.stringify(row), collectedAt));
    writes.push(env.DB.prepare(`UPDATE collection_jobs SET state=?,updated_at=?,candidates=candidates+?,kept=kept+?,
      filtered=filtered+?,upstream_total=?,next_page=next_page+1,lease_until=0,error='' WHERE id=?`)
      .bind(result.more ? 'queued' : 'succeeded', Date.now(), result.candidates, result.rows.length,
        result.filtered, result.total, task.id));
    await env.DB.batch(writes);
    if (result.more) await env.COLLECTION_QUEUE.send({ id: task.id, page: task.page + 1 });
    message.ack();
  } catch (error) {
    const checkpoint = await env.DB.prepare('SELECT next_page FROM collection_jobs WHERE id=?').bind(task.id).first();
    if (checkpoint.next_page > task.page) { message.retry({ delaySeconds: 5 }); return; }
    const retry = message.attempts < 3 && Date.now() + 30000 < persisted.deadline;
    // Never log arbitrary exception text: upstream URLs may contain API credentials.
    const code = /^[a-z_0-9]+$/.test(error.message) ? error.message : 'collection_failed';
    await env.DB.prepare('UPDATE collection_jobs SET state=?,error=?,updated_at=?,lease_until=0 WHERE id=?')
      .bind(retry ? 'retrying' : 'failed', code, Date.now(), task.id).run();
    if (retry) message.retry({ delaySeconds: 10 }); else message.ack();
  }
}

async function stats(env) {
  const [notices, news, pipeline, cost] = await Promise.all(
    ['notice', 'news', 'pipeline', 'cost'].map(kind => rowsFor(env, kind)));
  const result = { total: notices.length, news_total: news.length,
    pipeline_count: pipeline.length, cost_count: cost.length,
    high: notices.filter(x => x.score >= 60).length, watch: 0, review: 0,
    new_count: notices.filter(x => x.notice_type === '신규').length,
    revised_count: notices.filter(x => x.notice_type === '개정').length,
    construction_news_count: news.filter(x => x.category !== '법규·제도 개정').length,
    law_news_count: news.filter(x => x.category === '법규·제도 개정').length,
    app_version: 'cloudflare-free-trial', scheduler_enabled: false, collection_running: false,
    last_collect_date: '', last_public_bid_collect_date: '', trial: true,
    credential_configured: Boolean(env.DATA_GO_KR_SERVICE_KEY),
    collection_status: env.DATA_GO_KR_SERVICE_KEY ? 'ready' : 'awaiting_credential_authorization',
    supported_sources: ['나라장터', '누리장터'],
    unsupported_features: ['지원COK','뉴스','법규','사전 사업정보','공사비 분석','관리자 설정','자동메일'],
  };
  for (const [key, name] of Object.entries({ g2b: '나라장터', nuri: '누리장터', lh: 'LH',
    ex: '도로공사', kwater: 'K-water', kapt: '공동주택관리정보시스템', jiwoncok: '지원COK' }))
    result[`${key}_count`] = notices.filter(x => x.source === name).length;
  const { results: jobs } = await env.DB.prepare('SELECT state,deadline FROM collection_jobs WHERE deadline>?').bind(Date.now()).all();
  result.collection_running = jobs.some(j => ['queued', 'running', 'retrying'].includes(jobState(j)));
  return result;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    try {
      if (request.method === 'GET' && url.pathname === '/api/notices' && !env.DATA_GO_KR_SERVICE_KEY)
        return json({ error: '시험 서버에 API 인증키 연결 승인 대기 중입니다. 입찰공고 0건으로 확인된 것이 아닙니다.', zero_confirmed: false }, 503);
      if (request.method === 'GET' && kindRoutes[url.pathname])
        return json(filterRows(await rowsFor(env, kindRoutes[url.pathname]), url.searchParams));
      if (request.method === 'GET' && url.pathname === '/api/stats') return json(await stats(env));
      if (request.method === 'GET' && url.pathname === '/api/admin/session')
        return json({ authenticated: false, trial: true });
      if (url.pathname.startsWith('/api/trial/')) {
        if (!await authorized(request, env)) return json({ error: 'unauthorized' }, 401);
        if (request.method === 'POST' && url.pathname === '/api/trial/collect') {
          const body = await request.json();
          const lookback = Number(body.lookback_hours || 168);
          if (!Number.isInteger(lookback) || lookback < 1 || lookback > 168)
            return json({ error: 'invalid_lookback' }, 400);
          return json(await startCollection(env, lookback), 202);
        }
        if (request.method === 'GET' && url.pathname === '/api/trial/jobs') {
          const { results } = await env.DB.prepare('SELECT * FROM collection_jobs WHERE run_id=?')
            .bind(url.searchParams.get('run_id') || '').all();
          return json(results.map(job => ({ ...job, state: jobState(job) })));
        }
        if (request.method === 'GET' && url.pathname === '/api/trial/digest') {
          const rows = [...await rowsFor(env, 'notice'), ...await rowsFor(env, 'news')];
          return json({ mode: 'dry-run', sent: false, date: kstParts().date, items: todayDigest(rows) });
        }
        return json({ error: 'not_found' }, 404);
      }
      if (url.pathname.startsWith('/api/'))
        return json({ error: 'Cloudflare 시험판: 관리자 설정·실제 메일 발송은 아직 이전 검증 전입니다.' }, 501);
      const response = await env.ASSETS.fetch(request);
      if (!response.headers.get('Content-Type')?.includes('text/html')) return response;
      return new HTMLRewriter().on('body', { element(element) {
        element.prepend(`<aside style="padding:14px;background:#fff0d9;color:#492c00;text-align:center">Cloudflare 무료 시험판 — 나라장터·누리장터 수집 시험 ${env.DATA_GO_KR_SERVICE_KEY ? '' : '(API 인증키 연결 승인 대기)'}<br>지원COK·뉴스·법규 등 나머지 탭은 미연결이며 0건 확인을 의미하지 않습니다. 관리자 설정·자동메일 미지원.</aside>`, { html: true });
      } }).transform(response);
    } catch {
      return json({ error: 'trial_backend_unavailable', zero_confirmed: false }, 503);
    }
  },
  async queue(batch, env) {
    for (const message of batch.messages) await consume(message, env);
  },
  async scheduled(controller, env) {
    if (env.SCHEDULE_ENABLED !== 'true') return;
    // Use actual execution time too: never backfill a delayed event after cutoff.
    const action = scheduleAction(controller.scheduledTime);
    if (action !== scheduleAction(Date.now())) return;
    if (action === 'collect') await startCollection(env, 168, true);
    if (action === 'digest') console.log('trial_digest_dry_run_only');
  },
};
