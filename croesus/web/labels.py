"""사용자에게 보이는 모든 내부 상수(영문·snake_case)를 한국어로 옮기는 라벨 계층.

원칙
- 백엔드의 코드/식별자는 그대로 두고, 웹에서 보여줄 때만 한국어로 변환한다.
- 단순 번역이 아니라 "잘 모르는 사용자도 이해할 수 있는 말"로 옮긴다.
- 매핑에 없는 값은 원문을 그대로 돌려준다(깨지지 않게).
"""
from __future__ import annotations

# ── Layer 1: 레짐(경기 국면) ──────────────────────────────────────────────
# 성장 방향 × 물가 방향의 2×2 조합.
REGIME_LABEL = {
    "Goldilocks": "골디락스",
    "Reflation": "리플레이션",
    "Stagflation": "스태그플레이션",
    "Deflation": "디플레이션",
}
REGIME_TAGLINE = {
    "Goldilocks": "경기 확장 · 물가 하락 — 가장 우호적인 환경",
    "Reflation": "경기 확장 · 물가 상승 — 뜨거운 경기",
    "Stagflation": "경기 위축 · 물가 상승 — 가장 까다로운 국면",
    "Deflation": "경기 위축 · 물가 하락 — 침체 위험",
}
GROWTH_LABEL = {"Expanding": "확장", "Contracting": "위축"}
INFLATION_LABEL = {"Rising": "상승", "Falling": "하락"}

# ── 권장 자세(positioning) ────────────────────────────────────────────────
POSITIONING_LABEL = {
    "Aggressive": "공격적",
    "Moderately Aggressive": "다소 공격적",
    "Neutral": "중립",
    "Cautious": "신중",
    "Defensive": "방어적",
}
POSITIONING_GUIDANCE = {
    "Aggressive": "위험자산 비중을 평소보다 늘려도 좋은 환경입니다.",
    "Moderately Aggressive": "위험자산을 다소 늘리되 시장 스트레스를 함께 살피세요.",
    "Neutral": "위험자산 비중을 평소대로 유지하세요.",
    "Cautious": "새로 위험자산을 늘리기보다 비중을 지키는 편이 좋습니다.",
    "Defensive": "위험을 줄이고 현금·안전자산 비중을 높이세요.",
}
POSITIONING_TONE = {
    "Aggressive": "ok",
    "Moderately Aggressive": "ok",
    "Neutral": "neutral",
    "Cautious": "warn",
    "Defensive": "bad",
}

# ── 매크로 경고/기회 신호 ─────────────────────────────────────────────────
MACRO_SIGNAL_LABEL = {
    # 경고(위험)
    "HIGH_HY_SPREAD": "하이일드 신용 스프레드가 높음 — 신용 경계감 확대",
    "HIGH_VIX": "변동성(VIX)이 급등 — 주식 시장 불안",
    "INVERTED_YIELD_CURVE": "장단기 금리 역전 — 침체 신호",
    "TIGHT_FINANCIAL_CONDITIONS": "금융 여건이 빠듯해짐 — 유동성 위축",
    # 기회
    "TIGHT_CREDIT_SPREADS": "신용 스프레드가 역사적으로 좁음 — 위험 선호 양호",
    "LOW_VOLATILITY": "변동성이 이례적으로 낮음 — 시장이 차분함",
    "STRONG_GROWTH_SIGNAL": "구리/금 비율이 강함 — 원자재가 성장세를 확인",
}

# ── 추천 행동(action_type) ────────────────────────────────────────────────
ACTION_LABEL = {
    "hold": "유지",
    "raise_cash": "현금 확보",
    "trim": "비중 축소",
    "rebalance_to_band": "밴드 복귀",
    "block_new_buy": "신규 매수 차단",
    "watch": "관찰",
    "add": "신규 편입",
}
ACTION_TONE = {
    "trim": "bad",
    "raise_cash": "warn",
    "block_new_buy": "warn",
    "rebalance_to_band": "warn",
    "watch": "neutral",
    "hold": "ok",
    "add": "ok",
}

