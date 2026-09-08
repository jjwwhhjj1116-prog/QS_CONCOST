export function kstParts(time = Date.now()) {
  const date = new Date(time + 9 * 3600000);
  return { date: date.toISOString().slice(0, 10), weekday: date.getUTCDay(),
    hour: date.getUTCHours(), minute: date.getUTCMinutes() };
}

export function scheduleAction(time) {
  const k = kstParts(time);
  if (k.weekday === 0 || k.weekday === 6) return null;
  if (k.hour === 9 && k.minute < 55) return 'collect';
  if (k.hour === 10 && k.minute === 0) return 'digest';
  return null; // Never send a missed 10:00 digest hours later.
}

export function jobState(job, now = Date.now()) {
  if (['queued', 'running', 'retrying'].includes(job.state) && now >= job.deadline)
    return 'expired';
  return job.state;
}

export function filterRows(rows, params) {
  const min = Math.max(0, Number(params.get('min_score') || 0));
  const query = (params.get('q') || '').toLowerCase();
  return rows.filter(row => Number(row.score || 0) >= min &&
    ['category', 'source', 'notice_type', 'stage', 'record_type'].every(key =>
      !params.get(key) || row[key] === params.get(key)) &&
    (!query || [row.title, row.institution, row.region].join(' ').toLowerCase().includes(query)));
}

export function todayDigest(rows, time = Date.now()) {
  const today = kstParts(time).date;
  // Missing publication date is unknown, NOT newly published today.
  return rows.filter(row => row.published_at?.slice(0, 10) === today &&
    !['취소', '마감'].includes(row.notice_type) &&
    (!row.deadline_at || row.deadline_at.slice(0, 10) >= today));
}

export function validateResult(result, source) {
  if (result.source_id !== source || typeof result.ok !== 'boolean' || !Array.isArray(result.rows))
    throw new Error('invalid_collector_response');
  if (result.ok && (!['notice', 'news', 'pipeline', 'cost'].includes(result.kind) ||
    result.kept !== result.rows.length || result.candidates < result.kept))
    throw new Error('invalid_collector_counts');
  for (const row of result.rows) {
    if (!row.source || !row.source_key || !row.title ||
      (row.url && !/^https?:\/\//i.test(row.url)) ||
      (result.kind === 'notice' && !(row.score >= 40)))
      throw new Error('invalid_collector_record');
  }
}
