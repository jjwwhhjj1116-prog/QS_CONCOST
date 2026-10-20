import {WorkerEntrypoint} from 'cloudflare:workers';
import {claimApprovalMail} from './claim-approval-mail.js';
export {default} from './worker.js';

export class ClaimApprovalMailer extends WorkerEntrypoint {
  fetch(request){return claimApprovalMail(request,this.env);}
}
