import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {collectionView,trialUI} from '../src/trial-ui.js';

test('collection popup uses terminal tasks and separates processing counts',()=>{
  const view=collectionView({status:'running',sources:[
    {group:'입찰공고',state:'succeeded',total:1},
    {group:'건설 뉴스',state:'succeeded',total:10},
    {group:'법규·제도',state:'running',total:5},
  ]});
  assert.equal(view.percent,66);assert.equal(view.done,2);assert.equal(view.complete,false);
  assert.deepEqual(view.groups,{'입찰공고':1,'건설 뉴스':10,'법규·제도':5});
});
test('timeout completion is partial, not successful collection',()=>{
  const view=collectionView({status:'complete',ok:true,sources:[{state:'succeeded',total:1},{state:'expired',total:0}]});
  assert.equal(view.status,'부분 완료');assert.equal(view.failed,1);assert.equal(view.percent,100);
  assert.equal(collectionView({status:'complete',ok:false,sources:[{state:'failed'}]}).status,'확인 실패');
});
test('queued work has zero measured progress and browser bundle is valid JS',()=>{
  assert.equal(collectionView({status:'running',sources:[{state:'queued'}]}).percent,0);
  assert.doesNotThrow(()=>new Function(trialUI));
  assert.ok(trialUI.includes('dialog.showModal()'));
  assert.ok(!trialUI.includes('HashChangeEvent'));
});
test('deployed serialized UI does not require Worker-only name helpers',()=>{
  const config=JSON.parse(readFileSync(new URL('../wrangler.jsonc',import.meta.url),'utf8'));
  assert.equal(config.keep_names,false);
});
