# Opportunity Engine Roadmap & Status

**Goal:** extend the system's purpose from **risk management → return generation**.
The existing factor screen + portfolio layer is the **downside gate** (it manages
risk but does not beat SPY); a new **opportunity engine** sources upside — the
under-appreciated "모래 속 진주" (pearls in the sand) the backward-looking factor
screen can't surface by construction, plus shorter-horizon catalyst repricing.

- **Design spec (authoritative):** `docs/superpowers/specs/2026-06-15-opportunity-research-engine-design.md`
- **Boundary (philosophy §3 allows, §133 forbids):** the LLM only *judges /
  recommends* with REQUIRED evidence; deterministic code owns the numbers; the
  human owns the decision and any execution. SEC EDGAR + news are the sources.
- **Honesty stance the user accepts:** no alpha guarantee; not point-in-time
  backtestable (no historical news archive) → manual review + forward-test
  accumulation.

This document is the checked-in roadmap/progress/deferred record. The running
project memory mirrors it at `opportunity-engine-direction` (private memory).

---

## The pipeline (methodology A — moat-adjusted intrinsic value)

```
whole universe
  → [B2] deterministic event pre-filter        (cheap, no LLM)  → small candidate set
  → [C1] SEC filing body text  +  [News-1/2] Finnhub + GDELT news   (evidence)
  → [C2] LLM structural-thesis grader          (moat/tech/sector/disruption + evidence + bear)
  → [C3] grade → DcfKnobs → bear/base/bull intrinsic-value band     (recommendation-only)
  → [D]  user picks a methodology      → [E] risk gate integration   (recommendation-only)
```

The event pre-filter (B2) **is** the opportunity engine's "quantitative screening
first" stage — it is deliberately *not* the portfolio `screening_run` engine,
because momentum/quality screens are backward-looking and would rank out exactly
the pearls we want. The LLM runs only on this shortlist (philosophy §4).

---

## Status — what is DONE & merged

All phases below are merged to `main`, each via the same gate: written plan →
TDD implementation → two-stage review (spec-compliance, then code-quality) →
`/code-review high` (8 finder angles) → fixes → user-approved merge.

| Phase | Delivers | Key code / table / job | PR |
|-------|----------|------------------------|----|
| **A** | `DcfKnobs` value object + `value_with_knobs` recompute path; valuation job persists knob fields. Defaults reproduce prior behavior exactly. | `factors/equity/valuation.py` | #39 |
| **B1** | SEC EDGAR disclosure-metadata ingestion (no LLM, metadata only). | `croesus/disclosures/` (models/parse/source/repository/ingest); `disclosures` table; `disclosures_run` | #40 |
| **B2** | Deterministic event pre-filter (the candidate funnel). 4 detectors: abnormal_volume, abnormal_return, recent_disclosure, valuation_dislocation. | `croesus/events/`; `events` table (PK asset_id+as_of_date+event_type); `event_scan` | #41 |
| **C1** | SEC filing **body text** ingestion (the evidence "heart"). | `croesus/disclosures/text_*`; `disclosure_texts` table; `disclosure_texts_run` | #42 |
| **News-1** | Finnhub company-news ingestion (ticker-tagged). | `croesus/news/{models,parse,source,repository,finnhub_ingest}`; `news_items` + `news_item_assets`; `news_finnhub_run` | #43 |
| **News-2** | GDELT DOC 2.0 broad news + `trafilatura` body fetch (catalyst discovery). | `croesus/news/{gdelt_parse,gdelt_source,body_fetch,gdelt_ingest}`; `body` col; `news_gdelt_run` | #44 |
| **C2** | LLM structural-thesis grader: reads filing text + news + numbers for event candidates, emits discrete evidence-backed grades. | `croesus/research/thesis_*` + `json_extract`; `thesis_grades` table; `thesis_grader_run` | #45 |
| **C3** | thesis grade → `DcfKnobs` → **bear/base/bull intrinsic-value band**. | `factors/equity/{thesis_knobs,intrinsic_bands,band_repository}`; `intrinsic_value_bands` table | #46 |
| **D** | User-selectable opportunity methodology review surface. Methodology A is executable; methodology B is visible as designed/deferred and blocked until implemented. | `croesus/opportunities/{selection,review}.py`; `croesus/jobs/opportunity_review.py`; `croesus/reports/opportunity.py` | local |
| **E** | Recommendation-only risk-gate over user-selected candidates: bucket capacity (`block_new_buy`), asset-type eligibility, liquidity floor (warn). Verdict attached per card in the review report. No re-rank, no trades, no new table. | `croesus/opportunities/risk_gate.py`; `croesus/portfolio/asset_attrs.py` (shared); `review.py`/`reports/opportunity.py`/`jobs/opportunity_review.py` extensions | local |

