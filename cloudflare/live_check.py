"""Explicit trial-only live check. Rotates its admin secret; never sends email.

Run from repo root with the confirmed trial workers.dev URL as the argument.
Credentials go to Wrangler stdin and request headers, never files or output.
"""
import collections
import json
import secrets
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tender_radar.config import get_settings


def main():
    if '--install-trial-secrets' not in sys.argv:
        raise SystemExit('Explicit approval is required to store the existing API key in the trial Worker; then pass --install-trial-secrets')
    base = sys.argv[1].rstrip('/')
    host = urlsplit(base)
    if host.scheme != 'https' or host.netloc != 'concost-migration-trial.jjwwhhjj1116.workers.dev' or host.path:
        raise SystemExit('Only the confirmed migration trial URL is allowed')
    credential = get_settings().service_key
    if not credential:
        raise SystemExit('Local DATA_GO_KR_SERVICE_KEY not configured')
    token = secrets.token_urlsafe(40)
    cli = ROOT / 'cloudflare/node_modules/wrangler/bin/wrangler.js'
    result = subprocess.run([shutil.which('node'), str(cli), 'secret', 'bulk'],
        cwd=ROOT / 'cloudflare', input=json.dumps({ 'DATA_GO_KR_SERVICE_KEY': credential, 'TRIAL_ADMIN_TOKEN': token }),
        capture_output=True, encoding='utf-8')
    if result.returncode:
        raise SystemExit('Trial secret installation failed; inspect Wrangler status (credentials not printed)')
    print('Trial credentials installed securely; production mail credentials not copied', flush=True)

    def call(path, body=None):
        request = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
            headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json',
                     'User-Agent': 'CONCOST-Trial-Verification/1.0'})
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    started = call('/api/trial/collect', {'lookback_hours': 168})
    print(json.dumps(started), flush=True)
    previous = None
    while time.time() * 1000 < started['deadline'] + 10000:
        jobs = call(started['status_url'])
        states = dict(collections.Counter(j['state'] for j in jobs))
        summary = {'states': states, 'candidates': sum(j['candidates'] or 0 for j in jobs),
                   'kept': sum(j['kept'] or 0 for j in jobs),
                   'errors': dict(collections.Counter(j['error'] for j in jobs if j['error']))}
        if summary != previous:
            print(json.dumps(summary, ensure_ascii=False), flush=True)
            previous = summary
        if jobs and all(j['state'] in ('succeeded', 'failed', 'expired', 'enqueue_failed') for j in jobs):
            break
        time.sleep(5)
    print(json.dumps({'stats': call('/api/stats')}, ensure_ascii=False), flush=True)
    print(json.dumps({'notices': [{'source': r['source'], 'title': r['title'], 'score': r['score']} for r in call('/api/notices')]}, ensure_ascii=False), flush=True)
    preview = call('/api/trial/digest')
    assert preview['sent'] is False and preview['mode'] == 'dry-run'
    print('Digest preview passed: no email sent', flush=True)


if __name__ == '__main__':
    main()
