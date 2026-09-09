import { filterRows, jobState, kstParts, scheduleAction, todayDigest } from './logic.js';
import { initialJobs, collectPage } from './bid-api.js';
import { extraJobs, collectExtraPage, BOARDS, INTELLIGENCE } from './extra-sources.js';
import { sessionValid, sameOrigin, login, settingsRoute, collectionEnv, getSecret, requestBody, emailSettings, setSetting, getSetting } from './settings.js';
import { trialUI } from './trial-ui.js';
import { digestPreview,queueDigest,deliverMessage } from './mail.js';

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

async function startCollection(env, lookback = 168, scheduled = false, scope = 'all') {
  const now = Date.now(), k = kstParts(now);
  const runId = scheduled ? `${k.date}-${k.hour}-${Math.floor(k.minute / 5)}` : crypto.randomUUID();
  const deadline = scheduled ? Date.parse(`${k.date}T09:55:00+09:00`) : now + 300000;
  if (!await getSecret(env,'DATA_GO_KR_SERVICE_KEY')) throw new Error('missing_api_key');
  const existing=await env.DB.prepare('SELECT run_id,deadline FROM collection_gate WHERE scope=? AND deadline>?').bind(scope,now).first();
  if(existing)return {run_id:existing.run_id,job_id:existing.run_id,status_url:`/api/trial/jobs?run_id=${existing.run_id}`,deadline:existing.deadline,already_running:true};
  const used = await env.DB.prepare('SELECT COUNT(DISTINCT run_id) AS total FROM collection_jobs WHERE created_at>=?')
    .bind(Date.parse(`${k.date}T00:00:00+09:00`)).first();
  if (used.total >= 2) throw new Error('trial_daily_run_limit');
  const manifest = [...(scope==='all'||scope==='bids'?initialJobs(now,lookback):[]),...(scope==='bids'?[]:extraJobs(now,lookback,scope))];
  const gate=await env.DB.prepare('INSERT INTO collection_gate VALUES (?,?,?) ON CONFLICT(scope) DO UPDATE SET run_id=excluded.run_id,deadline=excluded.deadline WHERE deadline<=?').bind(scope,runId,deadline,now).run();
  if(!gate.meta.changes)throw new Error('collection_already_starting');
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
  return { run_id: runId, job_id:runId, status_url: `/api/trial/jobs?run_id=${runId}`, deadline };
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
    const configured=await collectionEnv(env);
    const result = /^(g2b|nuri)-/.test(persisted.source_id)
      ? await collectPage(persisted, configured.DATA_GO_KR_SERVICE_KEY)
      : await collectExtraPage(persisted, configured);
    if (Date.now() >= persisted.deadline) throw new Error('deadline_exceeded');
    const collectedAt = new Date().toISOString();
    if(result.rows.length>30)throw new Error('board_page_exceeds_free_write_budget');
    // One page + checkpoint in one D1 transaction. Retrying cannot double-count.
    const writes = result.rows.map(row => env.DB.prepare(`INSERT INTO items
        (kind,source,source_key,variant,published_at,payload,collected_at) VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(kind,source,source_key,variant) DO UPDATE SET
        published_at=excluded.published_at,payload=json_set(excluded.payload,'$.workflow_status',coalesce(json_extract(items.payload,'$.workflow_status'),'new')),collected_at=excluded.collected_at`)
        .bind(row.kind||'notice', row.source, row.source_key, '', row.published_at, JSON.stringify(row), collectedAt));
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
    app_version: 'cloudflare-mail-cutover', scheduler_enabled: env.SCHEDULE_ENABLED==='true', collection_running: false,
    last_collect_date: '', last_public_bid_collect_date: '', trial: true,
    credential_configured: Boolean(await getSecret(env,'DATA_GO_KR_SERVICE_KEY')),
    collection_status: 'ready',
    supported_sources: ['나라장터', '누리장터'],
    unsupported_features: ['관리자 비밀번호 변경','지원COK 메일 가져오기'],
  };
  for (const [key, name] of Object.entries({ g2b: '나라장터', nuri: '누리장터', lh: 'LH',
    ex: '도로공사', kwater: 'K-water', kapt: '공동주택관리정보시스템', jiwoncok: '지원COK' }))
    result[`${key}_count`] = notices.filter(x => x.source === name).length;
  const { results: jobs } = await env.DB.prepare('SELECT state,deadline FROM collection_jobs WHERE deadline>?').bind(Date.now()).all();
  result.collection_running = jobs.some(j => ['queued', 'running', 'retrying'].includes(jobState(j)));
  const {results:recent}=await env.DB.prepare('SELECT source_id,label,state,deadline,updated_at,candidates,kept,error FROM collection_jobs ORDER BY created_at DESC LIMIT 120').all();
  const seen=new Set();result.source_health=recent.filter(j=>!seen.has(j.source_id)&&seen.add(j.source_id)).map(j=>({...j,state:jobState(j)}));
  result.last_collect_date=recent.length?new Date(Math.max(...recent.map(j=>j.updated_at))).toISOString():'';
  return result;
}

