# Valuation Analysis Layer — Design Spec

**Date:** 2026-05-28  
**Status:** Approved  
**Sprint:** 003 (planned)

---

## 1. Background

Current Croesus factor pipeline computes only price-based technical factors (momentum, volatility, liquidity, 200-day MA). No fundamental or valuation analysis exists.

This spec defines the **Valuation Analysis Layer**: deterministic relative and absolute valuation for individual equities, integrated into the existing Factor Engine and Screening Engine with no breaking changes.

---

## 2. Goals

- **Relative valuation** — compute P/E, P/B, EV/EBITDA, FCF yield; rank each metric as a percentile within the same sector.
- **Absolute valuation** — 2-stage DCF producing intrinsic value per share and upside percentage.
- **Screening integration** — all valuation outputs land in `factor_values` as scalar floats; the Screening Engine reads them with zero changes.
- **Data source abstraction** — yfinance for MVP; interface designed for drop-in replacement (FMP, Alpha Vantage, etc.).
- **LLM extensibility** — DCF assumptions are recorded in `assumptions_json`; a future LLM layer can override them via a single `overrides: dict` parameter.

---

## 3. Architecture

```
yfinance (MVP) / FMP, Alpha Vantage (future)
        ↓
FundamentalsProvider  ←  abstraction interface
        ↓
fundamentals table  (raw financials: revenue, FCF, debt, …)
        ↓
equity/valuation.py  (Factor Engine module)
        ↓
┌──────────────────────────────────────────────┐
│ factor_values  (screening-ready scalar facts) │
│  pe_ratio, pb_ratio, ev_to_ebitda, fcf_yield  │
│  pe_vs_sector_pct, pb_vs_sector_pct           │
│  ev_ebitda_vs_sector_pct, price_to_intrinsic  │
└──────────────────────────────────────────────┘
┌──────────────────────────────────────────────┐
│ valuation_snapshots  (DCF detail record)      │
│  intrinsic_value_per_share, upside_pct        │
│  wacc, fcf_growth_rate, assumptions_json      │
└──────────────────────────────────────────────┘
        ↓                        ↓
Screening Engine          Research Agent
(reads factor_values,     (reads valuation_snapshots
 no changes needed)        for deep-dive reports)
```

**Key invariant:** The Screening Engine is unmodified. It reads `factor_values` as before; valuation metrics appear there as new `factor_name` rows.

---

## 4. New Tables

### 4.1 `fundamentals`

Long-format raw financial data, consistent with the existing `factor_values` design philosophy.

```sql
CREATE TABLE IF NOT EXISTS fundamentals (
  asset_id     TEXT NOT NULL,
  period_end   DATE NOT NULL,      -- fiscal period end date (e.g. 2024-12-31)
  period_type  TEXT NOT NULL,      -- 'annual' | 'quarterly'
  metric_name  TEXT NOT NULL,
  value        DOUBLE,
  source       TEXT,
  PRIMARY KEY (asset_id, period_end, period_type, metric_name)
);
```

Initial `metric_name` values:
`revenue`, `operating_income`, `net_income`, `eps`, `free_cash_flow`,
`total_debt`, `total_equity`, `cash_and_equivalents`, `shares_outstanding`,
`ebitda`, `capex`, `book_value_per_share`

`cash_and_equivalents` is required for enterprise value: `EV = market_cap + total_debt − cash`.
`eps` is the most recent annual figure from yfinance; TTM from quarterly sum is a future improvement.

### 4.2 `valuation_snapshots`

One row per asset per date. Stores DCF inputs and outputs together so any run is fully reproducible.

```sql
CREATE TABLE IF NOT EXISTS valuation_snapshots (
  asset_id                  TEXT NOT NULL,
  date                      DATE NOT NULL,
  intrinsic_value_per_share DOUBLE,
  current_price             DOUBLE,
  upside_pct                DOUBLE,       -- (intrinsic - current) / current
  wacc                      DOUBLE,
  fcf_growth_rate           DOUBLE,
  terminal_growth_rate      DOUBLE,
  assumptions_json          TEXT,         -- JSON; LLM override extension point
  PRIMARY KEY (asset_id, date)
);
```

`assumptions_json` example:

```json
{
  "wacc": 0.094,
  "fcf_growth_rate": 0.08,
  "terminal_growth_rate": 0.025,
  "beta": 1.12,
  "risk_free_rate": 0.045,
  "source": "capm_auto"
}
```

Future LLM override: `"source": "llm_override"` with user-supplied growth and discount assumptions.

### 4.3 New `factor_name` rows in `factor_values`

No schema change required.

| `factor_name` | Description | Screening use |
|---|---|---|
| `pe_ratio` | Price / EPS (most recent annual) | Absolute filter |
| `pb_ratio` | Price / Book value per share | Absolute filter |
| `ev_to_ebitda` | Enterprise value / EBITDA | Absolute filter |
| `fcf_yield` | FCF / Market cap | Higher = better |
| `pe_vs_sector_pct` | PE percentile within sector (0 = cheapest) | Relative ranking |
| `pb_vs_sector_pct` | PB percentile within sector | Relative ranking |
| `ev_ebitda_vs_sector_pct` | EV/EBITDA percentile within sector | Relative ranking |
| `price_to_intrinsic` | Current price / DCF intrinsic value (< 1 = undervalued) | Absolute value ranking |

---

## 5. Module Layout

