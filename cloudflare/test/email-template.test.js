import test from 'node:test';
import assert from 'node:assert/strict';
import {buildPreview,digestItemKey} from '../src/mail.js';
const time=Date.parse('2026-09-09T10:00:00+09:00');
const bid={kind:'notice',source:'나라장터',source_key:'1',title:'공사비 검증 용역',score:90,institution:'테스트 조합',estimated_price:125000000,published_at:'2026-09-09',deadline_at:'2026-09-30',url:'https://example.org/bid'};
test('incumbent brand, counts, cards and category sections are restored',()=>{
  const p=buildPreview([bid,{...bid,kind:'news',source_key:'2',category:'건설 주요뉴스'},{...bid,kind:'news',source_key:'3',category:'법규·제도 개정'}],time);
  for(const text of ['concost-logo.png','오늘의 건설 기회 브리핑','신규 입찰공고','기존 알림 프로젝트','건설 주요뉴스','법규·제도 개정','적합도','테스트 조합','125,000,000원','2026-09-30','https://example.org/bid'])assert.ok(p.html.includes(text),text);
  assert.deepEqual(p.counts,{new_notices:1,old_notices:0,new_news:2,construction_news:1,law_news:1});
});
test('only confirmed sent open bids are existing, old news never repeats as new',()=>{
  const old={...bid,source_key:'old',published_at:'2026-09-08'},unknown={...old,source_key:'unknown'},oldNews={...old,kind:'news',source_key:'news'};
  const p=buildPreview([bid,old,unknown,oldNews,{...old,source_key:'closed',notice_type:'마감'}],time,[digestItemKey(old),digestItemKey(oldNews),digestItemKey({...old,source_key:'closed'})]);
  assert.deepEqual(p.items,[bid]);assert.deepEqual(p.old_notices,[old]);assert.equal(p.counts.old_notices,1);
  assert.equal(buildPreview([bid],time,[digestItemKey(bid)]).counts.new_notices,0);
});
test('empty preview stays branded and does not claim verified zero',()=>{
  const p=buildPreview([],time);assert.equal(p.items.length,0);
  assert.ok(p.html.includes('실제 공고가 없다는 뜻은 아니며'));assert.ok(p.html.includes('concost-logo.png'));
});
test('dedup uses kind and variant; malicious source content cannot execute',()=>{
  const p=buildPreview([bid,bid,{...bid,variant:'001'},{...bid,kind:'news'}],time);assert.equal(p.items.length,3);
  const attack=buildPreview([{...bid,title:'<script>alert(1)</script>',url:'javascript:alert(1)',institution:'<img onerror=alert(1)>'}],time);
  assert.ok(!attack.html.includes('<script>'));assert.ok(!attack.html.includes('javascript:'));assert.ok(attack.html.includes('&lt;img'));
});