async function runSummary(env,id) {
  const {results}=await env.DB.prepare('SELECT * FROM collection_jobs WHERE run_id=?').bind(id).all();
  if(!results.length)return null;
  const group=id=>INTELLIGENCE[id]?.[0]==='pipeline'?'사전 사업정보':INTELLIGENCE[id]?.[0]==='cost'?'공사비 분석':id.startsWith('jiwon-')?'지원COK':id.startsWith('law-')||BOARDS[id]?.[3]==='법규·제도 개정'?'법규·제도':id.startsWith('news-')?'건설 뉴스':'입찰공고';
  const sources=results.map(j=>({source:j.label,group:group(j.source_id),state:jobState(j),ok:j.state==='succeeded',candidates:j.candidates||0,total:j.kept||0,filtered:j.filtered||0,error:j.error|| (jobState(j)==='expired'?'deadline_exceeded':'')}));
  const done=sources.filter(x=>['succeeded','failed','expired','enqueue_failed'].includes(x.state)).length;
  const total=sources.reduce((n,x)=>n+x.total,0),complete=done===sources.length;
  return {job_id:id,status:complete?'complete':'running',ok:sources.some(x=>x.ok)||total>0,partial:sources.some(x=>['failed','expired','enqueue_failed'].includes(x.state)),
    percent:complete?100:Math.max(5,Math.round(done/sources.length*95)),total,inserted:0,updated:0,unchanged:0,sources,
    message:complete?`확인된 적합 자료 ${total}건 (처리 건수, 중복 포함 가능)`:'기관별로 수집 중입니다. 저장된 자료는 즉시 표시됩니다.'};
}

