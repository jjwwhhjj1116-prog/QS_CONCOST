from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .scoring import MIN_NOTICE_SCORE, score_notice


class ProcurementIntelligenceError(RuntimeError):
    pass


def _pick(item: dict[str, Any], *keys: str, default: Any = "") -> Any:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return value
    return default


def _money(value: Any) -> int | None:
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return None


def _rate(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def _fetch(
    base_url: str,
    operation: str,
    service_key: str,
    params: dict[str, Any],
    rows: int = 300,
    max_pages: int = 2,
) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        query = {
            "serviceKey": service_key,
            "pageNo": page,
            "numOfRows": rows,
            "type": "json",
            **params,
        }
        request = Request(
            f"{base_url}/{operation}?{urlencode(query)}",
            headers={"User-Agent": "CONCOST-Procurement-Intelligence/1.0"},
        )
        try:
            with urlopen(request, timeout=14) as response:
                payload = json.loads(response.read().decode("utf-8-sig"))
        except HTTPError as exc:
            if exc.code in {401, 403}:
                raise ProcurementIntelligenceError(
                    f"{operation}: 활용승인 또는 인증키 반영 대기(HTTP {exc.code})"
                ) from exc
            raise ProcurementIntelligenceError(f"{operation}: HTTP {exc.code}") from exc
        except (URLError, TimeoutError) as exc:
            raise ProcurementIntelligenceError(f"{operation}: 연결 시간 초과") from exc
        except json.JSONDecodeError as exc:
            raise ProcurementIntelligenceError(f"{operation}: JSON 응답 오류") from exc
        response = payload.get("response", payload)
        header = response.get("header", {})
        code = str(header.get("resultCode", "00"))
        if code not in {"00", "0"}:
            raise ProcurementIntelligenceError(
                f"{operation}: API {code} {header.get('resultMsg', '')}".strip()
            )
        body = response.get("body", {})
        items = body.get("items", [])
        if isinstance(items, dict):
            items = items.get("item", items)
        if isinstance(items, dict):
            items = [items]
        items = list(items or [])
        collected.extend(items)
        total = int(body.get("totalCount", len(collected)) or 0)
        if not items or len(collected) >= total:
            break
    return collected


def _pipeline_item(item: dict[str, Any], stage: str, category: str) -> dict[str, Any]:
    if stage == "발주계획":
        key = str(_pick(item, "orderPlanUntyNo", "orderPlanSno", "cnstwkMngNo"))
        title = str(_pick(item, "bizNm", "cnstwkPrdCntnts", "specCntnts", default="발주계획"))
        institution = str(_pick(item, "orderInsttNm", "totlmngInsttNm"))
        published = str(_pick(item, "nticeDt", "chgDt"))
        planned = f"{_pick(item, 'orderYear')}-{str(_pick(item, 'orderMnth')).zfill(2)}"
        amount = _money(_pick(item, "sumOrderAmt", "orderContrctAmt", "orderThtmContrctAmt"))
        region = str(_pick(item, "cnstwkRgnNm"))
        url = ""
    elif stage == "사전규격":
        key = str(_pick(item, "bfSpecRgstNo", "refNo"))
        title = str(
            _pick(item, "prdctClsfcNoNm", "prdctDtlList", "refNo", default="사전규격")
        )
        institution = str(_pick(item, "orderInsttNm", "rlDminsttNm"))
        published = str(_pick(item, "rgstDt", "rcptDt", "chgDt"))
        planned = str(_pick(item, "opninRgstClseDt", "dlvrTmlmtDt"))
        amount = _money(_pick(item, "asignBdgtAmt"))
        region = ""
        url = str(_pick(item, "specDocFileUrl1"))
    else:
        key = str(_pick(item, "prcrmntReqNo", "frstyearPrcrmntReqNo"))
        title = str(_pick(item, "prcrmntReqNm", "cnsttyNm", default="조달요청"))
        institution = str(_pick(item, "orderInsttNm", "rcptBrnofceNm"))
        published = str(_pick(item, "inptDt", "rcptDt"))
        planned = str(_pick(item, "techRvwReqstDate", "rprsntDedtDate"))
        amount = _money(
            _pick(
                item, "presmptPrce", "totSrvceBdgtAmt", "totCnstwkScleAmt",
                "bdgtAmt", "thtmBdgtAmt", "contrctAmt",
            )
        )
        region = str(_pick(item, "cnstrtsiteRgnNm", "rprsntDlvrPlce"))
        url = str(_pick(item, "prcrmntReqInfoUrl"))
    score, matched = score_notice(title, institution, region, category, stage)
    return {
        "source": "나라장터",
        "source_key": key or f"{stage}-{hash(json.dumps(item, sort_keys=True, ensure_ascii=False))}",
        "stage": stage,
        "category": category,
        "title": title,
        "institution": institution,
        "published_at": published,
        "planned_at": planned,
        "amount": amount,
        "region": region,
        "url": url,
        "score": score,
        "matched_keywords": matched,
        "raw": item,
    }


def collect_pipeline(service_key: str, lookback_hours: int = 72) -> list[dict[str, Any]]:
    if not service_key:
        raise ProcurementIntelligenceError("공공데이터포털 인증키가 비어 있습니다.")
    now = datetime.now()
    start = now - timedelta(hours=max(24, lookback_hours))
    common_dates = {
        "inqryDiv": "1",
        "inqryBgnDt": start.strftime("%Y%m%d%H%M"),
        "inqryEndDt": now.strftime("%Y%m%d%H%M"),
    }
    month_params = {
        "inqryDiv": "1",
        "orderBgnYm": (now - timedelta(days=62)).strftime("%Y%m"),
        "orderEndYm": now.strftime("%Y%m"),
    }
    jobs = (
        (
            "발주계획", "공사", "https://apis.data.go.kr/1230000/ao/OrderPlanSttusService",
            "getOrderPlanSttusListCnstwk", month_params,
        ),
        (
            "발주계획", "용역", "https://apis.data.go.kr/1230000/ao/OrderPlanSttusService",
            "getOrderPlanSttusListServc", month_params,
        ),
        (
            "사전규격", "공사", "https://apis.data.go.kr/1230000/ao/HrcspSsstndrdInfoService",
            "getPublicPrcureThngInfoCnstwk", common_dates,
        ),
        (
            "사전규격", "용역", "https://apis.data.go.kr/1230000/ao/HrcspSsstndrdInfoService",
            "getPublicPrcureThngInfoServc", common_dates,
        ),
        (
            "조달요청", "공사", "https://apis.data.go.kr/1230000/ao/PrcrmntReqInfoService",
            "getPrcrmntReqInfoListCnstwk", common_dates,
        ),
        (
            "조달요청", "일반용역", "https://apis.data.go.kr/1230000/ao/PrcrmntReqInfoService",
            "getPrcrmntReqInfoListGnrlServc", common_dates,
        ),
        (
            "조달요청", "기술용역", "https://apis.data.go.kr/1230000/ao/PrcrmntReqInfoService",
            "getPrcrmntReqInfoListTechServc", common_dates,
        ),
    )
    items: list[dict[str, Any]] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="procurement-pipeline") as pool:
        futures = {
            pool.submit(_fetch, base, operation, service_key, params): (stage, category)
            for stage, category, base, operation, params in jobs
        }
        for future in as_completed(futures):
            stage, category = futures[future]
            try:
                normalized = (_pipeline_item(row, stage, category) for row in future.result())
                items.extend(row for row in normalized if row["score"] >= MIN_NOTICE_SCORE)
            except Exception as exc:
                errors.append(str(exc))
    if not items and len(errors) == len(jobs):
        raise ProcurementIntelligenceError(" / ".join(errors[:3]))
    return items


