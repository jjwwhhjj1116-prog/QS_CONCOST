// Port of tender_radar/email_digest.py: preserve the incumbent email design.
export const website='https://concost-migration-trial.jjwwhhjj1116.workers.dev';
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const link=row=>esc(/^https?:\/\//i.test(row.url||'')?row.url:website+'/#notices');
const score=row=>Math.max(0,Math.min(100,Number(row.score)||0));
function noticeCard(row,isNew){
  const points=score(row),color=points>=70?'#ed5b18':points>=45?'#d58a13':'#16745f';
  const amount=Number(row.estimated_price),price=Number.isFinite(amount)&&amount>0?'예정금액 '+amount.toLocaleString('ko-KR')+'원':'금액 미정';
  return `<tr><td style="padding:0 0 12px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #dfe5e8;border-radius:14px;background:#fff"><tr><td style="padding:18px"><table role="presentation" width="100%"><tr>
    <td width="64" valign="top"><div style="width:54px;height:54px;line-height:54px;text-align:center;border-radius:50%;background:${color};color:#fff;font-size:18px;font-weight:800">${points}</div><div style="font-size:10px;color:#61727b;text-align:center;margin-top:5px">적합도</div></td>
    <td valign="top"><div style="margin-bottom:7px"><span style="display:inline-block;padding:4px 8px;border-radius:20px;background:${isNew?'#fff1e9':'#eef2f4'};color:${isNew?'#b84200':'#53646e'};font-size:11px;font-weight:800">${isNew?'NEW':'기존 알림'}</span> <span style="font-size:11px;color:#61727b">${esc(row.source)} · ${esc(row.notice_type||'신규')}</span></div>
    <a href="${link(row)}" style="font-size:16px;line-height:1.45;color:#102d3f;text-decoration:none;font-weight:800;overflow-wrap:anywhere">${esc(row.title)}</a>
    <div style="font-size:12px;color:#566a75;margin-top:9px;line-height:1.6">${esc(row.institution||'기관 미확인')}<br>등록 ${esc(row.published_at||'날짜 확인 필요')}<br>마감 ${esc(row.deadline_at||'마감일 확인 필요')} · ${price}</div></td>
    </tr></table></td></tr></table></td></tr>`;
}
function newsCard(row){return `<tr><td style="padding:0 0 10px"><a href="${link(row)}" style="display:block;padding:16px 18px;border-left:4px solid #ed5b18;background:#f6f8f9;color:#102d3f;text-decoration:none;border-radius:8px;overflow-wrap:anywhere">
  <span style="font-size:10px;font-weight:800;color:#b84200">NEW · 관련도 ${score(row)}점</span><strong style="display:block;font-size:15px;line-height:1.45;margin:5px 0">${esc(row.title)}</strong>
  <span style="font-size:11px;color:#61727b">${esc(row.source)} · ${esc(row.category)} · ${esc(row.published_at)}</span>
  ${row.summary?`<span style="display:block;font-size:12px;color:#566a75;line-height:1.5;margin-top:7px">${esc(String(row.summary).slice(0,180))}</span>`:''}</a></td></tr>`;}
function section(title,subtitle,cards,empty){return `<tr><td class="section-pad" style="padding:26px 24px 8px"><h2 style="font-size:20px;color:#102d3f;margin:0;font-weight:900">${title}</h2><div style="font-size:12px;color:#61727b;margin-top:5px;line-height:1.6">${subtitle}</div></td></tr>
  <tr><td class="section-pad" style="padding:0 24px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0">${cards.join('')||`<tr><td style="padding:16px;background:#f6f8f9;color:#566a75;font-size:13px;line-height:1.6;border-radius:8px">${empty}</td></tr>`}</table></td></tr>`;}
export function renderEmail({date,newNotices,oldNotices,news,laws}){
  const total=newNotices.length+news.length+laws.length;
  const sections=[
    section('신규 입찰공고','오늘 등록되고 아직 발송 이력이 없는 공고 · 적합도 높은 순',newNotices.map(r=>noticeCard(r,true)),'오늘 날짜로 확인된 신규 발송 대상 공고가 없습니다.'),
    section('기존 알림 프로젝트','발송 이력이 확인된 공고 중 계속 확인할 항목 · 신규 건수와 별도',oldNotices.map(r=>noticeCard(r,false)),'발송 이력이 확인된 진행 중 공고가 없습니다.'),
    section('건설 주요뉴스','공사비·안전진단·재건축·재개발 관련 당일 뉴스',news.map(newsCard),'오늘 날짜로 확인된 신규 뉴스가 없습니다.'),
    section('법규·제도 개정','조달·건설 관련 당일 법령 및 제도 변화',laws.map(newsCard),'오늘 날짜로 확인된 신규 법규·제도 자료가 없습니다.'),
  ].join('');
  return `<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>CONCOST 오늘의 건설 기회 브리핑</title><style>@media(max-width:480px){.outer-pad{padding:12px 4px!important}.section-pad{padding-left:14px!important;padding-right:14px!important}.brief-title{font-size:24px!important}}a:focus-visible{outline:3px solid #297cb2;outline-offset:3px}</style></head>
  <body style="margin:0;background:#edf1f3;font-family:Arial,'Apple SD Gothic Neo','Noto Sans KR',sans-serif;color:#102d3f"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#edf1f3"><tr><td align="center" class="outer-pad" style="padding:28px 10px">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:680px;background:#fff;border-radius:18px;overflow:hidden">
  <tr><td style="padding:26px 28px;background:#20262b;border-top:5px solid #ed5b18"><img src="${website}/concost-logo.png" width="150" alt="CONCOST" style="display:block;background:#fff;border-radius:8px;padding:7px"><div style="color:#ff7a31;font-size:11px;letter-spacing:1.5px;font-weight:800;margin-top:20px">OPPORTUNITY INTELLIGENCE</div><h1 class="brief-title" style="color:#fff;font-size:27px;font-weight:900;line-height:1.3;margin:5px 0 0">오늘의 건설 기회 브리핑</h1><div style="color:#cbd3d7;font-size:13px;margin-top:8px">${esc(date)} · 입찰공고, 건설뉴스, 법규·제도 개정</div></td></tr>
  <tr><td class="section-pad" style="padding:20px 24px 0"><table role="presentation" width="100%" style="background:#fff5ef;border-radius:12px"><tr>${[[newNotices.length,'신규 공고'],[news.length,'건설뉴스'],[laws.length,'법규·제도']].map(([n,label])=>`<td width="33%" style="padding:16px 4px;text-align:center"><strong style="font-size:26px;color:#b84200">${n}</strong><br><span style="font-size:11px;color:#566a75">${label}</span></td>`).join('')}</tr></table></td></tr>
  ${!total?'<tr><td class="section-pad" style="padding:18px 24px 0"><p style="background:#fff5df;padding:14px;color:#70501c;font-size:13px;line-height:1.7;margin:0">당일 신규 발송 대상으로 확인된 자료가 없습니다. 실제 공고가 없다는 뜻은 아니며, 수집 오류나 원문 날짜 미확인 여부를 점검해야 합니다. 이 미리보기만으로 메일이 발송되지는 않습니다.</p></td></tr>':''}
  ${sections}<tr><td align="center" style="padding:30px 24px"><a href="${website}/#notices" style="display:inline-block;background:#ed5b18;color:#fff;text-decoration:none;font-weight:800;padding:14px 26px;border-radius:9px">QS_ConCost 바로가기 →</a><div style="font-size:11px;color:#61727b;line-height:1.6;margin-top:18px">각 제목을 누르면 원문 공고 또는 CONCOST 사이트로 이동합니다.<br>신규 자료와 기존 알림은 구분되며, 과거 뉴스는 다시 포함하지 않습니다.</div></td></tr>
  <tr><td style="background:#102d3f;color:#c4d0d6;padding:18px 24px;font-size:10px;text-align:center">© CONCOST · Construction Cost &amp; Opportunity Intelligence</td></tr></table></td></tr></table></body></html>`;
}
