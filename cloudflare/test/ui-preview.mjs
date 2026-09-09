// Local-only UI fixture: no external calls, credentials, collection, or mail.
import http from 'node:http';
import {readFileSync} from 'node:fs';
import {trialUI} from '../src/trial-ui.js';
const html=readFileSync(new URL('../../tender_radar/static/index.html',import.meta.url),'utf8').replace('</head>','<script src="/cf-trial.js" defer></script></head>');
let starts=0,reads=0;
http.createServer((req,res)=>{
  const url=new URL(req.url,'http://localhost');
  if(url.pathname==='/cf-trial.js'){res.setHeader('Content-Type','application/javascript');return res.end(trialUI);}
  if(url.pathname==='/fixture-metrics'){res.setHeader('Content-Type','application/json');return res.end(JSON.stringify({starts,reads}));}
  if(url.pathname.startsWith('/api/')){
    res.setHeader('Content-Type','application/json');let data=[];
    if(req.method==='POST'&&url.pathname.includes('collect')){starts++;reads=0;data={job_id:'fixture',deadline:Date.now()+300000};}
    else if(url.pathname.startsWith('/api/collect/status/')){reads++;const complete=reads>=15;data={status:complete?'complete':'running',ok:true,partial:true,sources:[
      {source:'나라장터 용역',group:'입찰공고',state:'succeeded',candidates:628,total:1},
      {source:'CERIK 동향브리핑',group:'건설 뉴스',state:'succeeded',candidates:10,total:10},
      {source:'국가법령 건설',group:'법규·제도',state:complete?'succeeded':'running',candidates:5,total:5},
      {source:'서울교통공사',group:'지원COK',state:'failed',candidates:0,total:0,error:'upstream_http_526'},
    ]};}
    else if(url.pathname==='/api/admin/session')data={authenticated:true};
    else if(url.pathname==='/api/admin/email-settings')data={recipients:[],from_email:'fixture@example.invalid',enabled:false,schedule_time:'10:00'};
    else if(url.pathname==='/api/admin/settings')data={api_key_configured:true,law_api_configured:true};
    else if(url.pathname==='/api/stats')data={total:0,by_source:{},by_category:{},by_status:{},intelligence:{},news:{}};
    return res.end(JSON.stringify(data));
  }
  if(/\.(png|svg|ico)$/.test(url.pathname)){try{return res.end(readFileSync(new URL('../../tender_radar/static'+url.pathname,import.meta.url)));}catch{res.statusCode=404;return res.end();}}
  res.setHeader('Content-Type','text/html; charset=utf-8');res.end(html);
}).listen(8790,'127.0.0.1',()=>console.log('UI fixture http://127.0.0.1:8790/#notices'));
