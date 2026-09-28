// Read-only production audit: uses the installed Wrangler login, never mail APIs.
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {digestPreview} from '../src/mail.js';
const cwd=fileURLToPath(new URL('../',import.meta.url));
const DB={prepare(sql){
  if(!/^SELECT /i.test(sql))throw Error('read_only_audit');
  return {async all(){
    const output=execFileSync(process.execPath,['node_modules/wrangler/bin/wrangler.js','d1','execute','concost-migration-trial','--remote','--json','--command',sql],{
      cwd,encoding:'utf8',env:{...process.env,WRANGLER_LOG_PATH:cwd+'/.wrangler/diagnostic-logs',WRANGLER_SEND_METRICS:'false'},maxBuffer:8*1024*1024,
    });
    return {results:JSON.parse(output)[0].results};
  }};
}};
const preview=await digestPreview({DB});
console.log(JSON.stringify({checked_at:new Date().toISOString(),subject:preview.subject,counts:preview.counts,
  pending:preview.pending_notices.map(r=>({title:r.title,published_at:r.published_at,deadline_at:r.deadline_at})),
  has_pending_section:preview.html.includes('최근 7일 미발송·진행 중 공고'),
},null,2));
