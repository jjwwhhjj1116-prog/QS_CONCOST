// Read-only live probe. Credentials arrive through stdin, never files or logs.
import {Miniflare,convertV4MiniflareOptions} from 'miniflare';
import {build} from 'esbuild';
let input='';for await(const chunk of process.stdin)input+=chunk;
const credentials=JSON.parse(input);
if(!credentials.DATA_GO_KR_SERVICE_KEY)throw new Error('missing_api_key');
const end=new Date(),start=new Date(end.getTime()-7*86400000);
const day=date=>new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Seoul'}).format(date).replaceAll('-','');
const {outputFiles}=await build({stdin:{contents:`
import {collectExtraPage} from './src/extra-sources.js';
export default {async fetch(request){
  const env=await request.json(),results=[];
  for(const source_id of ['lh','jiwon-metro-service','jiwon-metro-construction']) {
    try {let candidates=0,total=0,more=true,pages=0;const rows=[];
      while(more&&pages<10){const r=await collectExtraPage({source_id,next_page:++pages,start_date:'${day(start)}0000',end_date:'${day(end)}2359'},env);
        candidates+=r.candidates;total=r.total;more=r.more;rows.push(...r.rows);}
      results.push({source_id,total,candidates,kept:rows.length,more,pages,
        rows:rows.map(({source,title,published_at,deadline_at,score})=>({source,title,published_at,deadline_at,score}))});}
    catch(e){results.push({source_id,error:e.name,message:e.message});}
  }
  return Response.json(results);
}};`,resolveDir:process.cwd()},bundle:true,format:'esm',write:false});
const runtime=new Miniflare(convertV4MiniflareOptions({name:'probe',compatibilityDate:'2026-09-08',modules:[{type:'ESModule',path:'probe.js',contents:outputFiles[0].text}]}));
try {console.log(await(await runtime.dispatchFetch('http://probe',{method:'POST',body:JSON.stringify(credentials)})).text());}
finally {await runtime.dispose();}