# ── 행동 사유 코드(reason_codes) ──────────────────────────────────────────
REASON_CODE_LABEL = {
    "PROFILE_INVALID": "투자 성향 설정에 오류가 있어 제안을 멈췄습니다",
    "POSITION_OVER_MAX": "단일 종목 비중이 상한을 넘었습니다",
    "SECTOR_OVER_MAX": "섹터 비중이 상한을 넘었습니다",
    "INDUSTRY_OVER_MAX": "산업 비중이 상한을 넘었습니다",
    "THEME_OVER_MAX": "테마 비중이 상한을 넘었습니다",
    "COUNTRY_OVER_MAX": "국가 비중이 상한을 넘었습니다",
    "CURRENCY_OVER_MAX": "통화 비중이 상한을 넘었습니다",
    "REDUNDANT_GROUP_OVER_MAX": "사실상 같은 자산이 합쳐서 비중 상한을 넘었습니다",
    "CASH_BELOW_BUFFER": "현금 비중이 최소 기준 아래로 내려갔습니다",
    "SLEEVE_OVER_BAND": "자산군 비중이 목표 범위를 위로 벗어났습니다",
    "SLEEVE_UNDER_BAND": "자산군 비중이 목표 범위를 아래로 벗어났습니다",
    "MACRO_DEFENSIVE_REDUCE_CONCENTRATION": "방어적 시장 국면이라 위험을 줄입니다",
    "MACRO_CAUTIOUS_TIGHTEN_RISK": "신중한 시장 국면이라 위험 추가를 제한합니다",
    "QUALITATIVE_RESEARCH_REQUIRED": "매매 전 추가 리서치가 필요합니다",
    "VALUATION_TOO_EXPENSIVE": "적정가 대비 25% 이상 비쌉니다",
    "NO_ACTION_WITHIN_POLICY": "정책 범위 안에 있어 조정이 필요 없습니다",
    "FACTOR_SCORE_SUPPORTS_ADD": "스크리닝 점수가 편입을 뒷받침합니다",
    "TURNOVER_LIMIT": "월 매매 한도에 걸려 규모를 줄였습니다",
    "PORTFOLIO_FIT_BLOCKED": "기존 포트폴리오 비중 제약에 막혔습니다",
    "DISALLOWED_ASSET_TYPE": "투자 성향상 허용되지 않는 자산 유형입니다",
    "LIQUIDITY_BELOW_MINIMUM": "거래량(유동성)이 최소 기준보다 낮습니다",
}

# ── 스크리닝 판정(decision_bucket) ────────────────────────────────────────
BUCKET_LABEL = {
    "candidate": "후보",
    "watch": "관찰",
    "blocked_by_portfolio_fit": "비중 제약",
    "skipped": "제외",
}
BUCKET_DESC = {
    "candidate": "상위권에 들어 신규 편입을 검토할 만한 종목",
    "watch": "점수는 나왔지만 당장 편입 대상은 아닌 종목",
    "blocked_by_portfolio_fit": "점수는 충분하나 기존 비중 한도에 막힌 종목",
    "skipped": "유동성·변동성 등 기본 조건을 통과하지 못한 종목",
}
BUCKET_TONE = {
    "candidate": "ok",
    "watch": "neutral",
    "blocked_by_portfolio_fit": "warn",
    "skipped": "bad",
}

# ── 리스크 게이트(opportunity gate) ──────────────────────────────────────
GATE_LABEL = {"pass": "편입 가능", "warn": "주의", "block": "편입 불가"}
GATE_TONE = {"pass": "ok", "warn": "warn", "block": "bad"}

# ── 인트린식 밸류 등급 ────────────────────────────────────────────────────
GRADE_GROUP_LABEL = {
    "moat": "경제적 해자",
    "tech": "기술 경쟁력",
    "sector": "산업 추세",
    "disruption": "파괴 위험",
}
GRADE_VALUE_LABEL = {
    "wide": "넓음", "narrow": "좁음", "none": "없음",
    "leading": "선도", "parity": "평균", "lagging": "열위",
    "secular_growth": "구조적 성장", "stable": "안정", "declining": "쇠퇴",
    "low": "낮음", "medium": "보통", "high": "높음",
}
GRADE_VALUE_TONE = {
    "wide": "ok", "narrow": "neutral", "none": "bad",
    "leading": "ok", "parity": "neutral", "lagging": "bad",
    "secular_growth": "ok", "stable": "neutral", "declining": "bad",
    # disruption: 높을수록 나쁨
    "low": "ok", "medium": "neutral", "high": "bad",
}
CONFIDENCE_LABEL = {"high": "높음", "medium": "보통", "low": "낮음"}

