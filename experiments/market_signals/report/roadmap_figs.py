"""로드맵 ①~④ 종합 보고서용 차트 생성.

Run from repo root:
  python3 -m experiments.market_signals.report.roadmap_figs
Outputs: results/roadmap_report/fig/*.png (gitignore)
"""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from experiments.market_signals.common.config import RESULTS_DIR

OUT = RESULTS_DIR / "roadmap_report" / "fig"
CS = RESULTS_DIR / "cross_sectional_long"
VT = RESULTS_DIR / "vol_targeting"
ED = RESULTS_DIR / "event_drift"
RC = RESULTS_DIR / "regime_conditional"

FACTOR_ORDER = ["momentum_1m", "momentum_3m", "momentum_6m", "above_200d_ma",
                "volatility_3m", "beta_1y", "liquidity_1m"]
REGIME_ORDER = ["Goldilocks", "Reflation", "Stagflation", "Deflation"]
REGIME_COLOR = {"Goldilocks": "#4c9f70", "Reflation": "#e8a33d",
                "Stagflation": "#c0504d", "Deflation": "#5b7fb4"}


def _setup_fonts() -> None:
    names = {f.name for f in fm.fontManager.ttflist}
    for cand in ["Apple SD Gothic Neo", "AppleGothic", "NanumGothic", "Noto Sans CJK KR"]:
        if cand in names:
            plt.rcParams["font.family"] = cand
            break
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["figure.dpi"] = 150
    plt.rcParams["savefig.bbox"] = "tight"


def fig1_ic_tstats() -> None:
    ic = pd.read_csv(CS / "ic_summary.csv")
    horizons = [21, 63, 126]
    x = np.arange(len(FACTOR_ORDER))
    width = 0.26
    fig, ax = plt.subplots(figsize=(9, 4.2))
    for i, h in enumerate(horizons):
        sub = ic[ic["h"] == h].set_index("factor").reindex(FACTOR_ORDER)
        ax.bar(x + (i - 1) * width, sub["t_nw"], width, label=f"h={h}일",
               color=["#5b7fb4", "#4c9f70", "#e8a33d"][i], alpha=0.9)
    ax.axhline(0, color="black", lw=0.8)
    for y in (2, -2):
        ax.axhline(y, color="crimson", lw=0.8, ls="--")
    ax.text(len(FACTOR_ORDER) - 0.5, 2.15, "유의성 경계 |t|=2", color="crimson", fontsize=8)
    ax.set_xticks(x, FACTOR_ORDER, rotation=20, ha="right")
    ax.set_ylabel("IC t-stat (Newey-West)")
    ax.set_title("① 팩터별 예측력 t-통계량 — 30년(1995~2026), 367개 월별 단면")
    ax.legend(frameon=False)
    fig.savefig(OUT / "f1_ic_tstats.png")
    plt.close(fig)


def fig1b_survivorship() -> None:
    base = pd.read_csv(CS / "survivorship_sensitivity.csv")
    harsh = pd.read_csv(CS / "survivorship_sensitivity_harsh.csv")
    fig, ax = plt.subplots(figsize=(8, 4.2))
    colors = {"volatility_3m": "#c0504d", "beta_1y": "#e8a33d", "liquidity_1m": "#5b7fb4"}
    for sig, col in colors.items():
        b = base[base["signal"] == sig].sort_values("annual_delist_rate")
        h = harsh[harsh["signal"] == sig].sort_values("annual_delist_rate")
        ax.plot(b["annual_delist_rate"] * 100, b["ls_sharpe"], "o-", color=col,
                label=f"{sig} (완만한 가정)")
        ax.plot(h["annual_delist_rate"] * 100, h["ls_sharpe"], "s--", color=col, alpha=0.7,
                label=f"{sig} (가혹한 가정)")
    ax.axhline(0, color="black", lw=0.8)
    ax.axvline(4, color="gray", lw=0.8, ls=":")
    ax.text(4.1, ax.get_ylim()[0] + 0.1, "현실적 부실 상장폐지율 ≈ 연 4%", fontsize=8, color="gray")
    ax.set_xlabel("가정한 연간 부실 상장폐지율 (%)")
    ax.set_ylabel("롱숏 Sharpe")
    ax.set_title("① 생존편향 민감도 — 상장폐지를 가상 주입하면 '프리미엄'이 사라지거나 뒤집힌다")
    ax.legend(frameon=False, fontsize=8)
    fig.savefig(OUT / "f1b_survivorship.png")
    plt.close(fig)


def fig2_vol_targeting() -> None:
    curve = pd.read_csv(VT / "curve_spy_cap1_c10.csv", parse_dates=["date"]).set_index("date")
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 6), sharex=True,
                                   gridspec_kw={"height_ratios": [2, 1]})
    labels = {"bnh": ("그냥 보유 (SPY)", "#888888"),
              "garch": ("변동성 타게팅 (GARCH)", "#c0504d"),
              "oracle": ("이론적 상한 (oracle)", "#4c9f70")}
    for colname, (lab, col) in labels.items():
        cum = (1 + curve[colname]).cumprod()
        ax1.plot(cum.index, cum, label=lab, color=col, lw=1.2,
                 ls="--" if colname == "oracle" else "-")
        dd = cum / cum.cummax() - 1
        ax2.plot(dd.index, dd * 100, color=col, lw=1.0,
                 ls="--" if colname == "oracle" else "-")
    ax1.set_yscale("log")
    ax1.set_ylabel("누적 자산 (로그, 1=시작)")
    ax1.set_title("② 변동성 타게팅 — SPY, 무레버리지(cap=1), 거래비용 10bps 차감 후")
    ax1.legend(frameon=False)
    ax2.set_ylabel("드로다운 (%)")
    ax2.axhline(0, color="black", lw=0.6)
    fig.savefig(OUT / "f2_vol_targeting.png")
    plt.close(fig)


