"""프로덕션 투표 함수를 point-in-time 뷰로 월별 소급 실행해 레짐 라벨 생성.

투표 규칙 자체는 croesus.macro.indicators의 순수 함수를 그대로 import한다
(파라미터 튜닝 없음 = 프로덕션 충실). `with_yoy_inflation`은 레벨 퇴화
(CPI/PCE/임금 지수 레벨의 3개월 기울기는 98~99% 양수 → 인플레 방향이
사실상 항상 "Rising") 를 보정하는 대안 라벨용 변환이다.
"""
from __future__ import annotations

import pandas as pd

from croesus.macro.indicators.growth import compute_growth_direction
from croesus.macro.indicators.inflation import compute_inflation_direction
from experiments.market_signals.regime_conditional.fred import LAG_DAYS, as_of_view

YOY_SERIES = ["CPILFESL", "PCEPILFE", "CES0500000003"]


def classify_regime(growth: str, inflation: str) -> str:
    # croesus.macro.engine._classify_regime과 동일 매핑(테스트로 동치 보증)
    if growth == "Expanding" and inflation == "Falling":
        return "Goldilocks"
    if growth == "Expanding" and inflation == "Rising":
        return "Reflation"
    if growth == "Contracting" and inflation == "Rising":
        return "Stagflation"
    return "Deflation"


def with_yoy_inflation(raw: dict[str, pd.Series]) -> dict[str, pd.Series]:
    out = dict(raw)
    for code in YOY_SERIES:
        if code in out:
            out[code] = (out[code].pct_change(12) * 100).dropna()
    return out


def monthly_regimes(raw: dict[str, pd.Series], dates,
                    lags: dict[str, int] = LAG_DAYS) -> pd.DataFrame:
    rows = []
    for d in dates:
        view = as_of_view(raw, pd.Timestamp(d), lags)
        g, gc = compute_growth_direction(view)
        i, ic = compute_inflation_direction(view)
        rows.append({"date": pd.Timestamp(d), "growth": g, "inflation": i,
                     "regime": classify_regime(g, i),
                     "growth_conf": gc, "inflation_conf": ic})
    return pd.DataFrame(rows)


def run_length_summary(labels: pd.Series) -> pd.DataFrame:
    lab = labels.reset_index(drop=True)
    run_id = (lab != lab.shift()).cumsum()
    runs = lab.groupby(run_id).agg(["first", "size"])
    out = runs.groupby("first")["size"].agg(n_runs="count", avg_run_len="mean", n_months="sum")
    out["share"] = out["n_months"] / len(lab)
    return out.reset_index().rename(columns={"first": "regime"})


def transition_matrix(labels: pd.Series) -> pd.DataFrame:
    lab = labels.reset_index(drop=True)
    prev, nxt = lab.shift(), lab
    mask = prev.notna() & (prev != nxt)
    return pd.crosstab(prev[mask], nxt[mask]).rename_axis(index="from", columns="to")
