from __future__ import annotations


KEYWORD_GROUPS = {
    "QS 핵심": {
        "공사비": 25,
        "사업비 관리": 25,
        "사업비관리": 25,
        "공사원가": 25,
        "원가계산": 22,
        "적산": 25,
        "물량산출": 25,
        "수량산출": 22,
        "내역서": 18,
        "설계경제성": 25,
        "공사비 검증": 25,
        "공사비검증": 25,
        "설계변경 검토": 22,
        "건축견적": 25,
        "개산견적": 22,
        "예정가격": 15,
        "표준품셈": 18,
        "일위대가": 20,
        "원가검토": 22,
        "사업성 검토": 18,
    },
    "안전진단": {
        "정밀안전진단": 25,
        "정밀안전점검": 22,
        "안전진단": 22,
        "안전점검": 16,
        "구조안전": 18,
        "내진성능": 18,
        "시설물 안전": 16,
    },
    "정비사업": {
        "재건축": 20,
        "재개발": 20,
        "정비사업": 18,
        "도시정비": 15,
        "소규모주택정비": 18,
        "가로주택정비": 18,
        "관리처분": 14,
        "추정분담금": 18,
        "공사비 분쟁": 20,
    },
    "건설산업": {
        "건설산업": 12,
        "건설경기": 12,
        "건설동향": 12,
        "수주": 14,
        "착공": 10,
        "건설현장": 10,
    },
    "분야": {
        "건축": 9,
        "토목": 9,
        "조경": 9,
        "도시계획": 9,
        "교육시설": 9,
        "학교": 6,
    },
    "기회": {
        "용역": 6,
        "설계공모": 10,
        "제안서": 8,
        "기본설계": 7,
        "실시설계": 7,
        "VE": 10,
        "BIM": 8,
    },
}

EXCLUDE_KEYWORDS = ("식자재", "보험", "단순 임대", "폐기물 운반")


def score_notice(*parts: object) -> tuple[int, list[str]]:
    text = " ".join(str(part or "") for part in parts).lower()
    matched: list[str] = []
    score = 0
    for group, keywords in KEYWORD_GROUPS.items():
        for keyword, weight in keywords.items():
            if keyword.lower() in text:
                matched.append(f"{group}:{keyword}")
                score += weight
    for keyword in EXCLUDE_KEYWORDS:
        if keyword.lower() in text:
            matched.append(f"제외:{keyword}")
            score -= 35
    return max(0, min(100, score)), matched