def fig3_caar() -> None:
    fig, ax = plt.subplots(figsize=(8.5, 4.4))
    series = [("caar_abnormal_volume_up.csv", "거래량 급증 (n=105,809)", "#4c9f70"),
              ("caar_abnormal_return_up.csv", "가격 급등 +3σ (n=20,868)", "#e8a33d"),
              ("caar_abnormal_return_down.csv", "가격 급락 -3σ (n=19,337)", "#c0504d")]
    for fname, lab, col in series:
        df = pd.read_csv(ED / fname)
        ax.plot(df["h"], df["caar"] * 100, color=col, lw=1.5, label=lab)
    pl = pd.read_csv(ED / "caar_abnormal_volume_up.csv")
    ax.plot(pl["h"], pl["placebo_caar"] * 100, color="#4c9f70", lw=1.0, ls=":",
            label="placebo (무작위 날짜)")
    ax.axhline(0, color="black", lw=0.8)
    ax.set_xlabel("이벤트 후 경과 거래일 (h)")
    ax.set_ylabel("누적 초과수익 CAAR (%)")
    ax.set_title("③ 이벤트 후 표류(drift) — 시장조정 누적초과수익, 30년·14.6만 이벤트")
    ax.legend(frameon=False, fontsize=9)
    fig.savefig(OUT / "f3_caar.png")
    plt.close(fig)


def fig3b_cost() -> None:
    pf = pd.read_csv(ED / "portfolio.csv")
    pf = pf[(pf["book"] == "abnormal_volume") | (pf["hold"] == 21)]
    rows = [("abnormal_volume", 5), ("abnormal_volume", 21), ("abnormal_return", 21)]
    names = ["거래량 북\n(5일 보유)", "거래량 북\n(21일 보유)", "가격 롱숏 북\n(21일 보유)"]
    x = np.arange(len(rows))
    fig, ax = plt.subplots(figsize=(7, 4))
    for i, cost in enumerate([0.0, 10.0]):
        vals = [pf[(pf["book"] == b) & (pf["hold"] == h) & (pf["cost_bps"] == cost)]
                ["sharpe"].iloc[0] for b, h in rows]
        ax.bar(x + (i - 0.5) * 0.36, vals, 0.36,
               label=f"비용 {int(cost)}bps", color=["#5b7fb4", "#c0504d"][i])
    ax.axhline(0, color="black", lw=0.8)
    ax.set_xticks(x, names)
    ax.set_ylabel("Sharpe")
    ax.set_title("③ 거래비용 하나로 전멸하는 이벤트 포트폴리오")
    ax.legend(frameon=False)
    fig.savefig(OUT / "f3b_cost.png")
    plt.close(fig)


def fig4_regime_dist() -> None:
    rs = pd.read_csv(RC / "regime_summary.csv")
    fig, ax = plt.subplots(figsize=(8.5, 2.8))
    for row, variant in enumerate(["prod", "yoy"]):
        sub = rs[rs["variant"] == variant].set_index("regime").reindex(REGIME_ORDER)
        left = 0.0
        for reg in REGIME_ORDER:
            share = float(sub.loc[reg, "share"]) if not pd.isna(sub.loc[reg, "share"]) else 0.0
            ax.barh(row, share * 100, left=left, color=REGIME_COLOR[reg],
                    label=reg if row == 1 else None)
            if share > 0.04:
                ax.text(left + share * 50, row, f"{share:.0%}", va="center",
                        ha="center", fontsize=9, color="white", fontweight="bold")
            left += share * 100
    ax.set_yticks([0, 1], ["프로덕션 규칙 그대로\n(레벨 기울기)", "YoY 보정\n(변화율 기울기)"])
    ax.set_xlabel("437개월(1990~2026) 중 비중 (%)")
    ax.set_title("④ 레짐 분포 — 프로덕션 인플레이션 투표는 퇴화해 있다")
    ax.legend(frameon=False, ncol=4, fontsize=8, loc="upper center",
              bbox_to_anchor=(0.5, -0.32))
    fig.savefig(OUT / "f4_regime_dist.png")
    plt.close(fig)


def fig4b_heatmap() -> None:
    fr = pd.read_csv(RC / "factor_regime_table.csv")
    sub = fr[fr["variant"] == "yoy"].pivot(index="factor", columns="regime", values="sharpe")
    sub = sub.reindex(index=FACTOR_ORDER, columns=REGIME_ORDER)
    fig, ax = plt.subplots(figsize=(7.5, 4.4))
    im = ax.imshow(sub.values, cmap="RdYlGn", vmin=-1.4, vmax=1.4, aspect="auto")
    ax.set_xticks(range(len(REGIME_ORDER)), REGIME_ORDER)
    ax.set_yticks(range(len(FACTOR_ORDER)), FACTOR_ORDER)
    for i in range(sub.shape[0]):
        for j in range(sub.shape[1]):
            ax.text(j, i, f"{sub.values[i, j]:+.2f}", ha="center", va="center", fontsize=9)
    ax.set_title("④ 레짐 × 팩터 롱숏 Sharpe (YoY 보정 라벨, 1995~2026)")
    fig.colorbar(im, ax=ax, shrink=0.85, label="연환산 Sharpe")
    fig.savefig(OUT / "f4b_heatmap.png")
    plt.close(fig)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    _setup_fonts()
    fig1_ic_tstats()
    fig1b_survivorship()
    fig2_vol_targeting()
    fig3_caar()
    fig3b_cost()
    fig4_regime_dist()
    fig4b_heatmap()
    print(f"[report] wrote figures to {OUT}")


if __name__ == "__main__":
    main()