# ── 익스포저 유형(exposure_type) ──────────────────────────────────────────
EXPOSURE_TYPE_LABEL = {
    "position": "종목",
    "sector": "섹터",
    "industry": "산업",
    "country": "국가",
    "currency": "통화",
    "theme": "테마",
    "redundancy_group": "중복 자산",
}

# ── 설정 화면: 한도 필드 / 거래 모드 ─────────────────────────────────────
LIMIT_FIELD_LABEL = {
    "max_single_position_weight": "단일 종목 한도",
    "max_sector_weight": "섹터 한도",
    "max_industry_weight": "산업 한도",
    "max_theme_weight": "테마 한도",
    "max_country_weight": "국가 한도",
    "max_currency_weight": "통화 한도",
    "max_monthly_turnover": "월 매매 한도",
    "rebalance_band": "리밸런싱 허용 밴드",
}
TRADE_MODE_LABEL = {
    "propose_only": "제안만 (자동 매매 안 함)",
    "approval_required": "승인 후 실행",
}


# 설정 화면 각 항목의 도움말(? 아이콘 툴팁).
FIELD_HELP = {
    "expected_annual_return": "1년에 목표로 하는 평균 수익률입니다. 0.1 = 연 10%.",
    "max_tolerable_drawdown": "고점 대비 견딜 수 있는 최대 하락폭(음수). -0.25 = 최대 -25%까지 감내.",
    "investment_horizon_years": "목표를 향해 투자하는 기간(년).",
    "monthly_contribution": "매달 새로 넣는 금액.",
    "liquidity_buffer_months": "비상시 대비해 현금으로 두는 생활비 개월 수.",
    "max_single_position_weight": "한 종목이 차지할 수 있는 최대 비중. 0.1 = 10%.",
    "max_sector_weight": "한 섹터(예: 기술)에 담을 수 있는 최대 합산 비중.",
    "max_industry_weight": "한 산업에 담을 수 있는 최대 합산 비중.",
    "max_theme_weight": "한 테마(예: AI)에 담을 수 있는 최대 합산 비중.",
    "max_country_weight": "한 국가에 담을 수 있는 최대 합산 비중.",
    "max_currency_weight": "한 통화에 담을 수 있는 최대 합산 비중.",
    "max_monthly_turnover": "한 달에 사고팔 수 있는 포트폴리오 비중 상한(회전율). 0.15 = 월 15%.",
    "rebalance_band": "목표 비중에서 이만큼 벗어나면 리밸런싱을 제안. 0.05 = ±5%p.",
    "trade_mode": "제안만: 추천만 만들고 매매는 직접. 승인 후 실행: 승인한 건만 실행.",
    "sleeve_targets": "자산군(슬리브)별 목표 비중과 허용 범위입니다. 타깃의 합은 1.0이 되어야 합니다.",
}


def field_label(v: str | None) -> str:
    return LIMIT_FIELD_LABEL.get(v or "", _humanize(v or ""))


def field_help(v: str | None) -> str:
    return FIELD_HELP.get(v or "", "")


def trade_mode_label(v: str | None) -> str:
    return TRADE_MODE_LABEL.get(v or "", _humanize(v or ""))


# ── 거래 유형(transaction_type) ──────────────────────────────────────────
TX_TYPE_LABEL = {
    "buy": "매수", "sell": "매도", "deposit": "입금", "withdrawal": "출금",
    "dividend": "배당", "fee": "수수료", "manual_adjustment": "수동 조정",
}


def tx_type_label(v: str | None) -> str:
    return TX_TYPE_LABEL.get(v or "", _humanize(v or ""))