The A→E methodology-A pipeline is **functionally complete** (event sourcing →
evidence → grading → value band → user-selected review surface → recommendation-only
risk gate). Only **automatic selection influence** (구제/강등) remains deferred.

### C2 grade taxonomy → C3 knob mapping

| Dimension | Grades | → DcfKnob | C3 table |
|-----------|--------|-----------|----------|
| moat | wide / narrow / none | `explicit_years` (CAP) | `CAP_YEARS = {wide:10, narrow:7, none:5}` |
| sector trajectory | secular_growth / stable / declining | `terminal_growth_rate` | `TERMINAL_GROWTH = {0.030, 0.025, 0.015}` |
| disruption risk | low / medium / high | `wacc_risk_premium` | `RISK_PREMIUM = {0.00, 0.01, 0.02}` |
| tech capability | leading / parity / lagging | *(none — human-review evidence)* | — |

Every grade carries evidence; an overall `evidence_source` ∈ {filing,
general_knowledge} distinguishes filing-defensible from general knowledge; a
`bear_case` is always required.

---

## Key design decisions (user-confirmed)

1. **Risk gate stays mechanical.** The grade-derived DCF (C3) writes ONLY to
   `intrinsic_value_bands`. The base `valuation_snapshots` and the
   `price_to_intrinsic` factor — which feed `screening_run` and `rebalance_check`
   — keep `DEFAULT_DCF_KNOBS`. The LLM thesis never flows into automatic
   rebalancing; the opportunity engine is **recommendation-only**.
2. **Band rule = one grade-step perturbation.** base = grade-mapped knobs;
   **bear** steps moat & sector one notch pessimistic + disruption one notch
   worse; **bull** mirrors; all clamped to the spec's value range.
3. **Grade-only + viable-base-only bands.** A band is produced only for an asset
   that has a `generated` thesis grade AND a positive base DCF — never a
   universe-wide mechanical band, never upside manufactured from a broken base.
4. **Event funnel, not the portfolio screener.** C2/C3 funnel on
   `SELECT DISTINCT asset_id FROM events` (latest cohort), the cheap deterministic
   pre-filter — by design, since the portfolio screen can't surface pearls.
5. **Local LLM only.** C2 reuses the `ChatClient`/`ChatCompletionsClient` Ollama
   path (default `qwen3:32b`); `LlmUnavailable` → skip + retry next cycle (never
   freezes freshness); `LlmError`/parse → one `failed` grade + continue. Zero API
   cost, no data leaves the machine.
6. **`compute_fcf_growth` window stays fixed.** The CAGR look-back is observed
   history (`DCF_EXPLICIT_YEARS`); the moat-stretched CAP (`knobs.explicit_years`)
   controls only the projection length, not the historical window. Growth is
   identical across the three scenarios (an observed fact, not a thesis lever).

---

## Deferred & explicitly skipped (지나친 점)

### Design-level deferrals (from the spec — intentionally not built yet)
- **Methodology B** — event-driven opportunity thesis (structural-winner vs
  catalyst-repricing; direction + horizon + evidence + bear) over the same
  `events` feed. Design-only.
- **E — automatic selection influence (구제/강등)** — the recommendation-only risk
  gate is now built (PR for Phase E); what remains deferred is an opportunity
  recommendation changing screen rank or portfolio selection *without* a human.
  **Automatic selection influence is deferred on purpose** until forward-test
  validation accumulates.
- **Multiple-scoring sector-relative fix** — design-only.

