const enc=new TextEncoder();
const hex=bytes=>Array.from(new Uint8Array(bytes),b=>b.toString(16).padStart(2,'0')).join('');
const unhex=value=>Uint8Array.from(value.match(/../g)||[],b=>parseInt(b,16));
export const json=(data,status=200,headers={})=>Response.json(data,{status,headers:{'Cache-Control':'no-store',...headers}});
export async function getSetting(env,key,fallback='') {
  const row=await env.DB.prepare('SELECT value FROM settings WHERE key=?').bind(key).first(); return row?.value??fallback;
}
export async function setSetting(env,key,value) {
  await env.DB.prepare('INSERT INTO settings (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value').bind(key,String(value)).run();
}
async function cipherKey(env) {
  if(!env.SETTINGS_ENCRYPTION_KEY)throw new Error('settings_encryption_not_configured');
  return crypto.subtle.importKey('raw',unhex(env.SETTINGS_ENCRYPTION_KEY),'AES-GCM',false,['encrypt','decrypt']);
}
export async function saveSecret(env,key,value) {
  if(!value)return; const iv=crypto.getRandomValues(new Uint8Array(12));
  const encrypted=await crypto.subtle.encrypt({name:'AES-GCM',iv,additionalData:enc.encode(key)},await cipherKey(env),enc.encode(value));
  await setSetting(env,`secret:${key}`,`${hex(iv)}:${hex(encrypted)}`);
}
export async function getSecret(env,key) {
  const stored=await getSetting(env,`secret:${key}`); if(!stored)return env[key]||'';
  const [iv,data]=stored.split(':');
  return new TextDecoder().decode(await crypto.subtle.decrypt({name:'AES-GCM',iv:unhex(iv),additionalData:enc.encode(key)},await cipherKey(env),unhex(data)));
}
export async function collectionEnv(env) {
  return {...env,DATA_GO_KR_SERVICE_KEY:await getSecret(env,'DATA_GO_KR_SERVICE_KEY'),LAW_API_OC:await getSecret(env,'LAW_API_OC')};
}
export function sameOrigin(request) {
  const origin=request.headers.get('Origin');
  return origin===new URL(request.url).origin && request.headers.get('Sec-Fetch-Site')!=='cross-site';
}
export async function requestBody(request) {
  if(!request.headers.get('Content-Type')?.includes('application/json'))throw new Error('json_required');
  const reader=request.body?.getReader(); if(!reader)return {};
  let size=0,text='';const decoder=new TextDecoder();
  try {while(true){const {done,value}=await reader.read();if(done)break;size+=value.length;
    if(size>16384)throw new Error('request_too_large');text+=decoder.decode(value,{stream:true});}}
  finally{await reader.cancel();}text+=decoder.decode();return JSON.parse(text||'{}');
}
export const tokenHash=async token=>hex(await crypto.subtle.digest('SHA-256',enc.encode(token)));
const cookieToken=request=>request.headers.get('Cookie')?.match(/(?:^|;\s*)__Host-concost=([a-f0-9]{64})(?:;|$)/)?.[1]||'';
export async function sessionValid(request,env) {
  const token=cookieToken(request); if(!token)return false;
  return Boolean(await env.DB.prepare('SELECT token_hash FROM sessions WHERE token_hash=? AND expires_at>?').bind(await tokenHash(token),Date.now()).first());
}
export async function verifyPassword(env,username,password) {
  const auth=JSON.parse(await getSetting(env,'admin_verifier',env.ADMIN_VERIFIER||'null'));
  if(!auth)throw new Error('admin_not_migrated');
  if(typeof password!=='string'||password.length>256)return false;
  const material=await crypto.subtle.importKey('raw',enc.encode(password),'PBKDF2',false,['deriveBits']);
  const bits=await crypto.subtle.deriveBits({name:'PBKDF2',hash:'SHA-256',salt:unhex(auth.password_salt),iterations:auth.iterations},material,256);
  // Username compared after derivation, no fast unknown-user path.
  return crypto.subtle.timingSafeEqual(bits,unhex(auth.password_hash)) && username===auth.username;
}
const cookie=(value,age)=>`__Host-concost=${value}; Path=/; Max-Age=${age}; HttpOnly; Secure; SameSite=Strict`;
export async function login(request,env) {
  if(!sameOrigin(request))return json({error:'동일 사이트에서 로그인하세요.'},403);
  const bucket=String(Math.floor(Date.now()/3600000));
  // Deliberately one global administrator: at most 10 guesses/hour across IPs.
  const limit=await env.DB.prepare('INSERT INTO login_limits VALUES (?,1) ON CONFLICT(bucket) DO UPDATE SET attempts=attempts+1 WHERE attempts<10').bind(bucket).run();
  if(!limit.meta.changes)return json({error:'로그인 시도 제한입니다. 다음 시간대에 다시 시도하세요.'},429);
  const body=await requestBody(request);
  if(!await verifyPassword(env,body.username,body.password))return json({error:'아이디 또는 비밀번호가 올바르지 않습니다.'},401);
  const token=hex(crypto.getRandomValues(new Uint8Array(32)));
  await env.DB.prepare('INSERT INTO sessions VALUES (?,?)').bind(await tokenHash(token),Date.now()+8*3600000).run();
  return json({ok:true},200,{'Set-Cookie':cookie(token,8*3600)});
}
export async function logout(request,env) {
  const token=cookieToken(request);if(token)await env.DB.prepare('DELETE FROM sessions WHERE token_hash=?').bind(await tokenHash(token)).run();
  return json({ok:true},200,{'Set-Cookie':cookie('',0)});
}
export const validEmail=email=>typeof email==='string'&&email.length<=254&&/^[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+$/.test(email);
export async function importRecipients(env) {
  if(await getSetting(env,'recipients_imported')==='true'||!env.DIGEST_RECIPIENTS)return;
  const addresses=[...new Set(env.DIGEST_RECIPIENTS.split(/[,;\n]+/).map(x=>x.trim().toLowerCase()).filter(Boolean))];
  if(!addresses.length||addresses.length>20||addresses.some(x=>!validEmail(x)))throw new Error('invalid_recipient_configuration');
  // One transaction and a marker: deleted recipients never return on redeploy.
  await env.DB.batch([...addresses.map(email=>env.DB.prepare("INSERT OR IGNORE INTO recipients(email) SELECT ? WHERE NOT EXISTS (SELECT 1 FROM settings WHERE key='recipients_imported' AND value='true')").bind(email)),
    env.DB.prepare("INSERT OR IGNORE INTO settings VALUES ('recipients_imported','true')")]);
}
export async function emailSettings(env) {
  await importRecipients(env);
  const {results:recipients}=await env.DB.prepare('SELECT id,email,name FROM recipients ORDER BY id').all();
  const {results:deliveries}=await env.DB.prepare("SELECT strftime('%Y-%m-%dT%H:%M:%SZ',created_at/1000,'unixepoch') AS started_at,state AS status,(SELECT COUNT(*) FROM deliveries d WHERE d.day=delivery_days.day) AS recipient_count FROM delivery_days ORDER BY day DESC LIMIT 10").all();
  const key=await getSecret(env,'RESEND_API_KEY'),from=await getSetting(env,'from_email',env.DIGEST_FROM_EMAIL||'');
  return {provider_configured:Boolean(key),from_email:from,enabled:await getSetting(env,'digest_enabled','false')==='true',
    schedule_time:'10:00',timezone:'Asia/Seoul',storage_persistent:true,environment_backed:false,
    resend_permanent:Boolean(key),from_permanent:Boolean(from),recipients_permanent:true,recipients,deliveries,
    mail_mode:env.MAIL_MODE,scheduler_active:env.SCHEDULE_ENABLED==='true'};
}
export async function settingsRoute(request,env) {
  const path=new URL(request.url).pathname, method=request.method;
  if(path==='/api/admin/logout'&&method==='POST')return logout(request,env);
  if(path==='/api/admin/settings') {
    if(method==='PUT') {const body=await requestBody(request);
      for(const [field,key]of [['api_key','DATA_GO_KR_SERVICE_KEY'],['law_api_key','LAW_API_OC']]) {
        if(body[field] && (typeof body[field]!=='string'||body[field].length>2048))return json({error:'잘못된 인증값'},400);
        await saveSecret(env,key,body[field]?.trim());
      }
    } else if(method!=='GET')return null;
    return json({api_key_configured:Boolean(await getSecret(env,'DATA_GO_KR_SERVICE_KEY')),law_api_configured:Boolean(await getSecret(env,'LAW_API_OC'))});
  }
  if(path==='/api/admin/email-settings') {
    if(method==='PUT') {
      const body=await requestBody(request),from=body.from_email?.trim();
      if(body.schedule_time && body.schedule_time!=='10:00')return json({error:'예약 시각은 한국시간 10:00 고정입니다.'},400);
      if(from && !validEmail(from.match(/<([^<>]+)>$/)?.[1]||from))return json({error:'발신 주소 형식을 확인하세요.'},400);
      if(body.resend_api_key && (typeof body.resend_api_key!=='string'||!/^re_[\w-]{8,200}$/.test(body.resend_api_key)))return json({error:'Resend 키 형식을 확인하세요.'},400);
      await saveSecret(env,'RESEND_API_KEY',body.resend_api_key);
      if(from)await setSetting(env,'from_email',from);
      if(typeof body.enabled==='boolean')await setSetting(env,'digest_enabled',body.enabled);
    } else if(method!=='GET')return null;
    return json(await emailSettings(env));
  }
  if(path==='/api/admin/recipients'&&method==='POST') {
    const body=await requestBody(request),email=body.email?.trim().toLowerCase();
    if(!validEmail(email)||typeof(body.name||'')!=='string'||(body.name||'').length>100)return json({error:'수신 주소와 이름을 확인하세요.'},400);
    const count=await env.DB.prepare('SELECT COUNT(*) AS n FROM recipients').first();
    if(count.n>=20)return json({error:'무료 시험 주소록은 20명까지입니다.'},400);
    await env.DB.prepare('INSERT INTO recipients (email,name) VALUES (?,?) ON CONFLICT(email) DO UPDATE SET name=excluded.name').bind(email,body.name||'').run();
    return json({ok:true});
  }
  if(/^\/api\/admin\/recipients\/\d+$/.test(path)&&method==='DELETE') {
    await env.DB.prepare('DELETE FROM recipients WHERE id=?').bind(Number(path.split('/').pop())).run();return json({ok:true});
  }
  if(path==='/api/admin/password'&&method==='PUT')return json({error:'시험판에서는 비밀번호 변경을 잠시 비활성화했습니다. 운영 전환 전 강한 비밀번호로 재설정해야 합니다.'},501);
  return null;
}