# ── 종목 상세: 팩터 점수 / 지표 이름 ──────────────────────────────────────
FACTOR_SCORE_LABEL = {
    "momentum_score": "모멘텀", "trend_score": "추세(200일선)", "liquidity_score": "유동성",
    "valuation_score": "밸류에이션", "quality_score": "퀄리티", "low_beta_score": "저변동(저베타)",
    "volatility_penalty": "변동성 위험",
    "momentum_1m_pct": "1개월 모멘텀", "momentum_3m_pct": "3개월 모멘텀", "momentum_6m_pct": "6개월 모멘텀",
    "beta_1y": "베타(1년)", "roe": "ROE", "net_margin": "순이익률", "debt_to_equity": "부채/자본",
    "pe_ratio": "PER", "pb_ratio": "PBR", "ev_to_ebitda": "EV/EBITDA",
    "fcf_yield": "FCF 수익률", "price_to_intrinsic": "가격/내재가치",
    "above_200d_ma": "200일선 위", "beta": "베타",
}

# 상세 페이지에 백분위 막대로 보여줄 종합 점수(0~100, 높을수록 좋음; 변동성만 반대)
FACTOR_SCORE_BARS = ["momentum_score", "trend_score", "liquidity_score",
                     "valuation_score", "quality_score", "low_beta_score"]
# 참고용 원시 지표
FACTOR_RAW_METRICS = ["roe", "net_margin", "debt_to_equity", "pe_ratio",
                      "pb_ratio", "ev_to_ebitda", "fcf_yield", "beta_1y"]


def factor_score_label(v: str | None) -> str:
    return FACTOR_SCORE_LABEL.get(v or "", _humanize(v or ""))


# ── 매크로 원자료(raw_indicators) 시리즈 이름 ─────────────────────────────
INDICATOR_LABEL = {
    # 금리·신용
    "T10Y2Y": "장단기 금리차(10Y-2Y)", "DGS10": "미 국채 10년", "DGS2": "미 국채 2년",
    "DFII10": "실질금리(TIPS 10Y)", "EFFR": "연방기금 실효금리",
    "BAMLH0A0HYM2": "하이일드 신용 스프레드", "BAMLC0A0CM": "투자등급 신용 스프레드",
    "RRPONTSYD": "역레포(RRP)", "DRTSCILM": "은행 대출태도",
    # 유동성
    "WALCL": "연준 대차대조표", "WTREGEN": "재무부 일반계정(TGA)", "M2SL": "M2 통화량",
    "NFCI": "시카고 연준 금융여건지수",
    # 성장
    "CFNAI": "시카고 연준 활동지수", "UNRATE": "실업률", "ICSA": "신규 실업수당 청구",
    "RSXFS": "소매판매", "INDPRO": "산업생산", "GDPC1": "실질 GDP",
    "ism_mfg_pmi": "ISM 제조업 PMI", "ism_svc_pmi": "ISM 서비스업 PMI",
    "CES0500000003": "임금 상승률",
    # 물가
    "CPILFESL": "근원 CPI", "PCEPILFE": "근원 PCE", "T5YIE": "5년 기대 인플레이션",
    "DCOILWTICO": "WTI 유가",
    # 시장
    "^VIX": "VIX(변동성)", "^VIX3M": "3개월 VIX", "^GSPC": "S&P 500",
    "DX-Y.NYB": "달러 인덱스(DXY)", "KRW=X": "원/달러 환율",
    "HG=F": "구리 선물", "GC=F": "금 선물", "CL=F": "WTI 선물",
    "copper_gold_ratio": "구리/금 비율",
}


def indicator_label(v: str | None) -> str:
    return INDICATOR_LABEL.get(v or "", v or "")


# ── 정책 슬리브(sleeve) 이름 ──────────────────────────────────────────────
SLEEVE_LABEL = {
    "cash": "현금",
    "core_us_equity": "미국 핵심 주식",
    "core_equity": "핵심 주식",
    "satellite_equity": "위성 주식",
    "defensive_bonds": "방어 채권",
    "bonds": "채권",
    "international_equity": "해외 주식",
    "commodities": "원자재",
    "alternatives": "대체자산",
}


