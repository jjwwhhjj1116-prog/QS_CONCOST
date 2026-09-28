"""Read-only G2B identity check; API key and request URL are never printed."""
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tender_radar.config import get_settings

def main():
    key = get_settings().service_key
    if not key:
        print('missing_local_api_key')
        return 1
    for notice in sys.argv[1:]:
        if not re.fullmatch(r'[A-Z0-9]+', notice):
            raise SystemExit('invalid_notice_id')
        query = urlencode(dict(serviceKey=key, type='json', inqryDiv='2',
                               bidNtceNo=notice, numOfRows=20, pageNo=1))
        try:
            with urlopen('https://apis.data.go.kr/1230000/ad/BidPublicInfoService/getBidPblancListInfoServc?' + query, timeout=20) as response:
                payload = json.load(response)['response']
            rows = payload.get('body', {}).get('items', [])
            if isinstance(rows, dict):
                rows = rows.get('item', rows)
            if isinstance(rows, dict):
                rows = [rows]
            print(json.dumps({'notice': notice, 'code': payload.get('header', {}).get('resultCode'),
                              'rows': [{k: row.get(k) for k in ('bidNtceNo', 'bidNtceOrd', 'bidNtceNm', 'bidNtceDt', 'bidClseDt')} for row in rows or []]}, ensure_ascii=False))
        except HTTPError as error:
            print(json.dumps({'notice': notice, 'error': 'http_' + str(error.code)}))
        except Exception:
            print(json.dumps({'notice': notice, 'error': 'source_check_failed'}))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
