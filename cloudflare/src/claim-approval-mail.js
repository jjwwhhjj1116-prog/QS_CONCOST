import {getSetting,getSecret,validEmail,json} from './settings.js';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/iu;
const origins=new Set(['https://concost-claim-center-preview.jjwwhhjj1116.workers.dev','https://concost-claim-center-development.jjwwhhjj1116.workers.dev']);
function validReportLinks(text){
  const report=text.match(/^승인 보고서 확인·출력: (https:\/\/\S+)$/mu)?.[1],review=text.match(/^결재 내용: (https:\/\/\S+)$/mu)?.[1];
  try{const a=new URL(report),b=new URL(review);return origins.has(a.origin)&&a.origin===b.origin&&!a.username&&!a.password&&!b.username&&!b.password&&a.pathname==='/reports/studio'&&a.searchParams.get('step')==='5'&&uuid.test(a.searchParams.get('caseId')??'')&&b.pathname==='/approval'&&uuid.test(b.searchParams.get('reviewId')??'')&&!a.hash&&!b.hash&&[...a.searchParams].length===2&&[...b.searchParams].length===1;}catch{return false;}
}

// Only the named service binding reaches this handler. The public QS HTTP
// handler, scheduler, recipients and delivery history remain separate.
export async function claimApprovalMail(request,env,fetcher=fetch){
  if(request.method!=='POST'||new URL(request.url).origin!=='https://claim-center.internal')return json({code:'APPROVAL_MAIL_ONLY'},400);
  const raw=await request.text();if(raw.length>24000)return json({code:'APPROVAL_MAIL_TOO_LARGE'},413);
  let body;try{body=JSON.parse(raw);}catch{return json({code:'INVALID_APPROVAL_MAIL'},400);}
  if(!body||typeof body!=='object'||Array.isArray(body)||Object.keys(body).some(k=>!['messageType','to','subject','text','idempotencyKey'].includes(k))||body.messageType!=='REPORT_APPROVED_DELIVERY_REQUIRED'||!validEmail(body.to)||!body.to.toLowerCase().endsWith('@con-cost.com')||typeof body.idempotencyKey!=='string'||!uuid.test(body.idempotencyKey)||typeof body.subject!=='string'||!body.subject.startsWith('[클레임센터] ')||body.subject.length>500||/[\r\n]/u.test(body.subject)||typeof body.text!=='string'||body.text.length>20000||!validReportLinks(body.text))return json({code:'INVALID_APPROVAL_MAIL'},400);
  if(env.MAIL_MODE!=='live')return json({code:'APPROVAL_MAIL_NOT_CONFIGURED'},503);
  const from=await getSetting(env,'from_email',env.DIGEST_FROM_EMAIL||''),key=await getSecret(env,'RESEND_API_KEY');
  if(!key||!validEmail(from.match(/<([^<>]+)>$/u)?.[1]||from))return json({code:'APPROVAL_MAIL_NOT_CONFIGURED'},503);
  const response=await fetcher('https://api.resend.com/emails',{method:'POST',redirect:'manual',signal:AbortSignal.timeout(15000),headers:{Authorization:`Bearer ${key}`,'Content-Type':'application/json','Idempotency-Key':`claim-center-${body.idempotencyKey}`},body:JSON.stringify({from,to:[body.to],subject:body.subject,text:body.text})});
  if(!response.ok)return json({code:'APPROVAL_MAIL_PROVIDER_REJECTED'},response.status);
  const receipt=await response.json().catch(()=>null);
  if(!uuid.test(receipt?.id??''))return json({code:'APPROVAL_MAIL_RECEIPT_UNKNOWN'},202);
  return new Response(null,{status:200,headers:{'X-Message-Id':receipt.id,'Cache-Control':'no-store'}});
}