```
croesus/
  data_sources/
    fundamentals/
      base.py                   # FundamentalsProvider ABC
      yfinance_fundamentals.py  # yfinance implementation

  fundamentals/
    ingest_fundamentals.py      # fetch → normalize → write to fundamentals table
    repository.py               # read helpers (get_annual_fcf, get_latest_metric, …)

  factors/
    equity/
      valuation.py              # compute_valuation_factors() entry point

  jobs/
    daily_run.py                # MODIFIED: add daily multiples refresh
    quarterly_run.py            # NEW: fetch financials + recompute DCF
```

---

## 6. `FundamentalsProvider` Interface

```python
# data_sources/fundamentals/base.py

class FundamentalsProvider(ABC):
    @abstractmethod
    def get_financials(self, symbol: str) -> dict[str, pd.DataFrame]:
        """
        Returns:
          {
            "income_annual":    DataFrame,
            "income_quarterly": DataFrame,
            "balance_annual":   DataFrame,
            "cashflow_annual":  DataFrame,
          }
        Each DataFrame has fiscal period-end dates as columns and metric names as index.
        """
```

`YFinanceFundamentalsProvider` implements this interface. Swapping to FMP requires only a new implementation file; all downstream code is unchanged.

---

## 7. `equity/valuation.py` — Computation Flow

```
compute_valuation_factors(asset_id, date, conn)
  │
  ├─ load fundamentals from DB
  ├─ load current price + market cap from prices_daily
  │
  ├─ compute_multiples()
  │    → pe_ratio, pb_ratio, ev_to_ebitda, fcf_yield
  │
  ├─ compute_sector_percentiles()
  │    → join assets table on sector
  │    → rank each multiple within sector peers
  │    → pe_vs_sector_pct, pb_vs_sector_pct, ev_ebitda_vs_sector_pct
  │
  ├─ compute_dcf(overrides=None)
  │    ├─ compute_wacc()          CAPM: Rf + β × 5.5%
  │    ├─ compute_fcf_growth()    CAGR of last 5yr FCF, clipped to [-5%, +30%]
  │    ├─ 2-stage DCF formula
  │    └─ returns DcfResult(intrinsic_value_per_share, wacc, growth_rate, …)
  │
  ├─ write scalar factors → factor_values
  └─ write DCF detail     → valuation_snapshots
```

---

## 8. DCF Methodology

### WACC (MVP: all-equity, CAPM)

```
WACC = Risk-Free Rate + Beta × 5.5%

Risk-Free Rate  = 10Y US Treasury yield from macro_scores (fallback: 4.5%)
Beta            = 2-year daily return regression vs SPY
                  fallback: sector median beta, then 1.0
```

Full debt-weighted WACC is out of scope for MVP.

### FCF Growth Rate

```
1. Load last 5 annual FCF values from fundamentals
2. Skip DCF if fewer than 3 valid years
3. CAGR = (FCF_latest / FCF_oldest) ^ (1 / n) − 1
4. Clip to [−5%, +30%]
5. terminal_growth_rate = min(fcf_growth_rate, 2.5%)
```

### 2-Stage Formula

```
Stage 1 — explicit 5-year forecast:
  FCF_t = FCF_base × (1 + g)^t   for t = 1…5

Stage 2 — terminal value (Gordon Growth):
  TV = FCF_5 × (1 + g_term) / (WACC − g_term)

Intrinsic value = Σ FCF_t / (1+WACC)^t  +  TV / (1+WACC)^5

Per-share intrinsic value = Intrinsic value / shares_outstanding
```

### LLM Extension Point

```python
def compute_dcf(
    asset_id: str,
    date: date,
    conn,
    overrides: dict | None = None,  # future: LLM supplies {"wacc": 0.09, …}
) -> DcfResult:
    params = _build_capm_params(asset_id, date, conn)
    if overrides:
        params.update(overrides)
    ...
```

`assumptions_json` records `"source": "capm_auto"` or `"source": "llm_override"` for full traceability.

---

## 9. Data Gap Handling

| Condition | Behavior |
|---|---|
| FCF valid years < 3 | Skip DCF; store `price_to_intrinsic = NULL` |
| FCF negative for all years | Skip DCF; log reason |
| Beta regression data < 2yr | Use sector median beta; fallback to 1.0 |
| WACC ≤ terminal growth rate | Skip DCF (formula diverges); log reason |
| Missing multiple denominator (EPS = 0, etc.) | Store `NULL` for that factor |

All skips are logged clearly. A single asset failure must not crash the full run.

---

## 10. Job Schedule

| Job | Frequency | Responsibility |
|---|---|---|
| `daily_run` | Daily | Refresh price-based multiples (pe_ratio, pb_ratio, ev_to_ebitda, fcf_yield, sector percentiles, price_to_intrinsic) using latest price + cached fundamentals |
| `quarterly_run` | Quarterly | Fetch fresh financials via FundamentalsProvider → update fundamentals table → recompute DCF → update valuation_snapshots |

Financials change quarterly; multiples change daily as price moves.

---

## 11. Out of Scope

- Debt-weighted WACC
- DDM (Dividend Discount Model)
- Reverse DCF
- Multi-scenario (bull/base/bear) DCF
- LLM-supplied DCF assumptions (extension point is designed; implementation is future work)
- Non-equity asset valuation (ETFs, bonds, crypto)
- Paid data source integration
