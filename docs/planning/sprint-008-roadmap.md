# Sprint 008+ Roadmap — Closing the Product Gap

This roadmap supersedes the earlier one-line plans for sprints 008–010. It was
re-planned after a full audit (2026-06-11) found three gaps between the
system-overview promise and the implementation:

1. **Asset-type consistency**: the profile accepted 7+ asset types but only US
   equities were priced/factored; KRW cash was silently valued 1:1 to USD and
   bond ETFs could never reach the `defensive_bonds` sleeve.
2. **Screening opacity**: dimension sub-scores are computed then discarded; the
   8 valuation factors from Sprint 007 are never read by the screening score
   (ADR 0005's "auto-integration via factor_values" claim was wrong —
   `FACTOR_NAMES` is a hardcoded tuple).
3. **Missing product surface**: Research Agent and approval gate had zero code,
   the universe was ~15 tickers, and recorded transactions did not feed the
   next snapshot.

## What the finished product presents to the user

1. **Integrity-guaranteed tracking** — every held asset type (US/international
   equity, ETF/bond ETF, crypto, multi-currency cash) marked to market daily;
   data gaps surface as a DEGRADED status + report-leading block, never a
   silent fallback. *(Sprint 008a — shipped)*
2. **Real-scale screening** — S&P 500 + NASDAQ-100 (~600 names) with a
   regime-aware composite score *and* a per-dimension breakdown table
   (momentum / liquidity / volatility / valuation + raw multiples) so "why this
   name" is transparent. *(008b, 008c)*
3. **Valuation in decisions** — cheap names earn `valuation_score`; expensive
   names carry `VALUATION_TOO_EXPENSIVE` / OVERVALUED reason codes on
   proposals. *(008b)*
4. **Transactions → automatic state** — `record_transaction` once; the next
   snapshot derives holdings without a CSV; a price-history backfill makes
   1m/3m/6m/1y returns appear immediately. *(009)*
5. **Local-LLM research notes** — every new-buy candidate gets a business /
   catalysts / risks note from a local model (Ollama by default, any
   OpenAI-compatible launcher), attached to the proposal report. Zero API cost,
   no data leaves the machine, and the agent never proposes trades. *(010)*
6. **Approval workflow** — list pending proposals, approve/reject per action,
   7-day expiry; no execution path exists without an approval record. *(011)*
7. **System dashboard** — one `local_sync --status`: freshness, latest report
   paths (a `reports` DB table), data-quality error count, pending approvals,
   overall READY / DEGRADED / STALE. *(012)*
8. **Backtesting** — walk-forward replay of the screening + rebalancing rules:
   CAGR/Sharpe/MDD/turnover vs SPY, with weight-scheme A/B comparison to test
   the composite-score design against alternatives on our own data. *(014)*
9. **Post-approval execution hook** — paper broker first; only approved,
   unexpired actions can ever reach an order; `BOUNDED_AUTO` stays rejected by
   profile validation. *(013)*

Explicitly out of scope for now: KRX as a *screening universe* (held KR assets
are tracked), per-bond YTM models (bond ETFs use price factors), crypto
intrinsic value (no defensible model), options pricing, web dashboard.

## Sequence and rationale

```
008a integrity → 008b screening v2 → 008c universe → 009 txn loop + backfill
                                   └→ 014 backtest   → 010 research agent → 011 approval → 013 execution
012 reports DB / status dashboard — parallel at any point
```

Integrity first: research output computed on misstated valuations is worthless.
Backtest (014) starts right after 008b+008c to validate the screening rework
early.

| Sprint | Title | Size | Status |
|---|---|---|---|
| 008a | Asset-class routing & FX integrity | L | **done** |
| 008b | Screening v2: sub-scores + valuation dimension | M | **done** |
| 008c | Universe scale: S&P 500 + NASDAQ-100 | M | **done** |
| 009 | Transaction-driven snapshot + performance backfill | M | **done** |
| 010 | Research Agent (local LLM via OpenAI-compatible endpoint) | L | **done** |
| 011 | Approval gate | M | planned |
| 012 | Reports DB table + status dashboard | S | planned |
| 014 | Backtest harness (point-in-time price factors; valuation factors flagged for look-ahead; survivorship caveat) | L | planned |
| 013 | Post-approval execution hook (paper broker) | L | planned |

### Screening methodology decision (resolves the single-score question)

Literature review (Fitzgibbons et al. 2017; Clarke/de Silva/Thorley 2016;
Bender & Wang 2016 — pro-integration; Leippold & Rüegg 2018 null result;
Ghayur et al. 2018 conditional; Chow et al. 2018 cost-sensitive): keep the
**composite score for shortlisting** (integration beats sleeve-mixing for a
small universe feeding a human/LLM review), but **persist every dimension
sub-score** in `screening_results.factor_scores` and expose them in reports and
to the Research Agent. Eligibility gates (e.g. the binary `above_200d_ma`) are
separated from score components. Because sub-scores are persisted, the
mix-vs-integrate question stays testable with the 014 backtest harness rather
than being closed by assertion.
