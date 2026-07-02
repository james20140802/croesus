"""FRED 공개 CSV 수집 + point-in-time(발표 시차) 뷰.

fredgraph.csv 엔드포인트는 API 키가 필요 없다. 관측일(observation_date)은
월/분기 시계열의 경우 '기간 시작일'이므로, LAG_DAYS는 관측일로부터 실제 발표
(이용 가능)일까지의 보수적 오프셋(달력일)이다: 기간 길이 + 발표 지연.
"""
from __future__ import annotations

import io
import urllib.request
from pathlib import Path

import pandas as pd

FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={code}"

GROWTH_SERIES = ["CFNAI", "UNRATE", "ICSA", "RSXFS", "INDPRO", "GDPC1"]
INFLATION_SERIES = ["CPILFESL", "PCEPILFE", "T5YIE", "DCOILWTICO", "CES0500000003"]
ALL_SERIES = GROWTH_SERIES + INFLATION_SERIES

LAG_DAYS = {
    "CFNAI": 55, "UNRATE": 40, "ICSA": 7, "RSXFS": 47, "INDPRO": 47, "GDPC1": 121,
    "CPILFESL": 45, "PCEPILFE": 60, "T5YIE": 1, "DCOILWTICO": 3, "CES0500000003": 40,
}


def parse_fredgraph(text: str) -> pd.Series:
    df = pd.read_csv(io.StringIO(text), na_values=["."])
    df.columns = ["date", "value"]
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")["value"].astype(float).dropna()


def fetch_series(code: str, cache_dir: Path) -> pd.Series:
    cache_dir.mkdir(parents=True, exist_ok=True)
    f = cache_dir / f"{code}.csv"
    if not f.exists():
        with urllib.request.urlopen(FRED_URL.format(code=code), timeout=60) as r:
            f.write_bytes(r.read())
    return parse_fredgraph(f.read_text())


def load_all(cache_dir: Path) -> dict[str, pd.Series]:
    return {c: fetch_series(c, cache_dir) for c in ALL_SERIES}


def as_of_view(raw: dict[str, pd.Series], as_of: pd.Timestamp,
               lags: dict[str, int] = LAG_DAYS) -> dict[str, pd.Series]:
    out: dict[str, pd.Series] = {}
    for code, s in raw.items():
        cutoff = as_of - pd.Timedelta(days=lags.get(code, 60))
        v = s[s.index <= cutoff]
        if len(v):
            out[code] = v
    return out
