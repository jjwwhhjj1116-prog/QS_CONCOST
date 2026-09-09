"""One-time loopback administrator setup; passwords never enter files/logs.

Run locally, then let the user enter and submit the new credential themselves.
Only ADMIN_VERIFIER is installed. Collection/mail configuration is untouched.
"""
import hashlib
import hmac
import json
import secrets
import shutil
import subprocess
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parent
BASE = 'https://concost-migration-trial.jjwwhhjj1116.workers.dev'
HEADERS = {'User-Agent': 'CONCOST-Admin-Setup/1.0'}
CLI = [shutil.which('node'), str(ROOT / 'node_modules/wrangler/bin/wrangler.js')]


def verifier(password, confirmation):
    if password != confirmation:
        raise ValueError('비밀번호 확인이 일치하지 않습니다.')
    if not (12 <= len(password) <= 128 and any(c.isalpha() for c in password) and any(c.isdigit() for c in password)):
        raise ValueError('비밀번호는 영문·숫자를 포함해 12~128자로 입력하세요.')
    salt = secrets.token_bytes(16)
    return dict(username='concost_dt', password_salt=salt.hex(), iterations=100000,
                password_hash=hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 100000).hex())


def install_and_verify(auth, password):
    result = subprocess.run(CLI + ['secret', 'put', 'ADMIN_VERIFIER'], cwd=ROOT,
                            input=json.dumps(auth), text=True, capture_output=True, timeout=90)
    if result.returncode:
        return '설정 저장 실패. 비밀번호는 출력하지 않았습니다. Codex에 저장 실패라고 알려주세요.'
    for _ in range(10):
        try:
            req = urllib.request.Request(BASE + '/api/admin/login',
                data=json.dumps(dict(username=auth['username'], password=password)).encode(),
                headers={**HEADERS, 'Content-Type': 'application/json', 'Origin': BASE})
            with urllib.request.urlopen(req, timeout=15) as response:
                cookie = response.headers.get('Set-Cookie', '').split(';')[0]
            req = urllib.request.Request(BASE + '/api/admin/session', headers={**HEADERS, 'Cookie': cookie})
            with urllib.request.urlopen(req, timeout=15) as response:
                assert json.load(response)['authenticated']
            req = urllib.request.Request(BASE + '/api/admin/logout', data=b'{}',
                headers={**HEADERS, 'Cookie': cookie, 'Content-Type': 'application/json', 'Origin': BASE})
            urllib.request.urlopen(req, timeout=15).close()
            return '설정 완료: 실제 Cloudflare 로그인·세션 유지·로그아웃 검증을 통과했습니다.'
        except Exception:
            time.sleep(3)
    return '인증값은 저장했지만 실제 로그인 확인은 아직 실패했습니다. Codex에 확인을 요청하세요.'


def main():
    # Fail closed instead of silently replacing an existing administrator.
    result = subprocess.run(CLI + ['secret', 'list'], cwd=ROOT, capture_output=True, text=True, check=True)
    if any(row['name'] == 'ADMIN_VERIFIER' for row in json.loads(result.stdout)):
        raise SystemExit('Administrator already configured; this first-setup helper will not replace it.')
    nonce = secrets.token_urlsafe(32)
    deadline = time.monotonic() + 900

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, text, status=200, form=False):
            html = '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>CONCOST 관리자 최초 설정</title><style>body{font:17px sans-serif;max-width:520px;margin:60px auto;padding:24px}label,input,button{display:block;margin:16px 0}input{padding:12px;width:90%}button{padding:12px;background:#063b4b;color:white;border:0}</style><h1>CONCOST 관리자 최초 설정</h1><p>' + text + '</p>'
            if form:
                html += '<p>아이디: <b>concost_dt</b></p><p>이 화면은 이 PC에서만 열립니다. 비밀번호 원문은 저장·출력하지 않고 인증용 해시만 Cloudflare에 저장합니다.</p><form method="post" action="/setup"><input type="hidden" name="nonce" value="' + nonce + '"><label>새 비밀번호<input type="password" name="password" minlength="12" maxlength="128" autocomplete="new-password" required></label><label>새 비밀번호 확인<input type="password" name="confirmation" minlength="12" maxlength="128" autocomplete="new-password" required></label><button>관리자 계정 설정 및 로그인 검증</button></form>'
            else:
                html += '<a href="' + BASE + '/#notices">Cloudflare 사이트 열기</a>'
            data = html.encode()
            self.send_response(status)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.headers.get('Host') != self.server.origin.removeprefix('http://'):
                return self.reply('잘못된 접근입니다.', 403)
            self.reply('영문·숫자를 포함해 12자 이상으로 직접 입력하세요.', form=True)

        def do_POST(self):
            if self.path != '/setup' or self.headers.get('Origin') != self.server.origin or self.headers.get('Host') != self.server.origin.removeprefix('http://'):
                return self.reply('잘못된 접근입니다.', 403)
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 4096:
                return self.reply('요청 크기 오류', 400)
            data = parse_qs(self.rfile.read(size).decode())
            if not hmac.compare_digest(data.get('nonce', [''])[0], nonce):
                return self.reply('인증 오류', 403)
            try:
                password = data.get('password', [''])[0]
                auth = verifier(password, data.get('confirmation', [''])[0])
            except ValueError as error:
                return self.reply(str(error), 400, form=True)
            try:
                outcome = install_and_verify(auth, password)
            except Exception:
                outcome = '저장 또는 검증 중 문제가 발생했습니다. Codex에 확인을 요청하세요.'
            print(outcome, flush=True)
            self.server.done = True
            try:
                self.reply(outcome)
            except (ConnectionError, OSError):
                pass  # The user may navigate away while the deployment completes.

    server = HTTPServer(('127.0.0.1', 0), Handler)
    server.origin = f'http://127.0.0.1:{server.server_port}'
    server.timeout = 1
    server.done = False
    print('SETUP_URL=' + server.origin, flush=True)
    while not server.done and time.monotonic() < deadline:
        server.handle_request()
    server.server_close()


if __name__ == '__main__':
    main()
