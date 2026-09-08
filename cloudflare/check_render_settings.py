"""Read-only authenticated inventory. Password/cookies/addresses never printed."""
import getpass
import http.cookiejar
import json
import urllib.request
from urllib.error import HTTPError, URLError

BASE = 'https://qs-concost.onrender.com'


def main():
    username = input('Render admin ID: ').strip()
    password = getpass.getpass('Render admin password (hidden): ')
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(path, body=None):
        request = urllib.request.Request(BASE + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'User-Agent': 'CONCOST-Settings-Audit/1.0', 'Content-Type': 'application/json', 'Origin': BASE})
        with opener.open(request, timeout=60) as response:
            return json.load(response)

    try:
        call('/api/admin/login', {'username': username, 'password': password})
        password = None
        settings = call('/api/admin/settings')
        email = call('/api/admin/email-settings')
        print(json.dumps({
            'login': 'ok', 'public_api_configured': settings.get('api_key_configured'),
            'law_api_configured': settings.get('law_api_configured'),
            'resend_configured': email.get('provider_configured'),
            'from_address_configured': bool(email.get('from_email')),
            'recipient_count': len(email.get('recipients', [])),
            'enabled': email.get('enabled'), 'schedule_time': email.get('schedule_time'),
            'storage_persistent': email.get('storage_persistent'),
            'resend_environment_backed': email.get('resend_environment_backed'),
            'recent_deliveries': [{'started_at': row.get('started_at'), 'status': row.get('status'),
                                  'recipient_count': row.get('recipient_count')} for row in email.get('deliveries', [])[:3]],
            'api_exposes_resend_value': bool(email.get('resend_api_key')),
        }, ensure_ascii=False), flush=True)
    except HTTPError as error:
        print(f'Render read-only check failed: HTTP {error.code}', flush=True)
    except (URLError, TimeoutError, ValueError):
        print('Render read-only check failed: network/response unavailable', flush=True)


if __name__ == '__main__':
    main()
