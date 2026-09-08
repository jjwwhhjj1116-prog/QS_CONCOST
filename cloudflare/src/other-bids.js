import {scoreNotice,shouldKeep} from './scoring.js';
import {dateOnly} from './logic.js';
import {extract,PAGE_SIZE} from './bid-api.js';
// Source mappings mirror tender_radar/{lh,kwater,apartment_api,expressway}.py.
export const OTHER_BIDS={'lh':'LH 입찰','kwater-cntrwkList':'K-water 공사','kwater-servcList':'K-water 용역','kapt-api':'공동주택 입찰 API','ex-CT':'도로공사 공사','ex-SV':'도로공사 용역'};
const str=(v)=>String(v??'');
const money=v=>v==null||v===''||!Number.isFinite(Number(str(v).replaceAll(',','')))?null:Number(str(v).replaceAll(',',''));
function normalized(row,id) {
  let source,key,title,institution,category,published,deadline,amount,region='',url='',notice_type='신규';
  if(id==='lh') {
    source='LH';key=row.bidNum;title=row.bidnmKor;institution=row.zoneHqCd||'한국토지주택공사';category=str(row.cstrtnJobGbNm).includes('용역')?'용역':'공사';published=row.tndrbidRegDt;deadline=row.tndrdocAcptEndDtm;amount=row.presmtPrc;
    region=[1,2,3,4].map(i=>row['zoneRstrct'+i]||'').filter(Boolean).join(' · ');url='https://ebid.lh.or.kr/ebid.et.tp.cmd.BidMasterListCmd.dev';
    if(/취소/.test(row.bidKind))notice_type='취소';else if(/정정|변경/.test(row.bidKind))notice_type='개정';else if(/재공고/.test(row.bidKind))notice_type='재공고';
  } else if(id.startsWith('kwater-')) {
    source='K-water';key=row.tndrPbanno;title=row.tndrPblancNm;institution=row.cntrctDeptNm||'한국수자원공사';category=id.endsWith('servcList')?'용역':'공사';published=row.tndrPblancDe;deadline=row.tndrPblancEnddt;amount=row.tndrPlnprc;url='https://ebid.kwater.or.kr/';
  } else if(id==='kapt-api') {
    source='공동주택관리정보시스템';key=row.bidNum;title=row.bidTitle;institution=row.bidKaptname;category=/공사|도장|방수|교체|설치/.test(title)?'공사':'용역';published=row.bidRegDate||row.bidRegdate;deadline=row.bidDeadline;region=str(row.bidArea);url='https://www.k-apt.go.kr/bid/bidDetail.do?'+new URLSearchParams({bidNum:key});notice_type={'2':'개정','3':'재공고'}[row.bidState]||'신규';
  } else {
    source='도로공사';key=`${row.noti_no}-${row.bid_rev||1}`;title=row.noti_nm;institution='한국도로공사';category=id==='ex-SV'?'용역':'공사';published=row.noti_date;
    if(Number(row.bid_rev||1)>1)notice_type='개정';
    url='https://ebid.ex.co.kr/default.do?'+new URLSearchParams({menuId:category==='공사'?'NPRO11001':'NPRO12001',portal_yn:'Y',noti_cont_id:row.noti_cont_id||'',noti_id:row.noti_id||'',noti_no:row.noti_no||'',bid_no:row.bid_no||'',bid_rev:row.bid_rev||1});
  }
  if(!key||!title)throw new Error('upstream_missing_notice_identity');
  return {kind:'notice',source,source_key:str(key),id:`${id}-${key}`,title:str(title),institution:str(institution),category,
    published_at:dateOnly(published),deadline_at:dateOnly(deadline),estimated_price:money(amount),region,url,notice_type,
    workflow_status:'new',change_reason:'',changed_at:'',...scoreNotice(title,institution,region,category)};
}
export function xmlResponse(text) {
  const value=(tag,s=text)=>s.match(new RegExp(`<${tag}(?:\\s[^>]*)?>([\\s\\S]*?)<\\/${tag}>`,'i'))?.[1]?.trim()||'';
  const code=value('resultCode');
  if(code==='03'&&value('resultMsg')==='NODATA_ERROR')return {items:[],total:0};
  if(!['00','0'].includes(code))throw new Error('upstream_api_error');
  const total=Number(value('totalCount'));if(!value('totalCount')||!Number.isInteger(total))throw new Error('upstream_invalid_xml');
  const items=[...text.matchAll(/<item>([\s\S]*?)<\/item>/gi)].map(m=>Object.fromEntries([...m[1].matchAll(/<([\w]+)>([\s\S]*?)<\/\1>/g)].map(x=>[x[1],x[2].replace(/^<!\[CDATA\[|\]\]>$/g,'').trim()])));
  return {items,total};
}
export async function collectOtherBid(job,env,fetcher,requestText) {
  const id=job.source_id;let url,items,total;
  if(id.startsWith('ex-')) {
    const home=await fetcher('https://ebid.ex.co.kr/default.do',{signal:AbortSignal.timeout(15000),headers:{'User-Agent':'CONCOST/1.0'}});
    if(!home.ok)throw new Error(`upstream_http_${home.status}`);
    const html=await home.text();if(html.length>1500000)throw new Error('upstream_response_too_large');
    const csrf=html.match(/name="_csrf" content="([^"]+)"/)?.[1];if(!csrf)throw new Error('upstream_csrf_missing');
    const cookie=home.headers.getSetCookie().map(x=>x.split(';')[0]).join('; ');
    const text=await requestText('https://ebid.ex.co.kr/findPagingPortalBidNotiList.do',fetcher,{method:'POST',headers:{Cookie:cookie,'Content-Type':'application/json;charset=UTF-8','X-CSRF-TOKEN':csrf,menucode:'HOME'},body:JSON.stringify({noti_cls:id.slice(3)})});
    items=JSON.parse(text).result_list;if(!Array.isArray(items))throw new Error('upstream_invalid_list');total=items.length;
  } else {
    if(!env.DATA_GO_KR_SERVICE_KEY)throw new Error('missing_api_key');
    const params={serviceKey:env.DATA_GO_KR_SERVICE_KEY,pageNo:job.next_page,numOfRows:PAGE_SIZE,_type:'json'};
    if(id==='lh') {url=new URL('https://openapi.ebid.lh.or.kr/ebid.com.openapi.service.OpenBidInfoList.dev');Object.assign(params,{tndrbidRegDtStart:job.start_date.slice(0,8),tndrbidRegDtEnd:job.end_date.slice(0,8)});}
    else if(id.startsWith('kwater-')) {url=new URL('https://apis.data.go.kr/B500001/ebid/tndr3/'+id.slice(7));params.searchDt=job.start_date.slice(0,6);}
    else {url=new URL('https://apis.data.go.kr/1613000/ApHusBidPblAncInfoOfferServiceV2/getPblAncDeSearchV2');Object.assign(params,{startDate:job.start_date.slice(0,8),endDate:job.end_date.slice(0,8)});}
    url.search=new URLSearchParams(params);
    const text=await requestText(url,fetcher);
    ({items,total}=id==='lh'?xmlResponse(text):extract(JSON.parse(text)));
    if(items.length>PAGE_SIZE)throw new Error('upstream_ignored_page_size');
  }
  const offset=(job.next_page-1)*PAGE_SIZE;
  if(!items.length&&total>offset)throw new Error('upstream_missing_page');
  const start=dateOnly(job.start_date),end=dateOnly(job.end_date);
  const all=items.map(row=>normalized(row,id));
  if(id==='kapt-api'&&all.length&&all.every(row=>row.published_at && (row.published_at<start||row.published_at>end)))throw new Error('upstream_ignored_date_filter');
  const rows=all.filter(row=>shouldKeep(row)&&(!row.published_at||(row.published_at>=start&&row.published_at<=end)));
  return {rows,total,candidates:items.length,filtered:items.length-rows.length,more:!id.startsWith('ex-')&&offset+items.length<total};
}
