import { kstParts, todayDigest, dateOnly } from './logic.js';
import { getSetting, getSecret, emailSettings } from './settings.js';
import {renderEmail} from './email-template.js';
export const digestItemKey=row=>JSON.stringify([row.kind,row.source,row.source_key,row.variant||'']);
export function buildPreview(rows,time=Date.now(),sentKeys=[]) {
  const date=kstParts(time).date;
  const sent=new Set(sentKeys),seen=new Set();
  const unique=rows.filter(row=>{const key=digestItemKey(row);if(seen.has(key))return false;seen.add(key);return true;});
  const selected=todayDigest(unique,time).filter(row=>!sent.has(digestItemKey(row))&&(row.kind==='news'||row.kind==='notice'&&row.score>=40));
  const rank=(a,b)=>(Number(b.score)||0)-(Number(a.score)||0)||String(b.published_at).localeCompare(String(a.published_at));
  const newNotices=selected.filter(r=>r.kind==='notice').sort(rank).slice(0,30);
  const newNews=selected.filter(r=>r.kind==='news').sort(rank).slice(0,20);
  const oldNotices=unique.filter(r=>r.kind==='notice'&&r.score>=40&&sent.has(digestItemKey(r))&&dateOnly(r.published_at)&&dateOnly(r.published_at)<=date&&
    !['취소','마감'].includes(r.notice_type)&&(!dateOnly(r.deadline_at)||dateOnly(r.deadline_at)>=date)).sort(rank).slice(0,12);
  const news=newNews.filter(r=>r.category!=='법규·제도 개정'),laws=newNews.filter(r=>r.category==='법규·제도 개정');
  const items=[...newNotices,...newNews];
  const counts={new_notices:newNotices.length,old_notices:oldNotices.length,new_news:newNews.length,construction_news:news.length,law_news:laws.length};
  const subject=`[CONCOST] ${date} 건설 기회 브리핑 · 신규 공고 ${newNotices.length}건`;
  const html=renderEmail({date,newNotices,oldNotices,news,laws});
  const text=[subject,...[['신규 입찰공고',newNotices],['기존 알림 프로젝트',oldNotices],['건설 주요뉴스',news],['법규·제도 개정',laws]].flatMap(([label,list])=>['',label,...list.map(r=>`${r.title} (${r.source}) ${/^https?:\/\//i.test(r.url||'')?r.url:''}`)])].join('\n');
  return {subject,html,text,counts,items,old_notices:oldNotices,date};
}
export async function digestPreview(env) {
  const {results}=await env.DB.prepare("SELECT kind,variant,payload FROM items WHERE kind IN ('notice','news') AND published_at<>'' ORDER BY published_at DESC LIMIT 2000").all();
  // Legacy snapshots without item keys are unknown history, never guessed from titles.
  const {results:history}=await env.DB.prepare("SELECT d.payload FROM delivery_days d WHERE EXISTS (SELECT 1 FROM deliveries r WHERE r.day=d.day AND r.status='sent' AND r.provider_id<>'') ORDER BY d.day DESC LIMIT 90").all();
  const keys=history.flatMap(d=>{const value=JSON.parse(d.payload).item_keys;return Array.isArray(value)?value:[];});
  return buildPreview(results.map(x=>({...JSON.parse(x.payload),kind:x.kind,variant:x.variant})),Date.now(),keys);
}
export async function queueDigest(env) {
  if(env.MAIL_MODE!=='live')throw new Error('trial_mail_disabled');
  const now=Date.now(),k=kstParts(now);
  if(k.weekday===0||k.weekday===6||k.hour!==10||k.minute!==0)throw new Error('outside_mail_window');
  if(await getSetting(env,'digest_enabled','false')!=='true')throw new Error('digest_disabled');
  if(!await getSecret(env,'RESEND_API_KEY'))throw new Error('missing_resend_key');
  const settings=await emailSettings(env);
  if(!settings.from_email||!settings.recipients.length)throw new Error('missing_mail_addresses');
  const existing=await env.DB.prepare('SELECT state,payload FROM delivery_days WHERE day=?').bind(k.date).first();
  if(existing?.state==='sent')return {sent:false,already_sent:true,recipient_count:JSON.parse(existing.payload).recipients.length};
  const preview=existing?null:await digestPreview(env);
  if(!existing&&!preview.items.length)throw new Error('no_verified_today_items');
  // Immutable day snapshot. A second trigger cannot change contents/recipients.
  const snapshot=existing?JSON.parse(existing.payload):{from:settings.from_email,subject:preview.subject,html:preview.html,text:preview.text,
    recipients:settings.recipients.map(r=>r.email),counts:preview.counts,item_keys:preview.items.map(digestItemKey)};
  const insert=await env.DB.prepare("INSERT OR IGNORE INTO delivery_days(day,state,created_at,payload) VALUES (?,'preparing',?,?)")
    .bind(k.date,now,JSON.stringify(snapshot)).run();
  const saved=await env.DB.prepare('SELECT state,payload FROM delivery_days WHERE day=?').bind(k.date).first();
  const original=JSON.parse(saved.payload);
  if(saved.state==='sent')return {sent:false,already_sent:true,recipient_count:original.recipients.length};
  // INSERT OR IGNORE repairs an interrupted preparation without resetting sent rows.
  await env.DB.batch(original.recipients.map(email=>env.DB.prepare('INSERT OR IGNORE INTO deliveries(day,email) VALUES (?,?)').bind(k.date,email)));
  const {results:pending}=await env.DB.prepare("SELECT email FROM deliveries WHERE day=? AND status IN ('pending','retrying')").bind(k.date).all();
  if(pending.length)await env.MAIL_QUEUE.sendBatch(pending.map(x=>({body:{type:'digest',day:k.date,email:x.email}})));
  return {sent:false,queued:true,recipient_count:original.recipients.length,...original.counts,created:Boolean(insert.meta.changes)};
}
export async function deliverMessage(message,env,fetcher=fetch,time=Date.now()) {
  const {day,email}=message.body,k=kstParts(time);
  if(env.MAIL_MODE!=='live'){message.ack();return;}
  const current=await env.DB.prepare('SELECT status FROM deliveries WHERE day=? AND email=?').bind(day,email).first();
  if(!current||['sent','failed','expired'].includes(current.status)){message.ack();return;}
  // No afternoon catch-up or previous-day redelivery. Queue latency is not a clock guarantee.
  if(day!==k.date||k.hour!==10||k.minute>4||[0,6].includes(k.weekday)) {
    await env.DB.prepare("UPDATE deliveries SET status='expired',lease_until=0 WHERE day=? AND email=? AND status<>'sent'").bind(day,email).run();message.ack();return;
  }
  const claim=await env.DB.prepare("UPDATE deliveries SET status='sending',lease_until=? WHERE day=? AND email=? AND status IN ('pending','retrying','sending') AND lease_until<?")
    .bind(time+45000,day,email,time).run();
  if(!claim.meta.changes){message.retry({delaySeconds:45});return;}
  const saved=await env.DB.prepare('SELECT payload FROM delivery_days WHERE day=?').bind(day).first();
  if(!saved){message.ack();return;}
  const snapshot=JSON.parse(saved.payload);
  try {
    if(!snapshot.recipients.includes(email))throw new Error('invalid_digest_recipient');
    const key=await getSecret(env,'RESEND_API_KEY');if(!key)throw new Error('missing_resend_key');
    const response=await fetcher('https://api.resend.com/emails',{method:'POST',signal:AbortSignal.timeout(15000),headers:{
      Authorization:`Bearer ${key}`,'Content-Type':'application/json','User-Agent':'CONCOST/1.0',
      'Idempotency-Key':`concost-${day}-${email}`},body:JSON.stringify({from:snapshot.from,to:[email],subject:snapshot.subject,html:snapshot.html,text:snapshot.text})});
    if(!response.ok)throw new Error(`resend_http_${response.status}`);
    const receipt=await response.json();if(!receipt.id)throw new Error('invalid_resend_receipt');
    await env.DB.prepare("UPDATE deliveries SET status='sent',provider_id=?,lease_until=0 WHERE day=? AND email=?").bind(receipt.id,day,email).run();
    await env.DB.prepare("UPDATE delivery_days SET state='sent',error='' WHERE day=? AND NOT EXISTS (SELECT 1 FROM deliveries WHERE day=? AND status<>'sent')").bind(day,day).run();message.ack();
  } catch(error) {
    const retry=message.attempts<3&&kstParts(Date.now()).minute<4;
    await env.DB.prepare('UPDATE deliveries SET status=?,lease_until=0 WHERE day=? AND email=?').bind(retry?'retrying':'failed',day,email).run();
    await env.DB.prepare("UPDATE delivery_days SET state='partial',error=? WHERE day=?").bind(/^resend_http_\d+$/.test(error.message)?error.message:'mail_delivery_unconfirmed',day).run();
    if(retry)message.retry({delaySeconds:10});else message.ack();
  }
}