def _humanize(token: str) -> str:
    """매핑에 없는 식별자를 최소한 읽기 좋게: snake_case → 공백, 약어 대문자 유지."""
    return token.replace("_", " ").strip() if token else token


def regime_label(v: str | None) -> str:
    return REGIME_LABEL.get(v or "", v or "—")


def regime_tagline(v: str | None) -> str:
    return REGIME_TAGLINE.get(v or "", "")


def growth_label(v: str | None) -> str:
    return GROWTH_LABEL.get(v or "", v or "—")


def inflation_label(v: str | None) -> str:
    return INFLATION_LABEL.get(v or "", v or "—")


def positioning_label(v: str | None) -> str:
    return POSITIONING_LABEL.get(v or "", v or "—")


def positioning_guidance(v: str | None) -> str:
    return POSITIONING_GUIDANCE.get(v or "", "")


def positioning_tone(v: str | None) -> str:
    return POSITIONING_TONE.get(v or "", "neutral")


def macro_signal_label(code: str | None, indicator: str | None = None) -> str:
    return MACRO_SIGNAL_LABEL.get(code or "", indicator or code or "")


def action_label(v: str | None) -> str:
    return ACTION_LABEL.get(v or "", _humanize(v or ""))


def action_tone(v: str | None) -> str:
    return ACTION_TONE.get(v or "", "neutral")


def reason_code_label(code: str | None) -> str:
    return REASON_CODE_LABEL.get(code or "", _humanize(code or ""))


def reason_codes_label(codes) -> str:
    return " · ".join(reason_code_label(c) for c in (codes or []))


def bucket_label(v: str | None) -> str:
    return BUCKET_LABEL.get(v or "", _humanize(v or ""))


def bucket_desc(v: str | None) -> str:
    return BUCKET_DESC.get(v or "", "")


def bucket_tone(v: str | None) -> str:
    return BUCKET_TONE.get(v or "", "neutral")


def gate_label(v: str | None) -> str:
    return GATE_LABEL.get(v or "", v or "")


def gate_tone(v: str | None) -> str:
    return GATE_TONE.get(v or "", "neutral")


def grade_group_label(v: str | None) -> str:
    return GRADE_GROUP_LABEL.get(v or "", _humanize(v or ""))


def grade_value_label(v: str | None) -> str:
    return GRADE_VALUE_LABEL.get(v or "", v or "")


def grade_value_tone(v: str | None) -> str:
    return GRADE_VALUE_TONE.get(v or "", "neutral")


def confidence_label(v: str | None) -> str:
    return CONFIDENCE_LABEL.get(v or "", v or "—")


def exposure_type_label(v: str | None) -> str:
    return EXPOSURE_TYPE_LABEL.get(v or "", _humanize(v or ""))


def sleeve_label(v: str | None) -> str:
    return SLEEVE_LABEL.get(v or "", _humanize(v or ""))


# Jinja 환경에 등록할 필터 모음.
JINJA_FILTERS = {
    "regime_label": regime_label,
    "regime_tagline": regime_tagline,
    "growth_label": growth_label,
    "inflation_label": inflation_label,
    "positioning_label": positioning_label,
    "positioning_guidance": positioning_guidance,
    "positioning_tone": positioning_tone,
    "macro_signal_label": macro_signal_label,
    "action_label": action_label,
    "action_tone": action_tone,
    "reason_code_label": reason_code_label,
    "reason_codes_label": reason_codes_label,
    "bucket_label": bucket_label,
    "bucket_desc": bucket_desc,
    "bucket_tone": bucket_tone,
    "gate_label": gate_label,
    "gate_tone": gate_tone,
    "grade_group_label": grade_group_label,
    "grade_value_label": grade_value_label,
    "grade_value_tone": grade_value_tone,
    "confidence_label": confidence_label,
    "exposure_type_label": exposure_type_label,
    "sleeve_label": sleeve_label,
    "field_label": field_label,
    "field_help": field_help,
    "trade_mode_label": trade_mode_label,
    "tx_type_label": tx_type_label,
    "factor_score_label": factor_score_label,
    "indicator_label": indicator_label,
}