### Capability gaps left for follow-ups
- **Human-facing surface breadth** — Phase D now provides a CLI/report view for
  methodology A: `python -m croesus.jobs.opportunity_review --methodology
  moat_adjusted_intrinsic_value --report`. This is still review-only and does
  not feed the portfolio layer. Methodology B still needs its own normalized
  event-driven thesis implementation before it can be selected.
- **News-2 deferreds** — (a) theme-based broad discovery + NER / GKG entity →
  ticker mapping (GDELT currently maps by company-name query only — name
  ambiguity is a known limitation, resolved downstream by the C2 LLM); (b) GDELT
  `timelinevol` `news_spike` event detector (would revive B2's deferred
  `news_spike` trigger).
- **B2 deferred detectors** — `news_spike` (needs the News-2 timelinevol feed),
  `guidance_change` (needs 8-K text parsing beyond C1's plain extraction).
- **`trafilatura` not auto-installed** — it is only declared in `pyproject.toml`;
  until `uv add trafilatura` runs in the deploy env, GDELT bodies persist as
  NULL (the fetcher lazily imports and swallows the ImportError by design).
- **`thesis_grader_run` / quarterly DCF coupling** — the band rides the existing
  quarterly DCF pass and reads the *latest* grade best-effort (point-in-time via
  `load_latest_for_asset`); there is no job edge forcing a re-DCF when a fresh
  grade lands. A stale grade's age is visible via the stored `thesis_as_of_date`.
- **`thesis_grades.run_id` vs sync `run_id`** — the grader self-generates a
  `run_id`; correlating it to the enclosing `job_runs.run_id` would need a shared
  `JobRunner` signature change (out of scope; the natural key is asset+date, so
  grades are still correct).
- **Base-path zero-price guard** — `compute_valuation`'s base DCF divides by
  `calc.price` without a zero guard (pre-dates C3; isolated per-asset). The new
  band path guards it; the base path was left untouched per decision #1.

### Recurring review skips (consistent house patterns — intentional)
- `FILER_ASSET_TYPES = ("equity",)` is duplicated across ingest modules; each
  ingest job filters `list_active()` Python-side — matches the sibling pattern,
  not deduped.
- Repositories spell out column lists explicitly in SQL (no shared builder) —
  matches `NewsRepository` / `ThesisGradeRepository` house style.
- DuckDB is columnar → no secondary indexes added for sparse lookup tables.

---

## Where things live (index)

**Tables:** `disclosures`, `disclosure_texts`, `events`, `news_items`,
`news_item_assets`, `thesis_grades`, `intrinsic_value_bands`
(all in `croesus/db/schema.sql`, idempotent `CREATE TABLE IF NOT EXISTS`).

**Sync jobs** (`croesus/jobs/local_sync.py`, in dependency order):
`disclosures_run` → `disclosure_texts_run` → `news_finnhub_run` →
`news_gdelt_run` → `event_scan` → `thesis_grader_run`; the C3 band rides the
existing `quarterly_run` DCF pass (no separate job).

**Human-run review CLI:** `python -m croesus.jobs.opportunity_review`
opens a methodology menu when `--methodology` is omitted. Methodology A renders
current price, mechanical DCF, moat-adjusted bear/base/bull DCF, thesis grades,
confidence/evidence source, and bear case from persisted rows. It is
recommendation-only; no trade proposals or approvals are created.

**Plans** (`docs/superpowers/plans/`): one per phase —
`2026-06-15-dcf-knobs`, `2026-06-16-edgar-disclosure-ingestion`,
`2026-06-17-event-driven-prefilter`, `2026-06-18-filing-text-ingestion`,
`2026-06-18-news-finnhub-ingestion`, `2026-06-18-news-gdelt-ingestion`,
`2026-06-19-thesis-grader-c2`, `2026-06-20-intrinsic-value-bands-c3`.

**Reuse anchors for methodology B / the report surface:**
`croesus/research/llm_client.py` (ChatClient), `croesus/research/json_extract.py`
(tolerant `<think>`+JSON parse), `EventRepository.load_for_date`,
`ThesisGradeRepository.load_latest_for_asset`,
`IntrinsicValueBandRepository.load_for_asset`.
