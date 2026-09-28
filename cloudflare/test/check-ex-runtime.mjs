// Read-only public-source probe in the same workerd runtime as production.
import {Miniflare,convertV4MiniflareOptions} from 'miniflare';
import {build} from 'esbuild';
const {outputFiles}=await build({stdin:{contents:`
import {collectExtraPage} from './src/extra-sources.js';
export default {async fetch(){
  const results=[];
  for(const source_id of ['ex-CT','ex-SV']) {
    try {const r=await collectExtraPage({source_id,next_page:1,start_date:'202609250000',end_date:'202609282359'},{});
      results.push({source_id,candidates:r.candidates,kept:r.rows.length});}
    catch(e){results.push({source_id,error:e.name,message:e.message});}
  }
  return Response.json(results);
}};`,resolveDir:process.cwd()},bundle:true,format:'esm',write:false});
const runtime=new Miniflare(convertV4MiniflareOptions({name:'probe',compatibilityDate:'2026-09-08',modules:[{type:'ESModule',path:'probe.js',contents:outputFiles[0].text}]}));
try {console.log(await (await runtime.dispatchFetch('http://probe')).text());}
finally {await runtime.dispose();}
