from __future__ import annotations

import argparse
import time

from .config import get_settings
from .db import init_db, prune_news, upsert_news, upsert_notice
from .collector import collect_all
from .law_news import collect_law_news
from .official_news import collect_official_news
from .secrets_store import get_secret, migrate_secret


def collect() -> int:
    settings = get_settings()
    init_db(settings.db_path)
    migrate_secret(settings.db_path, "public_data_api_key", settings.service_key)
    service_key = get_secret(settings.db_path, "public_data_api_key", settings.service_key)
    notices, sources = collect_all(service_key, settings.lookback_hours)
    counts = {"inserted": 0, "updated": 0, "unchanged": 0}
    for notice in notices:
        counts[upsert_notice(settings.db_path, notice)] += 1
    for source in sources:
        state = f"{source['total']}건" if source["ok"] else f"실패 - {source['error']}"
        print(f"{source['source']}: {state}")
    news_items = collect_official_news()
    law_key = get_secret(settings.db_path, "law_api_oc")
    if law_key:
        try:
            news_items.extend(collect_law_news(law_key))
        except Exception as exc:
            print(f"국가법령정보: 실패 - {exc}")
    news_counts = {"inserted": 0, "updated": 0}
    for item in news_items:
        news_counts[upsert_news(settings.db_path, item)] += 1
    if news_items:
        prune_news(settings.db_path, news_items)
    print(f"뉴스·법령: {len(news_items)}건 / 신규 {news_counts['inserted']} / 갱신 {news_counts['updated']}")
    print(f"수집 완료: {len(notices)}건 / 신규 {counts['inserted']} / 변경 {counts['updated']} / 기존 {counts['unchanged']}")
    return 0 if any(source["ok"] for source in sources) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="QS 입찰 레이더")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("collect")
    serve_parser = sub.add_parser("serve")
    serve_parser.add_argument("--open", action="store_true", help="서버 시작 후 브라우저 열기")
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--interval", type=int, default=30, help="수집 간격(분)")
    args = parser.parse_args()
    settings = get_settings()
    if args.command == "init":
        init_db(settings.db_path)
        print(f"DB 준비 완료: {settings.db_path}")
        return 0
    if args.command == "collect":
        return collect()
    if args.command == "serve":
        from .server import serve
        serve(settings, open_browser=args.open)
        return 0
    if args.command == "run":
        while True:
            collect()
            time.sleep(max(1, args.interval) * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