def _cost_item(item: dict[str, Any], record_type: str, category: str) -> dict[str, Any]:
    if record_type == "낙찰":
        notice_no = str(_pick(item, "bidNtceNo"))
        order = str(_pick(item, "bidNtceOrd", default="000"))
        source_key = f"{notice_no}-{order}"
        title = str(_pick(item, "bidNtceNm", default="낙찰결과"))
        institution = str(_pick(item, "dminsttNm"))
        award_amount = _money(_pick(item, "sucsfbidAmt"))
        contract_amount = None
        company = str(_pick(item, "bidwinnrNm", "fnlSucsfCorpOfcl"))
        recorded_at = str(_pick(item, "fnlSucsfDate", "rgstDt", "rlOpengDt"))
        rate = _rate(_pick(item, "sucsfbidRate"))
        url = ""
    elif record_type == "공동주택 낙찰":
        notice_no = str(_pick(item, "bidNum"))
        source_key = notice_no
        title = str(_pick(item, "bidTitle", default="공동주택 낙찰결과"))
        institution = str(_pick(item, "bidKaptname"))
        award_amount = _money(_pick(item, "amount"))
        contract_amount = None
        company = str(_pick(item, "bidResultContent"))
        recorded_at = str(_pick(item, "bidRegDate", "bidRegdate"))
        rate = None
        url = ""
    else:
        notice_no = str(_pick(item, "ntceNo"))
        source_key = str(_pick(item, "untyCntrctNo", "dcsnCntrctNo", "cntrctRefNo"))
        title = str(_pick(item, "cntrctNm", "cnstwkNm", default="계약정보"))
        institution = str(_pick(item, "cntrctInsttNm"))
        award_amount = None
        contract_amount = _money(_pick(item, "totCntrctAmt", "thtmCntrctAmt"))
        companies = _pick(item, "corpList", default="")
        company = json.dumps(companies, ensure_ascii=False) if isinstance(companies, (list, dict)) else str(companies)
        recorded_at = str(_pick(item, "cntrctDate", "cntrctCnclsDate", "rgstDt"))
        rate = None
        url = str(_pick(item, "cntrctInfoUrl", "cntrctDtlInfoUrl"))
    region = str(_pick(item, "bidwinnrAdrs", "cntrctInsttJrsdctnDivNm", "bidArea"))
    score, matched = score_notice(title, institution, region, category, record_type)
    return {
        "source": "공동주택관리정보시스템" if record_type == "공동주택 낙찰" else "나라장터",
        "source_key": source_key or f"{record_type}-{hash(json.dumps(item, sort_keys=True, ensure_ascii=False))}",
        "record_type": record_type,
        "category": category,
        "title": title,
        "institution": institution,
        "notice_no": notice_no,
        "base_amount": None,
        "award_amount": award_amount,
        "contract_amount": contract_amount,
        "award_rate": rate,
        "company": company[:500],
        "recorded_at": recorded_at,
        "region": region,
        "url": url,
        "score": score,
        "matched_keywords": matched,
        "raw": item,
    }


