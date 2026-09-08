// Keep the existing page and forms. Replace only synchronous collection handlers.
export const trialUI = `
document.addEventListener('click', async function(event) {
  const button=event.target.closest('button'); if(!button)return;
  const routes={collectNow:'/api/collect',collectNews:'/api/admin/collect-news',collectJiwoncok:'/api/admin/collect-jiwoncok',refreshJiwoncok:'/api/admin/collect-jiwoncok'};
  // Both source-board buttons have changed IDs between production versions.
  const path=routes[button.id] || (/지원COK.*갱신/.test(button.textContent)?'/api/admin/collect-jiwoncok':null);
  if(!path)return;
  event.preventDefault();event.stopImmediatePropagation();
  const result=document.getElementById(path.endsWith('jiwoncok')?'jiwoncokResult':'collectResult');
  const label=button.textContent;button.disabled=true;button.textContent='수집 상태 확인 중…';
  const show=(text,error=false)=>{result.className='result '+(error?'error':'ok');result.textContent=text;};
  try {
    const response=await fetch(path,{method:'POST',signal:AbortSignal.timeout(20000),headers:{'Content-Type':'application/json'},body:JSON.stringify({lookback_hours:Number(document.getElementById('lookback').value)})});
    const start=await response.json();if(!response.ok)throw Error(start.error||'수집 요청 실패');
    const deadline=Math.min(start.deadline||Date.now()+300000,Date.now()+300000);
    while(true) {
      const statusResponse=await fetch('/api/collect/status/'+encodeURIComponent(start.job_id),{signal:AbortSignal.timeout(15000)});
      const job=await statusResponse.json();if(!statusResponse.ok)throw Error(job.error||'상태 확인 실패');
      const progress=job.sources.map(s=>s.source+': '+s.state+' (후보 '+s.candidates+' / 적합 '+s.total+')'+(s.error?' '+s.error:''));
      show((job.status==='complete'?(job.partial?'부분 수집 완료':'수집 완료'):'수집 중')+' · 적합 처리 '+job.total+'건 / '+progress.join(' | '),job.status==='complete'&&!job.ok);
      if(job.status==='complete')break;
      if(Date.now()>=deadline){show('5분 상태 확인 종료. 전체 완료는 미확인입니다. 저장된 자료는 표시되며 기관별 상태를 다시 확인할 수 있습니다.');break;}
      await new Promise(resolve=>setTimeout(resolve,3000));
    }
  } catch(error) {show('수집 상태 확인 실패: '+error.message+' — 0건으로 확인된 것은 아닙니다.',true);}
  finally {button.disabled=false;button.textContent=label;window.dispatchEvent(new HashChangeEvent('hashchange'));}
},true);
`;