export default {
  async fetch(request, env) {
      const url = new URL(request.url);
    try {
      if(request.method==='GET'&&url.pathname==='/cf-trial.js')return new Response(trialUI,{headers:{'Content-Type':'application/javascript; charset=utf-8','Cache-Control':'no-store'}});
      if (request.method === 'GET' && url.pathname === '/api/notices' && !await getSecret(env,'DATA_GO_KR_SERVICE_KEY'))
        return json({ error: '시험 서버에 API 인증키 연결 승인 대기 중입니다. 입찰공고 0건으로 확인된 것이 아닙니다.', zero_confirmed: false }, 503);
      if (request.method === 'GET' && kindRoutes[url.pathname])
        return json(filterRows(await rowsFor(env, kindRoutes[url.pathname]), url.searchParams));
      if (request.method === 'GET' && url.pathname === '/api/stats') return json(await stats(env));
      if (request.method === 'GET' && url.pathname === '/api/automation/status') {
        const settings=await emailSettings(env);
        const {results:days}=await env.DB.prepare('SELECT day,state,created_at,error FROM delivery_days ORDER BY day DESC LIMIT 7').all();
        const {results:counts}=await env.DB.prepare("SELECT day,status,COUNT(*) AS count,SUM(CASE WHEN provider_id<>'' THEN 1 ELSE 0 END) AS provider_accepted FROM deliveries GROUP BY day,status ORDER BY day DESC LIMIT 30").all();
        return json({scheduler_enabled:settings.scheduler_active,mail_enabled:settings.enabled,mail_mode:env.MAIL_MODE,
          provider_configured:settings.provider_configured,from_configured:Boolean(settings.from_email),recipient_count:settings.recipients.length,
          timezone:'Asia/Seoul',collection_window:'월~금 09:00~09:55',send_time:'월~금 10:00',
          last_scheduler_event:await getSetting(env,'last_scheduler_event'),last_scheduler_error:await getSetting(env,'last_scheduler_error'),days,counts});
      }
      if (request.method === 'GET' && url.pathname === '/api/admin/session')
        return json({ authenticated: await sessionValid(request,env), trial: true });
      if(url.pathname==='/api/admin/login'&&request.method==='POST')return login(request,env);
      const adminRoute=url.pathname.startsWith('/api/admin/') || url.pathname.startsWith('/api/collect') || (url.pathname.startsWith('/api/notices/')&&request.method==='PATCH');
      if(adminRoute) {
        if(!await sessionValid(request,env))return json({error:'관리자 로그인이 필요합니다.'},401);
        if(request.method!=='GET'&&!sameOrigin(request))return json({error:'동일 사이트에서 요청하세요.'},403);
        const settings=await settingsRoute(request,env);if(settings)return settings;
        if(request.method==='GET'&&url.pathname==='/api/admin/digest-preview')return json(await digestPreview(env));
        if(url.pathname==='/api/admin/recovery-digest') {
          if(request.method==='POST')return json(await queueDigest(env,true),202);
          if(request.method==='GET') {
            const preview=await digestPreview(env,true),settings=await emailSettings(env);
            const action=`<div style="padding:24px;text-align:center;background:#fff5df"><p>9월 8일 등록 자료 보완 발송 · 주소록 ${settings.recipients.length}명<br>평소 예약은 변경하지 않습니다. 오늘 한 번만 발송합니다.</p><form method="post" action="/api/admin/recovery-digest"><button ${preview.items.length?'':'disabled'} style="padding:16px;background:#ed5b18;color:white;border:0;border-radius:8px;font-weight:bold">9월 8일 자료를 주소록에 한 번 발송</button></form></div>`;
            return new Response(preview.html.replace(/<body[^>]*>/,body=>body+action),{headers:{'Content-Type':'text/html; charset=utf-8','Cache-Control':'no-store'}});
          }
        }
        if(request.method==='POST'&&url.pathname==='/api/admin/test-email')return json({error:'시험 수신자를 따로 지정하는 기능은 아직 준비 중입니다. 예약발송 결과를 확인하세요.'},409);
        if(request.method==='POST'&&url.pathname==='/api/admin/send-digest') {
          if(env.MAIL_MODE!=='live')return json({error:'시험판 실제 메일 발송은 비활성입니다. HTML 미리보기를 이용하세요.'},409);
          return json(await queueDigest(env),202);
        }
        if(request.method==='POST'&&['/api/collect','/api/admin/collect-news','/api/admin/collect-jiwoncok'].includes(url.pathname)) {
          const body=request.body?await requestBody(request):{},lookback=Number(body.lookback_hours||48);
          if(!Number.isInteger(lookback)||lookback<1||lookback>168)return json({error:'조회 범위는 1~168시간입니다.'},400);
          return json(await startCollection(env,lookback,false,url.pathname.endsWith('collect-news')?'news':url.pathname.endsWith('collect-jiwoncok')?'jiwon':'all'),202);
        }
        if(request.method==='GET'&&url.pathname.startsWith('/api/collect/status/')) {
          const result=await runSummary(env,url.pathname.split('/').pop());return json(result||{error:'수집 작업을 찾을 수 없습니다.'},result?200:404);
        }
        if(request.method==='PATCH'&&url.pathname.startsWith('/api/notices/')) {
          const {status}=await requestBody(request);if(!['new','review','watch','excluded'].includes(status))return json({error:'잘못된 검토 상태'},400);
          const result=await env.DB.prepare("UPDATE items SET payload=json_set(payload,'$.workflow_status',?) WHERE kind='notice' AND json_extract(payload,'$.id')=?").bind(status,decodeURIComponent(url.pathname.split('/').pop())).run();
          return json({ok:Boolean(result.meta.changes)});
        }
      }
      if (url.pathname.startsWith('/api/trial/')) {
        if (!await authorized(request, env)) return json({ error: 'unauthorized' }, 401);
        if (request.method === 'POST' && url.pathname === '/api/trial/collect') {
          const body = await request.json();
          const lookback = Number(body.lookback_hours || 168);
          if (!Number.isInteger(lookback) || lookback < 1 || lookback > 168)
            return json({ error: 'invalid_lookback' }, 400);
          const scope=body.scope||'all';if(!['all','bids','news','jiwon'].includes(scope))return json({error:'invalid_scope'},400);
          return json(await startCollection(env, lookback,false,scope), 202);
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
      return new HTMLRewriter().on('head',{element(element){element.append('<script src="/cf-trial.js" defer></script>',{html:true});}}).on('body', { element(element) {
        element.prepend(`<aside style="padding:14px;background:#fff0d9;color:#492c00;text-align:center">Cloudflare 이전 사이트 — Render와 별도 데이터입니다.<br>월~금 09:00 수집 시작·09:55 마감 / 10:00 자동메일 예약. 일부 기관 오류는 수집 결과에서 별도 확인합니다.</aside>`, { html: true });
      } }).transform(response);
    } catch(error) {
      const code=/^[a-z_0-9]+$/.test(error.message)?error.message:'trial_backend_unavailable';
      const messages={trial_daily_run_limit:'무료 시험 수집은 하루 2회까지입니다. 기존 자료는 유지됩니다.',collection_already_starting:'다른 수집 요청이 시작 중입니다. 잠시 후 상태를 확인하세요.',admin_not_migrated:'관리자 인증 정보 이전이 아직 완료되지 않았습니다.'};
      return json({ error: messages[code]||code, zero_confirmed: false },code==='trial_daily_run_limit'?429:503);
    }
  },
  async queue(batch, env) {
    for (const message of batch.messages) {
      if(message.body?.type==='digest')await deliverMessage(message,env);
      else await consume(message, env);
    }
  },
  async scheduled(controller, env) {
    if (env.SCHEDULE_ENABLED !== 'true') return;
    // Use actual execution time too: never backfill a delayed event after cutoff.
    const action = scheduleAction(controller.scheduledTime);
    if (action !== scheduleAction(Date.now())) return;
    if(!action)return;
    await setSetting(env,'last_scheduler_event',`${new Date().toISOString()} ${action}`);
    try {
      if (action === 'collect') await startCollection(env, 24, true);
      if (action === 'digest'&&env.MAIL_MODE==='live') await queueDigest(env);
      await setSetting(env,'last_scheduler_error','');
    } catch(error) {
      const code=/^[a-z_0-9]+$/.test(error.message)?error.message:'scheduled_job_failed';
      await setSetting(env,'last_scheduler_error',code);throw new Error(code);
    }
  },
};
