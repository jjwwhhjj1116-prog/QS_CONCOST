import test from 'node:test';
import assert from 'node:assert/strict';
import {claimApprovalMail} from '../src/claim-approval-mail.js';
const id='11111111-1111-4111-8111-111111111111',origin='https://concost-claim-center-preview.jjwwhhjj1116.workers.dev';
const payload=()=>({messageType:'REPORT_APPROVED_DELIVERY_REQUIRED',to:'qa@con-cost.com',subject:'[클레임센터] 합성 승인 검수',text:`QA님,\n\n승인 보고서 확인·출력: ${origin}/reports/studio?caseId=${id}&step=5\n결재 내용: ${origin}/approval?reviewId=${id}`,idempotencyKey:id});
const req=(body=payload(),url='https://claim-center.internal/approved')=>new Request(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
function fixture(){const reads=[];return {reads,env:{MAIL_MODE:'live',RESEND_API_KEY:'synthetic-provider-key',DIGEST_FROM_EMAIL:'CONCOST <qa@con-cost.co.kr>',DB:{prepare(sql){assert.match(sql,/^SELECT value FROM settings WHERE key=\?$/u,'Only existing settings reads');return {bind(key){reads.push(key);return {async first(){return null;}};}}}}}};}
test('private approval handler reuses configured sender/key without changing QS settings or deliveries',async()=>{
  const f=fixture(),sent=[];const fetcher=async(url,init)=>{sent.push({url,init});return Response.json({id});};
  for(let i=0;i<2;i++)assert.equal((await claimApprovalMail(req(),f.env,fetcher)).headers.get('X-Message-Id'),id);
  assert.deepEqual(f.reads,['from_email','secret:RESEND_API_KEY','from_email','secret:RESEND_API_KEY']);
  assert.equal(sent[0].url,'https://api.resend.com/emails');assert.equal(sent[0].init.redirect,'manual');assert.equal(sent[0].init.headers['Idempotency-Key'],sent[1].init.headers['Idempotency-Key']);
  const {html,...plain}=JSON.parse(sent[0].init.body);assert.equal(typeof html,'string');assert.deepEqual(plain,{from:f.env.DIGEST_FROM_EMAIL,to:['qa@con-cost.com'],subject:payload().subject,text:payload().text});
});
test('private approval handler rejects external recipients, arbitrary message fields and forged report links before any lookup/send',async()=>{
  const invalid=[{to:'qa@elsewhere.invalid'},{to:'qa@con-cost.com.evil.invalid'},{subject:'other mail'},{subject:'[클레임센터] header\r\ninjection'},{html:'<b>unexpected</b>'},{idempotencyKey:[id]},{text:payload().text.replaceAll(origin,origin+'.evil.invalid')},{text:payload().text.replaceAll(origin,origin.replace('https://','https://user:password@'))},{text:payload().text.replace('step=5','step=4')},{text:payload().text.replace('reviewId='+id,'reviewId=not-a-review')},{messageType:'DIGEST'}];
  for(const changes of invalid){const f=fixture();assert.equal((await claimApprovalMail(req({...payload(),...changes}),f.env,()=>{throw Error('Must not send');})).status,400);assert.equal(f.reads.length,0);}
  const f=fixture();assert.equal((await claimApprovalMail(req(payload(),'https://public.invalid/approved'),f.env)).status,400);assert.equal(f.reads.length,0);
});
test('provider failure and missing real receipt never fabricate acceptance or leak provider content',async()=>{
  for(const [status,body,expected] of [[401,{error:'private-provider-detail'},401],[200,{accepted:true},202]]){const f=fixture(),r=await claimApprovalMail(req(),f.env,async()=>Response.json(body,{status}));assert.equal(r.status,expected);assert.equal(r.headers.get('X-Message-Id'),null);assert.doesNotMatch(await r.text(),/private-provider-detail|synthetic-provider-key/u);}
});
test('paused mail or missing sender configuration never calls provider',async()=>{
  const f=fixture();f.env.MAIL_MODE='disabled';assert.equal((await claimApprovalMail(req(),f.env,()=>{throw Error('Must not send');})).status,503);assert.equal(f.reads.length,0);
  f.env.MAIL_MODE='live';f.env.DIGEST_FROM_EMAIL='';assert.equal((await claimApprovalMail(req(),f.env,()=>{throw Error('Must not send');})).status,503);
});

test('approval HTML contains exactly the two validated links while preserving the complete text alternative',async()=>{
 for(const base of [origin,origin.replace('-preview.','-development.')]){
  const body=payload();body.text=body.text.replaceAll(origin,base);const f=fixture();let delivered;
  const response=await claimApprovalMail(req(body),f.env,async(_url,init)=>{delivered=JSON.parse(init.body);return Response.json({id});});
  assert.equal(response.status,200);assert.equal(delivered.text,body.text);assert.equal(delivered.subject,body.subject);assert.deepEqual(delivered.to,[body.to]);
  assert.equal(typeof delivered.html,'string');
  const links=[...delivered.html.matchAll(/<a\b[^>]*\bhref=["']([^"']+)["'][^>]*>/giu)].map(m=>m[1].replaceAll('&amp;','&'));
  assert.deepEqual(links,[base+'/reports/studio?caseId='+id+'&step=5',base+'/approval?reviewId='+id]);
  assert.equal((delivered.html.match(/<a\b/giu)||[]).length,2);
 }
});
test('approval HTML escapes name/body markup and quotes without changing plain text or creating links',async()=>{
 const body=payload();body.text=body.text.replace('QA님,','<script>alert("QA")</script> & \'name\' <a href="https://evil.invalid">name</a>님,');
 const f=fixture();let delivered;
 assert.equal((await claimApprovalMail(req(body),f.env,async(_url,init)=>{delivered=JSON.parse(init.body);return Response.json({id});})).status,200);
 assert.equal(delivered.text,body.text);assert.equal(typeof delivered.html,'string');
 assert.doesNotMatch(delivered.html,/<script\b|<a\b[^>]*https:\/\/evil\.invalid/iu);
 assert.match(delivered.html,/&lt;script&gt;/u);assert.match(delivered.html,/&amp;/u);
 assert.match(delivered.html,/&quot;|&#34;/u);assert.match(delivered.html,/&#39;|&#x27;|&apos;/u);
 assert.equal((delivered.html.match(/<a\b/giu)||[]).length,2);
});
