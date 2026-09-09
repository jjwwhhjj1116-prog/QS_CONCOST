import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {readFileSync,readdirSync} from 'node:fs';
import {pbkdf2Sync,timingSafeEqual} from 'node:crypto';
import worker from '../src/worker.js';
import {parseBoard,normalizeIntelligence,collectExtraPage,extraJobs,requestText} from '../src/extra-sources.js';
import {dateOnly,kstParts} from '../src/logic.js';
import {getSecret,saveSecret,setSetting,tokenHash,emailSettings} from '../src/settings.js';
import {buildPreview,queueDigest,deliverMessage} from '../src/mail.js';
import {xmlResponse} from '../src/other-bids.js';

crypto.subtle.timingSafeEqual=(a,b)=>timingSafeEqual(new Uint8Array(a),new Uint8Array(b));
function fixture() {
  const db=new DatabaseSync(':memory:');
  const dir=new URL('../migrations/',import.meta.url);
  for(const f of readdirSync(dir).sort())db.exec(readFileSync(new URL(f,dir),'utf8'));
  function prepare(sql,args=[]) {return {bind(...values){return prepare(sql,values);},
    async first(){return db.prepare(sql).get(...args)||null;},async all(){return {results:db.prepare(sql).all(...args)};},
    async run(){return {meta:{changes:db.prepare(sql).run(...args).changes}};}};}
  const env={DB:{prepare,async batch(statements){db.exec('BEGIN');try{const result=[];for(const s of statements)result.push(await s.run());db.exec('COMMIT');return result;}catch(e){db.exec('ROLLBACK');throw e;}}},
    SETTINGS_ENCRYPTION_KEY:'a1'.repeat(32),DATA_GO_KR_SERVICE_KEY:'fixture-key',TRIAL_ADMIN_TOKEN:'fixture-admin',MAIL_MODE:'dry-run',SCHEDULE_ENABLED:'false',
    COLLECTION_QUEUE:{messages:[],async sendBatch(items){this.messages.push(...items.map(x=>x.body));},async send(body){this.messages.push(body);}}};
  return {db,env};
}
const request=(path,method='GET',body,extra={})=>new Request('https://trial.example'+path,{method,headers:{Origin:'https://trial.example','Content-Type':'application/json',...extra},body:body===undefined?undefined:JSON.stringify(body)});
test('new collector manifests are bounded and independent; no homepage discovery',()=>{
  const jobs=extraJobs(Date.now(),48);assert.ok(jobs.length<50);assert.equal(new Set(jobs.map(j=>j.source_id)).size,jobs.length);
  assert.ok(extraJobs(Date.now(),48,'jiwon').every(j=>j.source_id.startsWith('jiwon-')));
});
test('dates never invent today and normalize compact law timestamps',()=>{
  assert.equal(dateOnly('20260908'),'2026-09-08');assert.equal(dateOnly('2026.09.08'),'2026-09-08');
  assert.equal(dateOnly('2026-02-30'),'');assert.equal(dateOnly(''), '');
});
test('CERIK list metadata only, unknown structure is an error not zero',()=>{
  const result=parseBoard('<div class="document-preview-slide-wrap"><div class="title">공사비 동향</div><b>출판일</b><span>2026.09.08</span><a href="/report/issue/42">보기</a>','news-cerik-issue');
  assert.equal(result.rows.length,1);assert.equal(result.rows[0].published_at,'2026-09-08');
  assert.throws(()=>parseBoard('<h1>Access Denied</h1>','news-cerik-issue'),/structure_unknown/);
});
test('Busan redevelopment JS links become real links without borrowing adjacent dates',()=>{
  const html=`<a onclick="move_view('42','BBSMSTR_000000000080','demo')"><h3>재개발 공사비 검증 용역</h3></a>`;
  const result=parseBoard(html,'jiwon-busan-rebuild');assert.equal(result.rows.length,1);assert.equal(result.rows[0].published_at,'');
  assert.match(result.rows[0].url,/ntt_id=42/);
});
test('pipeline and contract identities/amounts keep their own meanings',()=>{
  const row=normalizeIntelligence({untyCntrctNo:'a',cntrctNm:'공사비 검증 용역',totCntrctAmt:'10,000',cntrctDate:'20260908'},'contract-Servc');
  assert.equal(row.kind,'cost');assert.equal(row.contract_amount,10000);assert.equal(row.award_amount,null);
  assert.throws(()=>normalizeIntelligence({cntrctNm:'공사비 검증 용역'},'contract-Servc'),/identity/);
});
test('law credentials are required and API schema failure stays explicit',async()=>{
  await assert.rejects(collectExtraPage({source_id:'law-0'},{}),/missing_law/);
  await assert.rejects(collectExtraPage({source_id:'law-0',start_date:'202609010000',end_date:'202609080000',next_page:1},{LAW_API_OC:'test'},async()=>Response.json({error:'bad'})),/schema_unknown/);
  assert.throws(()=>xmlResponse('<html>service unavailable</html>'),/api_error/);
});
test('response byte limit stops oversized upstream data',async()=>{
  await assert.rejects(requestText('https://example.org',async()=>new Response('x'.repeat(1500001))),/too_large/);
});
test('D1 encryption survives empty save and wrong encryption key fails closed',async()=>{
  const {env}=fixture();await saveSecret(env,'RESEND_API_KEY','test-secret-value');await saveSecret(env,'RESEND_API_KEY','');
  assert.equal(await getSecret({...env},'RESEND_API_KEY'),'test-secret-value');
  const raw=await env.DB.prepare('SELECT value FROM settings').first();assert.ok(!raw.value.includes('test-secret-value'));
  await assert.rejects(getSecret({...env,SETTINGS_ENCRYPTION_KEY:'b2'.repeat(32)},'RESEND_API_KEY'));
});
test('admin login, persistent session, origin check, blank key save and address deduplication',async()=>{
  const {env}=fixture();const password='test-password-only',salt='11'.repeat(16),iterations=1000;
  env.ADMIN_VERIFIER=JSON.stringify({username:'test-admin',password_salt:salt,password_hash:pbkdf2Sync(password,Buffer.from(salt,'hex'),iterations,32,'sha256').toString('hex'),iterations});
  assert.equal((await worker.fetch(request('/api/admin/settings'),env)).status,401);
  const response=await worker.fetch(request('/api/admin/login','POST',{username:'test-admin',password}),env);
  assert.equal(response.status,200);const cookie=response.headers.get('set-cookie').split(';')[0];
  const session=await worker.fetch(request('/api/admin/session','GET',undefined,{Cookie:cookie}),{...env});assert.equal((await session.json()).authenticated,true);
  assert.equal((await worker.fetch(request('/api/admin/settings','PUT',{api_key:''},{Cookie:cookie,Origin:'https://evil.example'}),env)).status,403);
  assert.equal((await worker.fetch(request('/api/admin/settings','PUT',{api_key:''},{Cookie:cookie}),env)).status,200);
  assert.equal(await getSecret(env,'DATA_GO_KR_SERVICE_KEY'),'fixture-key');
  for(let i=0;i<2;i++)assert.equal((await worker.fetch(request('/api/admin/recipients','POST',{email:'test@example.org',name:'Fixture'},{Cookie:cookie}),env)).status,200);
  assert.equal((await env.DB.prepare('SELECT COUNT(*) n FROM recipients').first()).n,1);
  assert.equal((await worker.fetch(request('/api/admin/send-digest','POST',undefined,{Cookie:cookie}),env)).status,409);
});
test('collector start retries attach to the existing job instead of enqueueing twice',async()=>{
  const {env}=fixture(),headers={Authorization:'Bearer fixture-admin'};
  const a=await (await worker.fetch(request('/api/trial/collect','POST',{lookback_hours:24,scope:'news'},headers),env)).json();
  const count=env.COLLECTION_QUEUE.messages.length;
  const b=await (await worker.fetch(request('/api/trial/collect','POST',{lookback_hours:24,scope:'news'},headers),env)).json();
  assert.equal(a.run_id,b.run_id);assert.equal(b.already_running,true);assert.equal(env.COLLECTION_QUEUE.messages.length,count);
});
test('collection status assigns actual source groups instead of calling all records bids',async()=>{
  const {env,db}=fixture(),token='ab'.repeat(32);
  db.prepare('INSERT INTO sessions VALUES (?,?)').run(await tokenHash(token),Date.now()+60000);
  const headers={Cookie:'__Host-concost='+token};
  const started=await (await worker.fetch(request('/api/collect','POST',{lookback_hours:24},headers),env)).json();
  const response=await worker.fetch(request('/api/collect/status/'+started.job_id,'GET',undefined,headers),env);
  assert.equal(response.status,200);const job=await response.json();
  assert.equal(job.sources.find(s=>s.source==='나라장터 용역').group,'입찰공고');
  assert.equal(job.sources.find(s=>s.source==='CERIK 동향브리핑').group,'건설 뉴스');
  assert.equal(job.sources.find(s=>s.source==='조달청 훈령').group,'법규·제도');
  assert.ok(job.sources.some(s=>s.group==='공사비 분석'));
  assert.ok(job.sources.some(s=>s.group==='사전 사업정보'));
  assert.equal(job.sources.find(s=>s.source==='서울교통공사').group,'지원COK');
});
test('today-only email excludes old, undated and cancelled and escapes source HTML',()=>{
  const today='2026-09-08';const row={source:'source',source_key:'a',kind:'news',category:'건설 주요뉴스',title:'<script>bad</script>',published_at:'20260908',url:'javascript:bad'};
  const preview=buildPreview([row,{...row,source_key:'b',published_at:''},{...row,source_key:'c',published_at:'2026-09-01'}],Date.parse(today+'T01:00:00Z'));
  assert.equal(preview.items.length,1);assert.ok(!preview.html.includes('<script>'));assert.ok(!preview.html.includes('javascript:'));
});
test('dry-run never enqueues mail, and day/recipient uniqueness is durable',async()=>{
  const {env,db}=fixture();await assert.rejects(queueDigest(env),/trial_mail_disabled/);
  db.prepare("INSERT INTO delivery_days VALUES ('2026-09-08','sent',1,'{}','')").run();
  assert.throws(()=>db.prepare("INSERT INTO delivery_days VALUES ('2026-09-08','pending',2,'{}','')").run());
  let ack=false,called=false;await deliverMessage({body:{type:'digest',day:'2026-09-08',email:'test@example.org'},ack(){ack=true;}},env,()=>{called=true;});
  assert.equal(ack,true);assert.equal(called,false);
});
test('environment addressbook imports once, persists and does not resurrect deletions',async()=>{
  const {env,db}=fixture();env.DIGEST_RECIPIENTS='a@example.org,b@example.org,A@example.org';
  assert.equal((await emailSettings(env)).recipients.length,2);
  db.exec("DELETE FROM recipients WHERE email='a@example.org'");
  assert.equal((await emailSettings({...env})).recipients.length,1);
  assert.equal((await worker.fetch(request('/api/automation/status'),env)).status,200);
});
test('10AM cron sends once through separate mail queue, without administrator session',async(t)=>{
  const {env,db}=fixture();env.MAIL_MODE='live';env.SCHEDULE_ENABLED='true';
  env.RESEND_API_KEY='re_fixture';env.DIGEST_FROM_EMAIL='CONCOST <news@example.org>';env.DIGEST_RECIPIENTS='a@example.org,b@example.org';
  env.MAIL_QUEUE={messages:[],async sendBatch(ms){this.messages.push(...ms.map(m=>m.body));}};
  const time=Date.parse('2026-09-09T10:00:01+09:00');t.mock.method(Date,'now',()=>time);
  const row={kind:'news',source:'test',source_key:'1',title:'공사비 뉴스',category:'건설 주요뉴스',published_at:'2026-09-09',url:'https://example.org/news'};
  db.prepare('INSERT INTO items VALUES (?,?,?,?,?,?,?)').run('news','test','1','','2026-09-09',JSON.stringify(row),'2026-09-09');
  await worker.scheduled({scheduledTime:time},env);
  assert.equal(env.MAIL_QUEUE.messages.length,2);assert.equal(env.COLLECTION_QUEUE.messages.length,0);
  let calls=0;const provider=async(url,opts)=>{calls++;assert.equal(JSON.parse(opts.body).to.length,1);assert.ok(opts.headers['Idempotency-Key']);return Response.json({id:'receipt-'+calls});};
  for(const body of env.MAIL_QUEUE.messages){const msg={body,attempts:1,ack(){},retry(){throw Error('unexpected retry');}};await deliverMessage(msg,env,provider,time);await deliverMessage(msg,env,provider,time);}
  assert.equal(calls,2);assert.equal(db.prepare('SELECT state FROM delivery_days').get().state,'sent');
  await worker.scheduled({scheduledTime:time},env);assert.equal(env.MAIL_QUEUE.messages.length,2);
  const status=await (await worker.fetch(request('/api/automation/status'),env)).json();
  assert.equal(status.counts[0].provider_accepted,2);assert.ok(!JSON.stringify(status).includes('a@example.org'));
});
test('no old content mail; failed 10AM preparation is recorded, not silently successful',async(t)=>{
  const {env}=fixture();env.MAIL_MODE='live';env.SCHEDULE_ENABLED='true';env.RESEND_API_KEY='re_fixture';
  env.DIGEST_FROM_EMAIL='news@example.org';env.DIGEST_RECIPIENTS='a@example.org';
  const time=Date.parse('2026-09-09T10:00:01+09:00');t.mock.method(Date,'now',()=>time);
  await assert.rejects(worker.scheduled({scheduledTime:time},env),/no_verified_today_items/);
  const status=await (await worker.fetch(request('/api/automation/status'),env)).json();assert.equal(status.last_scheduler_error,'no_verified_today_items');
  t.mock.method(Date,'now',()=>time+3600000);await worker.scheduled({scheduledTime:time},env);
  assert.equal(env.COLLECTION_QUEUE.messages.length,0);
});
