import { Container, getContainer } from '@cloudflare/containers';
import { env as bindings } from 'cloudflare:workers';
import { filterRows, jobState, kstParts, scheduleAction, todayDigest, validateResult } from './logic.js';

export class Collector extends Container {
  defaultPort = 8080;
  sleepAfter = '5m';
  envVars = {
    DATA_GO_KR_SERVICE_KEY: bindings.DATA_GO_KR_SERVICE_KEY || '',
    LAW_API_OC: bindings.LAW_API_OC || '',
  };
}

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
  const response = await getContainer(env.COLLECTOR, 'collector-0').fetch(new Request(
    'http://container/sources', { signal: AbortSignal.timeout(20000) }));
  if (!response.ok) throw new Error('collector_manifest_unavailable');
  const manifest = await response.json();
  if (!Array.isArray(manifest) || manifest.length < 1 || manifest.length > 100)
    throw new Error('invalid_source_manifest');
  for (const source of manifest) {
    if (!/^[a-z0-9-]+$/.test(source.id)) throw new Error('invalid_source_id');
    const id = `${runId}:${source.id}`;
    const inserted = await env.DB.prepare(`INSERT OR IGNORE INTO collection_jobs
      (id,run_id,source_id,label,created_at,deadline,updated_at) VALUES (?,?,?,?,?,?,?)`)
      .bind(id, runId, source.id, source.label, now, deadline, now).run();
    if (!inserted.meta.changes) continue;
    try {
      await env.COLLECTION_QUEUE.send({ id, source_id: source.id, lookback, deadline });
    } catch {
      await env.DB.prepare("UPDATE collection_jobs SET state='enqueue_failed',error='queue_unavailable' WHERE id=?").bind(id).run();
      // Explicit error state, never reported as a successful zero-row source.
    }
  }
  return { run_id: runId, status_url: `/api/trial/jobs?run_id=${runId}`, deadline };
}

async function consume(message, env) {
  const job = message.body;
  const persisted = await env.DB.prepare('SELECT * FROM collection_jobs WHERE id=?').bind(job.id).first();
  if (!persisted || persisted.source_id !== job.source_id) { message.ack(); return; }
  if (Date.now() >= persisted.deadline) {
    await env.DB.prepare("UPDATE collection_jobs SET state='expired' WHERE id=? AND state<>'succeeded'").bind(job.id).run();
    message.ack(); return;
  }
  const claimed = await env.DB.prepare(`UPDATE collection_jobs SET state='running',updated_at=?,attempts=attempts+1
    WHERE id=? AND state IN ('queued','retrying')`).bind(Date.now(), job.id).run();
  if (!claimed.meta.changes) { message.ack(); return; }
  try {
    const shard = [...job.source_id].reduce((n, c) => n + c.charCodeAt(0), 0) % 2;
    const timeout = Math.min(180, Math.floor((persisted.deadline - Date.now()) / 1000) - 15);
    if (timeout <= 0) throw new Error('deadline_exceeded');
    const response = await getContainer(env.COLLECTOR, `collector-${shard}`).fetch(new Request('http://container/collect', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source_id: job.source_id, lookback: job.lookback, timeout }),
      signal: AbortSignal.timeout((timeout + 10) * 1000),
    }));
    if (!response.ok) throw new Error('collector_http_failure');
    const result = await response.json();
    validateResult(result, job.source_id);
    if (!result.ok) throw new Error(result.error || 'source_failed');
    if (Date.now() >= persisted.deadline) throw new Error('deadline_exceeded');
    const collectedAt = new Date().toISOString();
    // ponytail: 50-row D1 batches. Incremental commits intentionally survive later failure.
    for (let i = 0; i < result.rows.length; i += 50) {
      const chunk = result.rows.slice(i, i + 50);
      await env.DB.batch(chunk.map(row => env.DB.prepare(`INSERT INTO items
        (kind,source,source_key,variant,published_at,payload,collected_at) VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(kind,source,source_key,variant) DO UPDATE SET
        published_at=excluded.published_at,payload=excluded.payload,collected_at=excluded.collected_at`)
        .bind(result.kind, row.source, String(row.source_key), row.stage || row.record_type || '',
          row.published_at || row.recorded_at || '', JSON.stringify(row), collectedAt)));
    }
    await env.DB.prepare(`UPDATE collection_jobs SET state='succeeded',updated_at=?,candidates=?,kept=?,filtered=?,error=''
      WHERE id=?`).bind(Date.now(), result.candidates, result.kept, result.filtered, job.id).run();
    message.ack();
  } catch (error) {
    const retry = message.attempts < 2 && Date.now() + 45000 < persisted.deadline;
    // Never log arbitrary exception text: upstream URLs may contain API credentials.
    const code = /^[a-z_]+$/.test(error.message) ? error.message : 'collection_failed';
    await env.DB.prepare('UPDATE collection_jobs SET state=?,error=?,updated_at=? WHERE id=?')
      .bind(retry ? 'retrying' : 'failed', code, Date.now(), job.id).run();
    if (retry) message.retry({ delaySeconds: 30 }); else message.ack();
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
    app_version: 'cloudflare-trial', scheduler_enabled: false, collection_running: false,
    last_collect_date: '', last_public_bid_collect_date: '', trial: true,
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
        element.prepend('<aside style="padding:14px;background:#fff0d9;color:#492c00;text-align:center">Cloudflare 이전 시험판 · 운영과 별도 데이터 · 관리자 설정/실제 메일 발송 미지원</aside>', { html: true });
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
