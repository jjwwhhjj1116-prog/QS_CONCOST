import { scoreNotice, shouldKeep } from './scoring.js';
import { extract, PAGE_SIZE } from './bid-api.js';
import { dateOnly } from './logic.js';
import {OTHER_BIDS,collectOtherBid} from './other-bids.js';

// Each entry is one independently timed Queue task. No homepage discovery crawl.
export const BOARDS = {
  'news-molit': ['국토교통부 보도자료', 'https://www.molit.go.kr/USR/NEWS/m_71/lst.jsp?lcmspage=1', 'molit'],
  'news-cerik-issue': ['CERIK 이슈포커스', 'https://www.cerik.re.kr/report/issue', 'cerik'],
  'news-cerik-brief': ['CERIK 동향브리핑', 'https://www.cerik.re.kr/report/briefing', 'cerik'],
  'news-ricon': ['RICON 건설시장', 'https://www.ricon.re.kr/board/list.php?cate=7&group=issue&page=market_issue', 'ricon'],
  'news-ricon-brief': ['RICON 건설브리프', 'https://www.ricon.re.kr/board/list.php?cate=8&group=issue&page=ricon_brief', 'ricon'],
  'jiwon-busan-rebuild': ['부산광역시 정비사업 통합홈페이지', 'https://dynamice.busan.go.kr/view.do?no=287', 'busan'],
  'jiwon-metro': ['서울교통공사', 'https://www.seoulmetro.co.kr/kr/board.do?menuIdx=546', 'agency'],
  'jiwon-gh': ['경기주택도시공사', 'https://www.gh.or.kr/gh/bid-relations.do?article.offset=0&mode=list', 'agency'],
  'jiwon-guro': ['구로구', 'https://www.guro.go.kr/www/selectBbsNttList.do?bbsNo=663&key=1791&pageIndex=1&pageUnit=20', 'agency'],
  'jiwon-ddm': ['서울특별시 동대문구', 'https://www.ddm.go.kr/www/selectEminwonWebList.do?key=3291&pageIndex=1&pageUnit=20&searchCnd=all', 'agency'],
};
for (const [label, key, category] of [
  ['조달청 보도자료','00634','건설 주요뉴스'], ['조달청 훈령','00029','법규·제도 개정'],
  ['조달청 고시','00030','법규·제도 개정'], ['조달청 행정예고','01265','법규·제도 개정'],
  ['조달청 시설공사 자료','00036','법규·제도 개정'],
]) BOARDS[`news-pps-${key}`] = [label, `https://www.pps.go.kr/kor/bbs/list.do?key=${key}`, 'pps', category];
export const LAW_QUERIES = ['건설','건축','주택','도시정비','시설물','국가계약','지방계약'];
const RELEVANT = /건설|건축|토목|주택|도시|정비|재건축|재개발|안전진단|시설물|공사비|원가|도로|설계|감리|BIM|입찰|계약|조달/i;
const EXCLUDE = /채용|부고|결혼|봉사활동|장학금|성희롱|견본주택 개관/;
const clean = text => String(text || '').replace(/<[^>]*>/g,' ').replace(/&(?:nbsp|amp|lt|gt|quot|#39);/g,
  v => ({'&nbsp;':' ','&amp;':'&','&lt;':'<','&gt;':'>','&quot;':'"','&#39;':"'"})[v]).replace(/\s+/g,' ').trim();
const attr = (text, name) => clean(text.match(new RegExp(`${name}\\s*=\\s*["']([^"']+)["']`, 'i'))?.[1]);
const pick = (row,...keys) => keys.map(k=>row[k]).find(x=>x !== '' && x != null) ?? '';
const money = v => v === '' || v == null || !Number.isFinite(Number(String(v).replaceAll(',',''))) ? null : Number(String(v).replaceAll(',',''));
export async function requestText(url, fetcher = fetch, init={}) {
  let response;
  try { response = await fetcher(url, {...init,signal:AbortSignal.timeout(15000),headers:{'User-Agent':'CONCOST-Radar/1.0',...init.headers}}); }
  catch { throw new Error('upstream_timeout_or_network'); }
  if (!response.ok) throw new Error(`upstream_http_${response.status}`);
  const reader = response.body.getReader(); let size=0; const chunks=[];
  try { while (true) { const {done,value}=await reader.read(); if(done)break;
    size+=value.length; if(size>1500000)throw new Error('upstream_response_too_large'); chunks.push(value); }
  } finally { await reader.cancel(); }
  const bytes=new Uint8Array(size); let offset=0; for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length;}
  const charset=response.headers.get('content-type')?.match(/charset=([\w-]+)/i)?.[1] || 'utf-8';
  return new TextDecoder(charset).decode(bytes).replace(/^\uFEFF/,'');
}
const safeLink = (href, base) => { try { const u=new URL(href,base); return ['http:','https:'].includes(u.protocol) ? u.href : ''; } catch { return ''; } };
function article(source,key,title,date,url,category='건설 주요뉴스') {
  title=clean(title); if(!key || !title || !url) return null;
  return {kind:'news',source,source_key:key,id:`${source}-${key}`,title,summary:'',category,
    published_at:dateOnly(date),url,...scoreNotice(title,source)};
}
export function parseBoard(page, sourceId) {
  const [source,base,parser,category]=BOARDS[sourceId]; const rows=[]; let candidates=0;
  function add(key,title,date,href,authority=false) {
    candidates++; const item=article(source,key,title,date,safeLink(href,base),category);
    if(item && !EXCLUDE.test(item.title) && (authority || RELEVANT.test(item.title))) rows.push(item);
  }
  if(parser==='cerik') {
    for(const block of page.split(/<div\s+class="document-preview-slide-wrap">/i).slice(1)) {
      const href=block.match(/href="(\/(?:report|material)\/[^"?#]+\/\d+)"/i)?.[1];
      const title=block.match(/<div class="title">([\s\S]*?)<\/div>/i)?.[1];
      if(href && title)add(href,title,clean(block.match(/<b>\s*출판일\s*<\/b>\s*<span>([^<]+)/i)?.[1]),href,true);
    }
  } else if(parser==='busan') {
    for(const m of page.matchAll(/<a\b([^>]*)>([\s\S]*?)<\/a>/gi)) {
      const args=m[1].match(/move_view\(\s*'(\d+)'\s*,\s*'BBSMSTR_000000000080'\s*,\s*'([^']+)'/);
      const title=clean(m[2].match(/<h3[^>]*>([\s\S]*?)<\/h3>/i)?.[1]);
      if(!args || !title)continue; candidates++;
      rows.push({kind:'notice',source:'지원COK',source_key:`${source}-${args[1]}`,title,institution:source,
        category:/평가위원/.test(title)?'평가위원 모집':'용역',published_at:dateOnly(clean(m[2]).match(/등록일\s*:\s*(20\d{2}-\d{2}-\d{2})/)?.[1]),
        region:'부산',url:new URL(`/home/${args[2]}/view.do?no=329&pgMode=view&ntt_id=${args[1]}`,base).href,...scoreNotice(title,source)});
    }
  } else {
    for(const match of page.matchAll(/<tr\b[^>]*>([\s\S]*?)<\/tr>/gi)) {
      const block=match[1], date=dateOnly(clean(block));
      if(parser==='molit') {
        const m=block.match(/href="([^"]*dtl\.jsp\?[^"]*id=(\d+)[^"]*)"[^>]*>([\s\S]*?)<\/a>/i);
        if(m)add(m[2],m[3],date,clean(m[1]));
      } else if(parser==='pps') {
        const m=block.match(/goView\('([^']+)'[^)]*\)[^>]*>([\s\S]*?)<\/a>/i);
        if(m)add(m[1],m[2],date,`https://www.pps.go.kr/kor/bbs/view.do?bbsSn=${m[1]}&key=${new URL(base).searchParams.get('key')}`);
      } else if(parser==='ricon') {
        const href=block.match(/href="([^"]*\/board\/view\.php\?[^"#]+)"/i)?.[1];
        const title=block.match(/<strong class="bo_sbj">([\s\S]*?)<\/strong>/i)?.[1];
        if(href && title)add(clean(href),title,clean(block.match(/<td class="col_date">\s*([^<]+)/i)?.[1]),clean(href),true);
      } else {
        for(const a of block.matchAll(/<a\b([^>]*)>([\s\S]*?)<\/a>/gi)) {
          const href=attr(a[1],'href'),title=clean(a[2]);
          if(!href || /^(#|javascript:)/i.test(href) || title.length<8)continue;
          const link=safeLink(href,base); if(!link || new URL(link).origin !== new URL(base).origin)continue;
          candidates++; if(!RELEVANT.test(title) || EXCLUDE.test(title))continue;
          rows.push({kind:'notice',source:'지원COK',source_key:link,title,institution:source,category:/평가위원/.test(title)?'평가위원 모집':'용역',
            published_at:date,region:source.startsWith('서울')||source==='구로구'?'서울':'',url:link,...scoreNotice(title,source)});
        }
      }
    }
  }
  // A changed template/access-denied page is NOT evidence of no notices.
  if(!candidates && !/등록된\s*(?:게시물|자료|공고).*없|검색된\s*(?:자료|결과).*없/.test(clean(page))) throw new Error('upstream_board_structure_unknown');
  const dedup=new Map();
  for(const row of rows) {
    if(row.kind==='notice') {
      Object.assign(row,{id:`${sourceId}-${row.source_key}`,deadline_at:'',estimated_price:null,notice_type:'신규',workflow_status:'new',matched_keywords:row.matched_keywords||[]});
      if(!shouldKeep(row))continue;
    }
    dedup.set(`${row.source}:${row.source_key}`,row);
  }
  // Index snapshots only; do not claim a full board archive traversal.
  return {rows:[...dedup.values()],candidates,total:candidates,filtered:candidates-dedup.size,more:false};
}

export const INTELLIGENCE = {};
for(const [suffix,category] of [['Cnstwk','공사'],['Servc','용역']]) {
  INTELLIGENCE[`plan-${suffix}`]=['pipeline','발주계획',category,`ao/OrderPlanSttusService/getOrderPlanSttusList${suffix}`];
  INTELLIGENCE[`spec-${suffix}`]=['pipeline','사전규격',category,`ao/HrcspSsstndrdInfoService/getPublicPrcureThngInfo${suffix}`];
  INTELLIGENCE[`award-${suffix}`]=['cost','낙찰',category,`as/ScsbidInfoService/getScsbidListSttus${suffix}`];
  INTELLIGENCE[`contract-${suffix}`]=['cost','계약',category,`ao/CntrctInfoService/getCntrctInfoList${suffix}`];
}
for(const [suffix,category] of [['Cnstwk','공사'],['GnrlServc','일반용역'],['TechServc','기술용역']])
  INTELLIGENCE[`request-${suffix}`]=['pipeline','조달요청',category,`ao/PrcrmntReqInfoService/getPrcrmntReqInfoList${suffix}`];
export function normalizeIntelligence(row,id) {
  const [kind,type,category]=INTELLIGENCE[id]; let key,title,institution,published,amount,planned='',url='';
  if(type==='발주계획') {key=pick(row,'orderPlanUntyNo','orderPlanSno','cnstwkMngNo');title=pick(row,'bizNm','cnstwkPrdCntnts','specCntnts');institution=pick(row,'orderInsttNm','totlmngInsttNm');published=pick(row,'nticeDt','chgDt');amount=pick(row,'sumOrderAmt','orderContrctAmt');planned=`${row.orderYear||''}-${String(row.orderMnth||'').padStart(2,'0')}`;}
  else if(type==='사전규격') {key=pick(row,'bfSpecRgstNo','refNo');title=pick(row,'prdctClsfcNoNm','prdctDtlList','refNo');institution=pick(row,'orderInsttNm','rlDminsttNm');published=pick(row,'rgstDt','rcptDt','chgDt');planned=pick(row,'opninRgstClseDt','dlvrTmlmtDt');amount=row.asignBdgtAmt;url=pick(row,'specDocFileUrl1');}
  else if(type==='조달요청') {key=pick(row,'prcrmntReqNo','frstyearPrcrmntReqNo');title=pick(row,'prcrmntReqNm','cnsttyNm');institution=pick(row,'orderInsttNm','rcptBrnofceNm');published=pick(row,'inptDt','rcptDt');amount=pick(row,'presmptPrce','totSrvceBdgtAmt','totCnstwkScleAmt','bdgtAmt','thtmBdgtAmt','contrctAmt');planned=pick(row,'techRvwReqstDate','rprsntDedtDate');url=pick(row,'prcrmntReqInfoUrl');}
  else if(type==='낙찰') {key=`${row.bidNtceNo||''}-${row.bidNtceOrd||'000'}`;title=row.bidNtceNm;institution=row.dminsttNm;published=pick(row,'fnlSucsfDate','rgstDt','rlOpengDt');amount=row.sucsfbidAmt;}
  else {key=pick(row,'untyCntrctNo','dcsnCntrctNo','cntrctRefNo');title=pick(row,'cntrctNm','cnstwkNm');institution=row.cntrctInsttNm;published=pick(row,'cntrctDate','cntrctCnclsDate','rgstDt');amount=pick(row,'totCntrctAmt','thtmCntrctAmt');url=pick(row,'cntrctInfoUrl','cntrctDtlInfoUrl');}
  if(!key || !title)throw new Error('upstream_missing_record_identity');
  const region=String(pick(row,'cnstwkRgnNm','cnstrtsiteRgnNm','bidwinnrAdrs','cntrctInsttJrsdctnDivNm'));
  return {kind,source:'나라장터',source_key:String(key),id:`${id}-${key}`,title:String(title),institution:institution||'',region,
    category,published_at:dateOnly(published),recorded_at:dateOnly(published),planned_at:planned,stage:kind==='pipeline'?type:undefined,
    record_type:kind==='cost'?type:undefined,amount:money(amount),award_amount:type==='낙찰'?money(amount):null,
    contract_amount:type==='계약'?money(amount):null,award_rate:money(row.sucsfbidRate),notice_no:pick(row,'bidNtceNo','ntceNo'),
    company:String(pick(row,'bidwinnrNm','fnlSucsfCorpOfcl','corpList')).slice(0,500),url:url?safeLink(url,'https://www.g2b.go.kr'):'',
    ...scoreNotice(title,institution,region,category,type)};
}
export function extraJobs(now,lookback,scope='all') {
  const fmt=ms=>new Date(ms+9*3600000).toISOString().replace(/[-:T]/g,'').slice(0,12);
  let ids=Object.keys(BOARDS);
  if(scope==='jiwon')ids=ids.filter(x=>x.startsWith('jiwon-'));
  else if(scope==='news')ids=ids.filter(x=>x.startsWith('news-'));
  else ids.push(...Object.keys(INTELLIGENCE),...Object.keys(OTHER_BIDS));
  if(scope!=='jiwon')ids.push(...LAW_QUERIES.map((_,i)=>`law-${i}`));
  return ids.map(source_id=>({source_id,label:BOARDS[source_id]?.[0]||OTHER_BIDS[source_id]||INTELLIGENCE[source_id]?.slice(1,3).join(' ')||`국가법령 ${LAW_QUERIES[Number(source_id.slice(4))]}`,
    start_date:fmt(now-lookback*3600000),end_date:fmt(now)}));
}
export async function collectExtraPage(job,env,fetcher=fetch) {
  const id=job.source_id;
  if(OTHER_BIDS[id])return collectOtherBid(job,env,fetcher,requestText);
  if(BOARDS[id])return parseBoard(await requestText(BOARDS[id][1],fetcher),id);
  let url,items,total,normalize;
  if(id.startsWith('law-')) {
    if(!env.LAW_API_OC)throw new Error('missing_law_api_key');
    url=new URL('https://www.law.go.kr/DRF/lawSearch.do');
    url.search=new URLSearchParams({OC:env.LAW_API_OC,target:'law',type:'JSON',display:PAGE_SIZE,page:job.next_page,sort:'ddes',query:LAW_QUERIES[Number(id.slice(4))],ancYd:`${job.start_date.slice(0,8)}~${job.end_date.slice(0,8)}`});
    const root=JSON.parse(await requestText(url,fetcher)).LawSearch;
    if(!root || root.totalCnt == null)throw new Error('upstream_law_schema_unknown');
    items=root.law||[];if(!Array.isArray(items))items=[items];total=Number(root.totalCnt);
    normalize=row=>article('국가법령정보센터',String(pick(row,'법령일련번호','법령ID')),pick(row,'법령명한글','법령명'),row['공포일자'],safeLink(row['법령상세링크'],'https://www.law.go.kr'),'법규·제도 개정');
  } else {
    const def=INTELLIGENCE[id];if(!def)throw new Error('unknown_source');
    if(!env.DATA_GO_KR_SERVICE_KEY)throw new Error('missing_api_key');
    url=new URL(`https://apis.data.go.kr/1230000/${def[3]}`);
    const dates=id.startsWith('plan-')?{orderBgnYm:job.start_date.slice(0,6),orderEndYm:job.end_date.slice(0,6)}:{inqryBgnDt:job.start_date,inqryEndDt:job.end_date};
    url.search=new URLSearchParams({serviceKey:env.DATA_GO_KR_SERVICE_KEY,type:'json',inqryDiv:'1',pageNo:job.next_page,numOfRows:PAGE_SIZE,...dates});
    ({items,total}=extract(JSON.parse(await requestText(url,fetcher)))); normalize=row=>normalizeIntelligence(row,id);
  }
  if(!Number.isInteger(total)||total<0||items.length>PAGE_SIZE)throw new Error('upstream_invalid_pagination');
  const offset=(job.next_page-1)*PAGE_SIZE;
  if(!items.length && total>offset)throw new Error('upstream_missing_page');
  const rows=items.map(normalize).filter(row=>row && (row.kind==='news'||shouldKeep(row)));
  return {rows,total,candidates:items.length,filtered:items.length-rows.length,more:offset+items.length<total};
}