def collect_cost_records(service_key: str, lookback_hours: int = 168) -> list[dict[str, Any]]:
    if not service_key:
        raise ProcurementIntelligenceError("공공데이터포털 인증키가 비어 있습니다.")
    now = datetime.now()
    start = now - timedelta(hours=max(72, lookback_hours))
    params = {
        "inqryDiv": "1",
        "inqryBgnDt": start.strftime("%Y%m%d%H%M"),
        "inqryEndDt": now.strftime("%Y%m%d%H%M"),
    }
    jobs = (
        (
            "낙찰", "공사", "https://apis.data.go.kr/1230000/as/ScsbidInfoService",
            "getScsbidListSttusCnstwk", params,
        ),
        (
            "낙찰", "용역", "https://apis.data.go.kr/1230000/as/ScsbidInfoService",
            "getScsbidListSttusServc", params,
        ),
        (
            "계약", "공사", "https://apis.data.go.kr/1230000/ao/CntrctInfoService",
            "getCntrctInfoListCnstwk", params,
        ),
        (
            "계약", "용역", "https://apis.data.go.kr/1230000/ao/CntrctInfoService",
            "getCntrctInfoListServc", params,
        ),
    )
    items: list[dict[str, Any]] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="cost-analysis") as pool:
        futures = {
            pool.submit(_fetch, base, operation, service_key, query, 300, 2):
            (record_type, category)
            for record_type, category, base, operation, query in jobs
        }
        for future in as_completed(futures):
            record_type, category = futures[future]
            try:
                normalized = (_cost_item(row, record_type, category) for row in future.result())
                items.extend(row for row in normalized if row["score"] >= MIN_NOTICE_SCORE)
            except Exception as exc:
                errors.append(str(exc))
    # Apartment result API currently ignores date parameters on some gateway
    # versions and can return a million-row total. Read one small first page,
    # then locally keep only relevant current rows.
    try:
        apartment_rows = _fetch(
            "https://apis.data.go.kr/1613000/ApHusBidResultNoticeInfoOfferServiceV2",
            "getPblAncDeSearchV2",
            service_key,
            {
                "_type": "json",
                "startDate": start.strftime("%Y%m%d"),
                "endDate": now.strftime("%Y%m%d"),
            },
            rows=100,
            max_pages=1,
        )
        normalized = (_cost_item(row, "공동주택 낙찰", "공동주택") for row in apartment_rows)
        items.extend(row for row in normalized if row["score"] >= MIN_NOTICE_SCORE)
    except Exception as exc:
        errors.append(str(exc))
    if not items and len(errors) >= len(jobs):
        raise ProcurementIntelligenceError(" / ".join(errors[:3]))
    return items
