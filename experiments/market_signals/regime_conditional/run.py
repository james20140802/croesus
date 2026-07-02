"""로드맵 ④ orchestration — 레짐 라벨 소급 계산 + 팩터 롱숏 레짐 조건부 분해.

Run from repo root:
  python3 -m experiments.market_signals.regime_conditional.run
Env:
  RC_FACTORS=momentum_6m  검증할 팩터 제한(콤마 구분; 기본 7종 전체)
"""
from __future__ import annotations

import os

import pandas as pd

from experiments.market_signals.common.config import RESULTS_DIR
from experiments.market_signals.cross_sectional.history import load_long_history
from experiments.market_signals.regime_conditional.conditional import (
    join_regime,
    post_change_table,
    regime_table,
    shift_placebo,
)
from experiments.market_signals.regime_conditional.fred import load_all
from experiments.market_signals.regime_conditional.regimes import (
    monthly_regimes,
    run_length_summary,
    transition_matrix,
    with_yoy_inflation,
)
from experiments.market_signals.vol_targeting.data import equal_weight_returns

OUT = RESULTS_DIR / "regime_conditional"
PERDATE_DIR = RESULTS_DIR / "cross_sectional_long"
FACTORS = ["momentum_1m", "momentum_3m", "momentum_6m", "volatility_3m",
           "liquidity_1m", "above_200d_ma", "beta_1y"]
GRID = pd.date_range("1990-01-31", "2026-06-30", freq="M")
# sanity check용 — 워크트리에는 프로덕션 DB가 없으므로 메인 체크아웃 경로 폴백(읽기 전용)
PROD_DB_CANDIDATES = ["storage/croesus.duckdb",
                      "/Users/drchasekim/Developer/croesus/storage/croesus.duckdb"]


def _factors() -> list[str]:
    env = os.environ.get("RC_FACTORS", "")
    return [f.strip() for f in env.split(",") if f.strip()] or FACTORS


def _load_perdate(factor: str) -> pd.DataFrame:
    df = pd.read_csv(PERDATE_DIR / f"perdate_{factor}_21.csv", parse_dates=["date"])
    return df[["date", "ls"]]


def _market_monthly_forward() -> pd.DataFrame:
    prices = load_long_history(start_year=1990)
    daily = equal_weight_returns(prices, min_names=30)
    mret = (1.0 + daily).resample("M").prod() - 1.0
    fwd = mret.shift(-1).dropna()  # 월말 d 시점에 알려진 라벨 → 다음 달 시장 수익
    return pd.DataFrame({"date": fwd.index, "ls": fwd.values})


def _prod_sanity(regimes_prod: pd.DataFrame) -> None:
    import duckdb
    ms = None
    for path in PROD_DB_CANDIDATES:
        try:
            con = duckdb.connect(path, read_only=True)
            ms = con.execute("SELECT date, regime FROM macro_scores ORDER BY date").df()
            con.close()
            break
        except Exception as exc:  # 웹앱이 락을 잡고 있을 수 있음 — 검증은 선택 사항
            print(f"[rc] prod sanity: {path} 사용 불가 ({str(exc).splitlines()[0]})", flush=True)
    if ms is None:
        return
    if ms.empty:
        print("[rc] prod sanity skipped: macro_scores empty", flush=True)
        return
    prod_mode = ms["regime"].mode().iloc[0]
    ours = regimes_prod.iloc[-1]
    print(f"[rc] prod sanity: macro_scores {ms['date'].min()}..{ms['date'].max()} "
          f"mode={prod_mode} (n={len(ms)}) vs retro {ours['date'].date()}={ours['regime']}",
          flush=True)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    raw = load_all(OUT / "fred_cache")
    for code, s in sorted(raw.items()):
        print(f"[rc] FRED {code}: {len(s)} obs, {s.index.min().date()}..{s.index.max().date()}",
              flush=True)

    variants = {"prod": raw, "yoy": with_yoy_inflation(raw)}
    regimes: dict[str, pd.DataFrame] = {}
    summaries = []
    for name, r in variants.items():
        reg = monthly_regimes(r, GRID)
        reg.to_csv(OUT / f"regimes_{name}.csv", index=False)
        regimes[name] = reg
        rl = run_length_summary(reg["regime"])
        rl.insert(0, "variant", name)
        summaries.append(rl)
        transition_matrix(reg["regime"]).to_csv(OUT / f"transitions_{name}.csv")
        print(f"[rc] regimes/{name}:\n{rl.round(3).to_string(index=False)}", flush=True)
    summary = pd.concat(summaries, ignore_index=True)
    summary.to_csv(OUT / "regime_summary.csv", index=False)

    _prod_sanity(regimes["prod"])

    factors = _factors()
    t_rows, p_rows, c_rows = [], [], []
    for name, reg in regimes.items():
        for factor in factors:
            joined = join_regime(_load_perdate(factor), reg)
            tbl = regime_table(joined)
            tbl.insert(0, "factor", factor)
            tbl.insert(0, "variant", name)
            t_rows.append(tbl)
            obs, p = shift_placebo(joined["ls"].to_numpy(), joined["regime"].to_numpy())
            p_rows.append({"variant": name, "factor": factor, "between_stat": obs,
                           "p_shift": p, "n": len(joined)})
            pc = post_change_table(joined)
            pc.insert(0, "factor", factor)
            pc.insert(0, "variant", name)
            c_rows.append(pc)
    fr_tbl = pd.concat(t_rows, ignore_index=True)
    fr_tbl.to_csv(OUT / "factor_regime_table.csv", index=False)
    placebo = pd.DataFrame(p_rows)
    placebo.to_csv(OUT / "placebo.csv", index=False)
    pd.concat(c_rows, ignore_index=True).to_csv(OUT / "post_change.csv", index=False)

    for name in regimes:
        sub = fr_tbl[fr_tbl["variant"] == name]
        piv = sub.pivot(index="factor", columns="regime", values="sharpe")
        print(f"[rc] regime x factor Sharpe ({name}):\n{piv.round(2).to_string()}", flush=True)
    print(f"[rc] shift placebo:\n{placebo.round(4).to_string(index=False)}", flush=True)

    mkt = _market_monthly_forward()
    m_rows = []
    for name, reg in regimes.items():
        joined = join_regime(mkt, reg)
        tbl = regime_table(joined)
        obs, p = shift_placebo(joined["ls"].to_numpy(), joined["regime"].to_numpy())
        tbl.insert(0, "variant", name)
        tbl["p_shift_all"] = p
        m_rows.append(tbl)
    mtbl = pd.concat(m_rows, ignore_index=True)
    mtbl.to_csv(OUT / "market_by_regime.csv", index=False)
    print(f"[rc] market (EW, 다음 달) by regime:\n{mtbl.round(4).to_string(index=False)}",
          flush=True)
    print(f"[rc] wrote results to {OUT}", flush=True)


if __name__ == "__main__":
    main()
