from __future__ import annotations

SCORING_VERSION = "concost-consulting-v2"

# CONCOST는 시공사가 아니라 공사비·원가·안전·계약 전문 컨설팅 회사다.
# 같은 업무군의 유사어가 한 제목에 반복되어도 최고 가중치 하나만 반영한다.
SERVICE_GROUPS = {
    "공사비·견적": {
        "공사비 검증": 62, "공사비검증": 62, "공사비 적정성": 60,
        "개산견적": 60, "실시견적": 58, "실시설계 견적": 58,
        "예산견적": 55, "건축견적": 55, "견적용역": 52,
        "사업비 관리": 55, "사업비관리": 55, "원가계산": 55,
        "원가검토": 55, "공사원가": 50, "적산": 50,
        "물량산출": 52, "수량산출": 52, "내역서 작성": 48,
        "내역서": 28, "BOQ": 48, "일위대가": 40, "표준품셈": 35,
        "예정가격": 35, "설계경제성": 50, "VE": 28,
    },
    "설계변경·클레임": {
        "설계변경 정산": 62, "설계변경": 38, "클레임": 62,
        "계약금액 조정": 58, "공사비 분쟁": 55, "분쟁조정": 48,
        "정산": 35,
    },
    "물가변동·ES": {
        "물가변동": 60, "물가상승": 58, "물가연동": 50,
        "에스컬레이션": 60, "ESCALATION": 60, "ES 검토": 60,
        "ES 용역": 60, "지수조정률": 55, "품목조정률": 55,
    },
    "안전·구조진단": {
        "정밀안전진단": 60, "정밀안전점검": 54, "안전진단": 56,
        "구조안전": 52, "내진성능평가": 54, "내진성능": 46,
        "시설물 안전": 44, "안전점검": 32,
    },
    "정비사업": {
        "재건축 공사비": 60, "재개발 공사비": 60, "정비사업 공사비": 60,
        "추정분담금": 50, "관리처분계획": 36, "가로주택정비": 22,
        "소규모주택정비": 22, "재건축": 18, "재개발": 18,
        "정비사업": 16,
    },
    "FED·BIM": {
        "FED 내역서": 58, "해외 내역서": 55, "CSI CODE": 46,
        "구조 BIM": 56, "BIM 산출": 52, "WBS": 36, "BIM": 22,
    },
}

# 전문서비스를 발주한다는 문맥. 합계가 과도해지지 않도록 15점까지만 반영한다.
ADVISORY_CONTEXT = {
    "컨설팅": 12, "용역": 10, "검증": 10, "검토": 8,
    "산정": 8, "산출": 8, "평가": 7, "제안서": 6,
    "사업성": 10, "자문": 10,
}

# 직접 시공업체를 구하는 공고는 CONCOST의 수주 대상이 아니다.
DIRECT_CONSTRUCTION = {
    "공사업체 선정": 75, "시공업체 선정": 75, "업체선정공고": 65,
    "사업자 선정": 55, "도장공사": 55, "재도장": 55,
    "균열보수": 50, "방수공사": 50, "보수공사": 45,
    "교체공사": 45, "설치공사": 45, "철거공사": 45,
}

IRRELEVANT = ("식자재", "보험", "단순 임대", "폐기물 운반", "청소용역", "경비용역")


def _best_match(text: str, keywords: dict[str, int]) -> tuple[str, int] | None:
    matches = ((keyword, weight) for keyword, weight in keywords.items() if keyword.lower() in text)
    return max(matches, key=lambda item: (item[1], len(item[0])), default=None)


def score_notice(*parts: object) -> tuple[int, list[str]]:
    """Score an opportunity for CONCOST's consulting services, not construction work."""
    text = " ".join(str(part or "") for part in parts).lower()
    matched: list[str] = []
    score = 0
    service_score = 0

    for group, keywords in SERVICE_GROUPS.items():
        best = _best_match(text, keywords)
        if best:
            keyword, weight = best
            service_score += weight
            score += weight
            matched.append(f"전문업무:{group}({keyword})")

    context_hits = [(keyword, weight) for keyword, weight in ADVISORY_CONTEXT.items() if keyword.lower() in text]
    if context_hits:
        context_score = min(15, sum(weight for _, weight in context_hits))
        score += context_score
        labels = ",".join(keyword for keyword, _ in context_hits[:2])
        matched.append(f"발주형태:{labels}")

    direct_hits = [(keyword, penalty) for keyword, penalty in DIRECT_CONSTRUCTION.items() if keyword.lower() in text]
    if direct_hits:
        keyword, penalty = max(direct_hits, key=lambda item: item[1])
        # 명확한 전문용역이 공사의 원가·견적을 다루는 경우에는 작은 혼합공고 감점만 준다.
        applied_penalty = 12 if service_score >= 45 and context_hits else penalty
        score -= applied_penalty
        matched.append(f"직접시공 감점:{keyword}")

    for keyword in IRRELEVANT:
        if keyword.lower() in text:
            score -= 70
            matched.append(f"업무제외:{keyword}")

    return max(0, min(100, score)), matched
