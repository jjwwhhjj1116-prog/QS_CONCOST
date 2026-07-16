from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


DEFAULT_BASE_URL = "https://qs-concost.onrender.com"


def _base_url() -> str:
    return os.getenv("QS_CONCOST_URL", DEFAULT_BASE_URL).rstrip("/")


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"{name} 환경변수가 비어 있습니다.")
    return value


def _post(path: str, headers: dict[str, str]) -> dict:
    url = f"{_base_url()}{path}"
    request = urllib.request.Request(url, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=540) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"{url} 호출 실패: HTTP {exc.code} {body}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"{url} 호출 실패: {exc}") from exc
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise SystemExit(f"{url} 응답이 JSON이 아닙니다: {body[:500]}")
    print(json.dumps(payload, ensure_ascii=False))
    if payload.get("error"):
        raise SystemExit(1)
    return payload


def collect() -> dict:
    token = _required_env("DIGEST_TRIGGER_TOKEN")
    return _post(
        "/api/automation/collect",
        {
            "Authorization": f"Bearer {token}",
            "X-Collect-Scheduled": "true",
            "User-Agent": "QS-CONCOST-Render-Cron/1.0",
        },
    )


def digest() -> dict:
    return _post(
        "/api/automation/digest",
        {
            "X-Resend-Api-Key": _required_env("RESEND_API_KEY"),
            "X-Digest-From-Email": _required_env("DIGEST_FROM_EMAIL"),
            "X-Digest-Recipients": _required_env("DIGEST_RECIPIENTS"),
            "X-Digest-Scheduled": "true",
            "User-Agent": "QS-CONCOST-Render-Cron/1.0",
        },
    )


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    command = args[0] if args else ""
    if command == "collect":
        collect()
        return 0
    if command == "digest":
        digest()
        return 0
    print("사용법: python -m tender_radar.automation_client collect|digest", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
