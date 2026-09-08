import rules from './scoring-rules.json' with { type: 'json' };

// Same dictionary as Python; parity tests prevent an independent scoring policy.
const groups = Object.entries(rules.SERVICE_GROUPS).map(([group, words]) =>
  [group, Object.entries(words).sort((a, b) => b[1] - a[1] || b[0].length - a[0].length)]);
const shorts = new Set(['적산','건축적산','건설적산','공사적산','정산','공사비정산','설계변경정산','기성정산','준공정산']);
const latinPatterns = new Map(Object.values(rules.SERVICE_GROUPS).flatMap(Object.keys)
  .filter(k => /[a-z0-9]/i.test(k)).map(k => [k, new RegExp(`(?<![a-z0-9])${k.toLowerCase().replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(?![a-z0-9])`)]));
function has(text, keyword) {
  if (keyword === '적산' || keyword === '정산')
    text = text.replace(/[가-힣]*(?:적산|정산)(?:성)?/g, m => shorts.has(m) ? m : '');
  return latinPatterns.has(keyword) ? latinPatterns.get(keyword).test(text) : text.includes(keyword.toLowerCase());
}
export function scoreNotice(...parts) {
  const text = parts.map(x => String(x || '')).join(' ').toLowerCase();
  if (/(?:평가위원회|심사위원회).*(?:개최\s*결과|일정\s*변경)|(?:낙찰자|우선협상대상자)\s*선정\s*결과/.test(text))
    return { score: 0, matched_keywords: ['업무제외:모집이 아닌 평가 결과·일정 안내'] };
  let score = 0, serviceScore = 0;
  const matched_keywords = [];
  for (const [group, words] of groups) {
    const hit = words.find(([keyword]) => has(text, keyword));
    if (hit) { score += hit[1]; serviceScore += hit[1]; matched_keywords.push(`전문업무:${group}(${hit[0]})`); }
  }
  const contexts = Object.entries(rules.ADVISORY_CONTEXT).filter(([k]) => text.includes(k.toLowerCase()));
  if (contexts.length) {
    score += Math.min(15, contexts.reduce((n, [, w]) => n + w, 0));
    matched_keywords.push(`발주형태:${contexts.slice(0, 2).map(([k]) => k).join(',')}`);
  }
  const direct = Object.entries(rules.DIRECT_CONSTRUCTION).filter(([k]) => text.includes(k.toLowerCase())).sort((a, b) => b[1] - a[1]);
  if (direct.length) {
    score -= serviceScore >= 45 && contexts.length ? 12 : direct[0][1];
    matched_keywords.push(`직접시공 감점:${direct[0][0]}`);
  }
  for (const k of rules.IRRELEVANT) if (text.includes(k.toLowerCase())) { score -= 70; matched_keywords.push(`업무제외:${k}`); }
  return { score: Math.max(0, Math.min(100, score)), matched_keywords };
}
export function shouldKeep(row) {
  if (row.score < rules.MIN_NOTICE_SCORE) return false;
  const text = [row.title, row.institution, row.region, row.category, row.source, ...(row.matched_keywords || [])].join(' ').toLowerCase();
  if (!rules.SAFETY_DIAGNOSIS_TERMS.some(x => text.includes(x.toLowerCase()))) return true;
  const place = [row.title, row.institution, row.region, row.source].join(' ').toLowerCase();
  return rules.SEOUL_TERMS.some(x => place.includes(x.toLowerCase()));
}
