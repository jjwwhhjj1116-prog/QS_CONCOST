"""Read-only K-apt response check; never output keys or request URLs."""
import sys
import json
from pathlib import Path
from urllib.request import urlopen
from urllib.parse import urlencode
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tender_radar.config import get_settings

key = get_settings().service_key
for extra in ({'_type': 'json'}, {}):
    query = urlencode(dict(serviceKey=key, startDate='20260925', endDate='20260928',
                           pageNo=1, numOfRows=10, **extra))
    try:
        with urlopen('https://apis.data.go.kr/1613000/ApHusBidPblAncInfoOfferServiceV3/getPblAncDeSearchV3?' + query, timeout=15) as response:
            print('status', response.status, 'type_parameter', bool(extra))
            charset = response.headers.get_content_charset() or 'utf-8'
            payload = json.loads(response.read().decode(charset))['response']
            body = payload['body']
            print(json.dumps({'charset': charset, 'header': payload.get('header'),
                              'total': body['totalCount'], 'returned': len(body['items']),
                              'first_date': body['items'][0]['bidRegDate']}, ensure_ascii=True))
    except HTTPError as error:
        print('status', error.code, 'type_parameter', bool(extra))
        print(error.read(800).decode('utf-8', errors='replace').replace(key, '[redacted]'))
    except Exception as error:
        print(type(error).__name__)
