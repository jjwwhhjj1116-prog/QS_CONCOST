// Shared by the browser and regression tests; counts are processing, not new items.
export function collectionView(job) {
  const sources=job.sources||[], terminal=['succeeded','failed','expired','enqueue_failed'];
  const done=sources.filter(s=>terminal.includes(s.state)).length;
  const failed=sources.filter(s=>terminal.includes(s.state)&&s.state!=='succeeded').length;
  const complete=job.status==='complete', groups={};
  for(const s of sources)groups[s.group||'기타 자료']=(groups[s.group||'기타 자료']||0)+(Number(s.total)||0);
  return {sources,done,failed,complete,groups,percent:sources.length?Math.floor(done/sources.length*100):0,
    title:!complete?'자료를 수집하고 있습니다':!job.ok?'수집 결과를 확인하지 못했습니다':failed?'일부 기관을 제외하고 수집했습니다':'자료 수집을 완료했습니다',
    status:!complete?'수집 중':!job.ok?'확인 실패':failed?'부분 완료':'완료'};
}

function mountCollection(viewOf) {
  const routes={collectNow:'/api/collect',collectNews:'/api/admin/collect-news',collectJiwoncok:'/api/admin/collect-jiwoncok',collectJiwoncokInline:'/api/admin/collect-jiwoncok',refreshJiwoncok:'/api/admin/collect-jiwoncok'};
  const dialog=document.createElement('dialog');dialog.id='cfCollection';dialog.setAttribute('aria-labelledby','cfTitle');
  dialog.innerHTML=`<div class="cf-heading"><img src="/concost-app-icon.png" alt="" width="48" height="48"><button type="button" id="cfClose" aria-label="수집창 닫기">×</button></div>
    <h2 id="cfTitle">자료 수집을 준비하고 있습니다</h2><p id="cfMessage" role="status" aria-live="polite">수집 요청을 확인하고 있습니다.</p>
    <div class="cf-progress"><progress id="cfProgress" max="100" aria-label="수집 작업 처리율"></progress><strong id="cfPercent">준비 중</strong></div>
    <p id="cfTasks"></p><dl id="cfCounts"></dl><p class="cf-note">저장 처리 건수이며 신규 공고 수와는 다릅니다. 기존 자료 갱신·중복 처리가 포함될 수 있습니다.</p>
    <details><summary>기관별 상세 결과</summary><div id="cfSources"></div></details>
    <p id="cfElapsed" class="cf-note">창을 닫아도 서버의 수집은 계속됩니다.</p><div class="cf-actions"><button type="button" id="cfRetry" class="button" hidden>상태 다시 확인</button><button type="button" id="cfList" class="button primary">창 닫고 목록 보기</button></div>`;
  document.body.append(dialog);
  const style=document.createElement('style');style.textContent=`
    #cfCollection{box-sizing:border-box;width:560px;max-width:calc(100vw - 32px);max-height:calc(100dvh - 32px);padding:28px;border:1px solid #dce5e9;border-radius:18px;color:#102b38;background:#fff;overflow:auto}
    #cfCollection::backdrop{background:rgb(6 25 35 / .65)}
    #cfCollection .cf-heading{display:flex;align-items:center;justify-content:space-between}
    #cfCollection #cfClose{width:44px;height:44px;border:0;border-radius:8px;background:#eef3f5;font-size:26px;color:inherit;cursor:pointer}
    #cfCollection h2{font-size:22px;margin:18px 0 10px;line-height:1.4;word-break:keep-all}
    #cfCollection p{line-height:1.6}#cfMessage{min-height:48px;color:#516674}
    #cfCollection .cf-progress{display:flex;align-items:center;gap:16px;margin-top:20px}
    #cfCollection progress{width:100%;height:12px;accent-color:#ff6500}#cfCollection progress[value]{appearance:none;border:0;background:#edf1f3;border-radius:8px;overflow:hidden}#cfCollection progress::-webkit-progress-bar{background:#edf1f3;border-radius:8px}#cfCollection progress::-webkit-progress-value{background:#ff6500;border-radius:8px}#cfCollection progress::-moz-progress-bar{background:#ff6500}#cfPercent{white-space:nowrap;color:#d65000;font-size:22px}
    #cfTasks{font-size:14px}#cfCounts{display:grid;grid-template-columns:1fr auto;gap:9px 20px;padding:16px;background:#f4f7f8;border-radius:10px}#cfCounts:empty{display:none}#cfCounts dd{margin:0;font-weight:700;font-variant-numeric:tabular-nums}
    #cfCollection .cf-note{color:#617381;font-size:12px}#cfCollection summary{cursor:pointer;min-height:44px;display:flex;align-items:center;font-weight:700}#cfCollection summary::before{content:'▸';margin-right:8px}#cfCollection details[open]>summary::before{content:'▾'}
    #cfSources{border-top:1px solid #dce5e9;max-height:260px;overflow:auto}#cfSources p{margin:0;padding:12px 0;border-bottom:1px solid #e8edef;font-size:13px;overflow-wrap:anywhere}#cfSources span{display:block;color:#617381}
    #cfCollection .cf-actions{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:8px}#cfCollection [hidden]{display:none!important}#cfCollection :focus-visible{outline:3px solid #297cb2;outline-offset:3px}
    @media(max-width:480px){#cfCollection{padding:20px}#cfCollection h2{font-size:20px}#cfCollection .cf-actions{flex-direction:column}}
    @media(prefers-reduced-motion:reduce){#cfCollection *{animation:none!important;transition:none!important}}`;
  document.head.append(style);
  const $=id=>dialog.querySelector('#'+id), key='concost.collection.v1';
  const labels=new Map(Object.keys(routes).map(id=>[id,document.getElementById(id)?.textContent]));
  let current=null,busy=false,opener=null;
  function buttons(active){for(const [id,label] of labels){const b=document.getElementById(id);if(b){b.disabled=false;b.textContent=active?'수집 현황 보기':label;}}}
  function save(){try{sessionStorage.setItem(key,JSON.stringify(current));}catch{}}
  function refresh(){Promise.all([window.load?.(),window.loadNews?.(),window.loadIntelligence?.()]).catch(()=>{});}
  function open(button){opener=button||opener;if(!dialog.open)dialog.showModal();}
  function close(){dialog.close();opener?.focus();refresh();}
  $('cfClose').onclick=close;$('cfList').onclick=close;dialog.addEventListener('cancel',e=>{e.preventDefault();close();});
  dialog.addEventListener('keydown',e=>{if(e.key==='Escape')e.stopPropagation();});
  function inline(message,error=false){
    const result=document.getElementById(current?.path.endsWith('jiwoncok')?'jiwoncokResult':'collectResult');if(!result)return;
    result.className='result '+(error?'error':'ok');result.replaceChildren(document.createTextNode(message+' '));
    const b=document.createElement('button');b.type='button';b.className='button';b.textContent='수집 현황 보기';b.onclick=()=>{open(b);if(current)poll();};result.append(b);
  }
  function reason(code){
    if(!code)return '';
    if(/timeout|deadline|timed out/.test(code))return '응답 시간초과 — 이번 수집에서 건너뜀';
    if(/526|certificate/i.test(code))return '원기관 보안 연결 오류';
    if(/network/i.test(code))return '원기관 연결 오류';
    if(/enqueue|queue/i.test(code))return '수집 작업 접수 오류';
    return '오류: '+code;
  }
  function render(job){
    const v=viewOf(job);$('cfTitle').textContent=v.title;$('cfProgress').value=v.percent;$('cfPercent').textContent=v.percent+'%';
    $('cfTasks').textContent='작업 '+v.done+'/'+v.sources.length+'개 처리 완료 · 오류·시간초과 '+v.failed+'개';
    const running=v.sources.filter(s=>s.state==='running').map(s=>s.source);
    $('cfMessage').textContent=v.complete?(!job.ok?'수집 결과를 확인하지 못했습니다. 기관별 오류를 확인해 주세요.':v.failed?'성공한 기관의 자료는 저장했습니다. 오류 기관은 아래에서 확인할 수 있습니다.':'저장된 자료를 목록에서 확인할 수 있습니다.'):(running.slice(0,2).join(' · ')||'기관 응답을 기다리고 있습니다.')+(running.length>2?' 외 '+(running.length-2)+'개 작업 진행 중':'');
    $('cfCounts').replaceChildren();for(const [name,count] of Object.entries(v.groups)){const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=name;dd.textContent=count.toLocaleString()+'건';$('cfCounts').append(dt,dd);}
    $('cfSources').replaceChildren();const states={queued:'대기',running:'수집 중',succeeded:'완료',failed:'실패',expired:'시간초과',enqueue_failed:'접수 실패'};
    for(const s of v.sources){const p=document.createElement('p'),strong=document.createElement('strong'),span=document.createElement('span');strong.textContent=s.source+' · '+(states[s.state]||s.state);span.textContent='후보 '+s.candidates+'건 / 저장 처리 '+s.total+'건'+(s.error?' · '+reason(s.error):'');p.append(strong,span);$('cfSources').append(p);}
    $('cfElapsed').textContent='확인 경과 '+Math.max(0,Math.floor((Date.now()-current.started)/1000))+'초 · 창을 닫아도 서버 수집은 계속됩니다.';
    inline(v.status+' · 작업 '+v.done+'/'+v.sources.length+'개 · 오류·시간초과 '+v.failed+'개',v.complete&&!job.ok);
    if(v.complete){current.complete=true;save();}
  }
  async function read(path,options={}){const r=await fetch(path,{...options,signal:AbortSignal.timeout(15000)});let data;try{data=await r.json();}catch{throw Error('서버 응답을 확인할 수 없습니다 (HTTP '+r.status+').');}if(!r.ok)throw Error(r.status===401?'관리자 세션이 만료되었습니다. 다시 로그인해 주세요.':data.error||'상태 확인 실패 (HTTP '+r.status+')');return data;}
  async function poll(){
    if(busy||!current?.job_id)return;busy=true;buttons(true);$('cfRetry').hidden=true;const observed=Date.now();
    try{while(true){const job=await read('/api/collect/status/'+encodeURIComponent(current.job_id));render(job);if(job.status==='complete')break;
      if(Date.now()-observed>=300000){$('cfMessage').textContent='5분간 상태를 확인했습니다. 전체 완료 여부는 아직 확인되지 않았습니다. 저장된 자료는 목록에서 볼 수 있습니다.';$('cfRetry').hidden=false;inline('전체 완료 미확인 · 저장된 자료는 표시됩니다.');break;}
      await new Promise(resolve=>setTimeout(resolve,3000));
    }}catch(e){$('cfTitle').textContent='수집 상태를 다시 확인해 주세요';$('cfMessage').textContent=e.message+' 수집 실패나 0건으로 확정된 것은 아닙니다.';$('cfRetry').hidden=false;inline('상태 확인 지연 · 수집 현황에서 다시 확인해 주세요.',true);}
    finally{busy=false;buttons(!current.complete);refresh();}
  }
  $('cfRetry').onclick=()=>poll();
  document.addEventListener('click',async event=>{
    const button=event.target.closest('button'),path=routes[button?.id];if(!path)return;
    event.preventDefault();event.stopImmediatePropagation();open(button);
    if(busy)return;if(current&&!current.complete){poll();return;}
    current=null;busy=true;buttons(true);$('cfTitle').textContent='자료 수집을 준비하고 있습니다';$('cfMessage').textContent='수집 요청을 확인하고 있습니다.';$('cfProgress').removeAttribute('value');$('cfPercent').textContent='준비 중';$('cfTasks').textContent='';$('cfCounts').replaceChildren();$('cfSources').replaceChildren();$('cfRetry').hidden=true;dialog.querySelector('details').open=false;
    try{const start=await read(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({lookback_hours:Number(document.getElementById('lookback').value)})});current={job_id:start.job_id,path,started:Date.now(),complete:false};save();busy=false;poll();}
    catch(e){busy=false;buttons(false);$('cfTitle').textContent='수집 요청을 확인하지 못했습니다';$('cfMessage').textContent=e.message;inline('수집 요청 확인 실패',true);}
  },true);
  try{const saved=JSON.parse(sessionStorage.getItem(key)||'null');if(saved?.job_id&&Object.values(routes).includes(saved.path)&&Date.now()-saved.started<86400000){current=saved;inline(saved.complete?'이전 수집 결과를 확인할 수 있습니다.':'진행 중인 수집 상태를 확인합니다.');if(!saved.complete)poll();}}catch{}
}

export const trialUI=`(${mountCollection.toString()})(${collectionView.toString()});`;
