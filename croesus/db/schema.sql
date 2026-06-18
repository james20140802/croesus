CREATE TABLE IF NOT EXISTS assets (
  asset_id TEXT PRIMARY KEY,
  symbol TEXT NOT NULL,
  name TEXT,
  asset_type TEXT NOT NULL,
  country TEXT,
  exchange TEXT,
  currency TEXT,
  sector TEXT,
  industry TEXT,
  is_active BOOLEAN DEFAULT TRUE,
  source TEXT,
  metadata JSON
);

CREATE TABLE IF NOT EXISTS prices_daily (
  asset_id TEXT NOT NULL,
  date DATE NOT NULL,
  open DOUBLE,
  high DOUBLE,
  low DOUBLE,
  close DOUBLE,
  adjusted_close DOUBLE,
  volume BIGINT,
  source TEXT,
  PRIMARY KEY (asset_id, date)
);

CREATE TABLE IF NOT EXISTS fx_rates (
  quote_currency TEXT NOT NULL,
  date DATE NOT NULL,
  rate_per_usd DOUBLE,
  source TEXT,
  PRIMARY KEY (quote_currency, date)
);

CREATE TABLE IF NOT EXISTS factor_values (
  asset_id TEXT NOT NULL,
  date DATE NOT NULL,
  factor_name TEXT NOT NULL,
  value DOUBLE,
  PRIMARY KEY (asset_id, date, factor_name)
);

CREATE TABLE IF NOT EXISTS screening_results (
  run_id TEXT NOT NULL,
  asset_id TEXT NOT NULL,
  score DOUBLE,
  rank INTEGER,
  decision_bucket TEXT,
  reason TEXT,
  PRIMARY KEY (run_id, asset_id)
);

ALTER TABLE screening_results ADD COLUMN IF NOT EXISTS reason_codes JSON;
ALTER TABLE screening_results ADD COLUMN IF NOT EXISTS factor_scores JSON;
ALTER TABLE screening_results ADD COLUMN IF NOT EXISTS metadata JSON;

CREATE TABLE IF NOT EXISTS macro_scores (
  date                DATE PRIMARY KEY,
  regime              TEXT NOT NULL,
  regime_confidence   DOUBLE,
  growth_direction    TEXT,
  inflation_direction TEXT,
  amplifier_score     DOUBLE,
  confirmation_score  DOUBLE,
  positioning         TEXT,
  raw_indicators      JSON,
  warnings            JSON,
  opportunities       JSON,
  regime_methods      JSON
);

-- Add regime_methods column to existing databases that predate this migration
ALTER TABLE macro_scores ADD COLUMN IF NOT EXISTS regime_methods JSON;

CREATE TABLE IF NOT EXISTS investor_profiles (
  profile_id TEXT PRIMARY KEY,
  name TEXT,
  base_currency TEXT,
  expected_annual_return DOUBLE,
  max_tolerable_drawdown DOUBLE,
  investment_horizon_years INTEGER,
  monthly_contribution DOUBLE,
  liquidity_buffer_months DOUBLE,
  allowed_asset_types JSON,
  disallowed_asset_types JSON,
  max_single_position_weight DOUBLE,
  max_sector_weight DOUBLE,
  max_industry_weight DOUBLE,
  max_theme_weight DOUBLE,
  max_country_weight DOUBLE,
  max_currency_weight DOUBLE,
  max_monthly_turnover DOUBLE,
  rebalance_band DOUBLE,
  trade_mode TEXT,
  created_at TIMESTAMP,
  updated_at TIMESTAMP,
  metadata JSON
);

CREATE TABLE IF NOT EXISTS policy_targets (
  profile_id TEXT NOT NULL,
  sleeve_name TEXT NOT NULL,
  target_weight DOUBLE NOT NULL,
  min_weight DOUBLE,
  max_weight DOUBLE,
  metadata JSON,
  PRIMARY KEY (profile_id, sleeve_name)
);

CREATE TABLE IF NOT EXISTS portfolios (
  portfolio_id TEXT PRIMARY KEY,
  profile_id TEXT NOT NULL,
  name TEXT,
  base_currency TEXT,
  created_at TIMESTAMP,
  updated_at TIMESTAMP,
  metadata JSON
);

CREATE TABLE IF NOT EXISTS portfolio_holdings (
  portfolio_id TEXT NOT NULL,
  asset_id TEXT NOT NULL,
  as_of_date DATE NOT NULL,
  quantity DOUBLE,
  market_value DOUBLE,
  currency TEXT,
  cost_basis DOUBLE,
  avg_cost DOUBLE,
  source TEXT,
  metadata JSON,
  PRIMARY KEY (portfolio_id, asset_id, as_of_date)
);

ALTER TABLE portfolio_holdings ADD COLUMN IF NOT EXISTS avg_cost DOUBLE;

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
  portfolio_id TEXT NOT NULL,
  as_of_date DATE NOT NULL,
  total_market_value DOUBLE,
  total_cost_basis DOUBLE,
  unrealized_pnl DOUBLE,
  cash_value DOUBLE,
  metadata JSON,
  PRIMARY KEY (portfolio_id, as_of_date)
);

ALTER TABLE portfolio_snapshots ADD COLUMN IF NOT EXISTS total_cost_basis DOUBLE;
ALTER TABLE portfolio_snapshots ADD COLUMN IF NOT EXISTS unrealized_pnl DOUBLE;

CREATE TABLE IF NOT EXISTS portfolio_exposures (
  portfolio_id TEXT NOT NULL,
  as_of_date DATE NOT NULL,
  exposure_type TEXT NOT NULL,
  exposure_name TEXT NOT NULL,
  weight DOUBLE,
  market_value DOUBLE,
  limit_weight DOUBLE,
  is_violation BOOLEAN,
  PRIMARY KEY (portfolio_id, as_of_date, exposure_type, exposure_name)
);

CREATE TABLE IF NOT EXISTS policy_drifts (
  portfolio_id TEXT NOT NULL,
  as_of_date DATE NOT NULL,
  sleeve_name TEXT NOT NULL,
  current_weight DOUBLE,
  target_weight DOUBLE,
  min_weight DOUBLE,
  max_weight DOUBLE,
  drift DOUBLE,
  is_outside_band BOOLEAN,
  PRIMARY KEY (portfolio_id, as_of_date, sleeve_name)
);

CREATE TABLE IF NOT EXISTS rebalance_runs (
  run_id TEXT PRIMARY KEY,
  portfolio_id TEXT NOT NULL,
  profile_id TEXT NOT NULL,
  date DATE NOT NULL,
  macro_regime TEXT,
  macro_positioning TEXT,
  decision TEXT,
  summary TEXT,
  metadata JSON
);

CREATE TABLE IF NOT EXISTS proposed_actions (
  action_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  asset_id TEXT,
  sleeve_name TEXT,
  action_type TEXT NOT NULL,
  current_weight DOUBLE,
  target_weight DOUBLE,
  proposed_weight DOUBLE,
  estimated_trade_value DOUBLE,
  reason_codes JSON,
  human_readable_reason TEXT,
  requires_research BOOLEAN,
  requires_user_approval BOOLEAN
);

-- Sprint 006b: local scheduler and data freshness.
-- job_runs records the execution history of every local job (success/failure/
-- skip) so a future dashboard reads the same state the CLI does.
CREATE TABLE IF NOT EXISTS job_runs (
  run_id TEXT PRIMARY KEY,
  job_name TEXT NOT NULL,
  started_at TIMESTAMP,
  finished_at TIMESTAMP,
  status TEXT,
  summary TEXT,
  error TEXT,
  metadata JSON
);

-- data_freshness is the queryable "can I trust today's report?" state, one row
-- per data domain, derived deterministically from job_runs and source tables.
CREATE TABLE IF NOT EXISTS data_freshness (
  data_domain TEXT PRIMARY KEY,
  latest_data_date DATE,
  latest_success_at TIMESTAMP,
  stale_after_hours DOUBLE,
  status TEXT,
  reason TEXT,
  metadata JSON
);

-- Sprint 006c: transaction ledger. The append-only record of how a portfolio
-- changed over time (buys, sells, deposits, withdrawals, dividends, fees,
-- manual adjustments). Holdings can be derived deterministically from these
-- rows, and a manually-executed proposed action links back via
-- linked_action_id. transaction_type values are a stable product contract.
CREATE TABLE IF NOT EXISTS portfolio_transactions (
  transaction_id TEXT PRIMARY KEY,
  portfolio_id TEXT NOT NULL,
  asset_id TEXT,
  transaction_date DATE NOT NULL,
  transaction_type TEXT NOT NULL,
  quantity DOUBLE,
  price DOUBLE,
  gross_amount DOUBLE,
  currency TEXT,
  fees DOUBLE,
  source TEXT,
  linked_action_id TEXT,
  metadata JSON
);

-- Sprint 006d: performance and goal tracking. One row per (portfolio, date,
-- period) turns the profile's target return into a measurable progress report:
-- contribution-adjusted return (deposits are not investment gain), the gap to
-- the profile's expected_annual_return, and a risk_status shown beside it.
-- These are progress reports, not guarantees. Annualized return and lightweight
-- attribution live in metadata JSON.
CREATE TABLE IF NOT EXISTS portfolio_performance_snapshots (
  portfolio_id TEXT NOT NULL,
  as_of_date DATE NOT NULL,
  period TEXT NOT NULL,
  start_value DOUBLE,
  end_value DOUBLE,
  net_contributions DOUBLE,
  investment_return DOUBLE,
  investment_return_pct DOUBLE,
  target_return_pct DOUBLE,
  return_gap_pct DOUBLE,
  max_drawdown_pct DOUBLE,
  risk_status TEXT,
  status TEXT,
  metadata JSON,
  PRIMARY KEY (portfolio_id, as_of_date, period)
);

-- Sprint 007: valuation analysis. fundamentals stores normalized
-- financial-statement metrics (long format like factor_values), one row per
-- (asset, period_end, period_type, metric). metric_name strings are a stable
-- contract. valuation_snapshots records the detailed 2-stage DCF result per
-- (asset, date): intrinsic value, the CAPM WACC, growth assumptions, and a JSON
-- blob of every assumption so an LLM override can be audited later.
CREATE TABLE IF NOT EXISTS fundamentals (
  asset_id     TEXT NOT NULL,
  period_end   DATE NOT NULL,
  period_type  TEXT NOT NULL,   -- 'annual' | 'quarterly'
  metric_name  TEXT NOT NULL,
  value        DOUBLE,
  source       TEXT,
  PRIMARY KEY (asset_id, period_end, period_type, metric_name)
);

CREATE TABLE IF NOT EXISTS valuation_snapshots (
  asset_id                  TEXT NOT NULL,
  date                      DATE NOT NULL,
  intrinsic_value_per_share DOUBLE,
  current_price             DOUBLE,
  upside_pct                DOUBLE,
  wacc                      DOUBLE,
  fcf_growth_rate           DOUBLE,
  terminal_growth_rate      DOUBLE,
  assumptions_json          TEXT,
  PRIMARY KEY (asset_id, date)
);

-- Sprint 008a: data-quality issues. Every silent fallback (missing price,
-- missing FX rate) is recorded here as a persistent, queryable issue instead of
-- a transient warning string, so reports and the status dashboard can mark a
-- snapshot DEGRADED rather than presenting misstated values as clean.
CREATE TABLE IF NOT EXISTS data_quality_issues (
  issue_id   TEXT PRIMARY KEY,
  run_id     TEXT,
  domain     TEXT NOT NULL,     -- 'portfolio_snapshot' | 'price_ingestion' | 'fx'
  severity   TEXT NOT NULL,     -- 'error' | 'warn' | 'info'
  asset_id   TEXT,
  currency   TEXT,
  as_of_date DATE,
  code       TEXT NOT NULL,     -- 'PRICE_MISSING' | 'FX_MISSING' | ...
  message    TEXT,
  created_at TIMESTAMP DEFAULT now()
);

-- Sprint 010: qualitative research notes from the local LLM. Notes annotate
-- rebalance proposals only — nothing in this table can create, size, or
-- execute a trade. ``model`` records which local model wrote the note;
-- ``knowledge_cutoff_caveat`` flags that the model had no web access and may
-- be unaware of events after its training cutoff.
CREATE TABLE IF NOT EXISTS research_notes (
  note_id    TEXT PRIMARY KEY,
  run_id     TEXT NOT NULL,     -- rebalance run the note annotates
  action_id  TEXT,
  asset_id   TEXT NOT NULL,
  as_of_date DATE NOT NULL,
  model      TEXT NOT NULL,
  status     TEXT NOT NULL,     -- 'generated' | 'failed'
  business_summary TEXT,
  catalysts  TEXT,
  risk_factors TEXT,
  knowledge_cutoff_caveat BOOLEAN DEFAULT TRUE,
  error      TEXT,
  metadata   JSON,
  created_at TIMESTAMP DEFAULT now()
);

-- Sprint 011: approval gate. Trade proposals carry an explicit approval
-- record; nothing downstream may act on an action that is not 'approved' and
-- unexpired. Approval state lives on the action row itself; a new rebalance
-- run writes new rows under a new run_id, so earlier decisions are never
-- overwritten.
ALTER TABLE proposed_actions ADD COLUMN IF NOT EXISTS approval_status TEXT;
ALTER TABLE proposed_actions ADD COLUMN IF NOT EXISTS approved_at TIMESTAMP;
ALTER TABLE proposed_actions ADD COLUMN IF NOT EXISTS approval_notes TEXT;
ALTER TABLE proposed_actions ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP;

-- Sprint 012: reports registry. Every report file written by the pipeline is
-- registered here so the status dashboard can show the latest report path per
-- report type without scanning the filesystem. ``report_type`` is a stable
-- product contract: 'macro' | 'screening' | 'portfolio_action' | 'performance'.
-- ``format`` is inferred from the file suffix when not provided by the writer.
-- ``run_id`` links back to the rebalance_run or screening_run that generated
-- the report so a dashboard can correlate freshness, actions, and files.
CREATE TABLE IF NOT EXISTS reports (
  report_id   TEXT PRIMARY KEY,
  report_type TEXT NOT NULL,   -- 'macro' | 'screening' | 'portfolio_action' | 'performance' | ...
  as_of_date  DATE,
  path        TEXT NOT NULL,
  format      TEXT,            -- 'markdown' | 'csv'
  run_id      TEXT,
  created_at  TIMESTAMP DEFAULT now()
);

-- Forward-test harness (post-roadmap). Valuation cannot be backtested without
-- point-in-time fundamentals (look-ahead), so candidate weight schemes are
-- forward-tested: each cohort records the top-N a scheme would buy on a given
-- date, with the entry price, and realized return is measured from stored
-- prices over time vs SPY. No look-ahead — every figure is out-of-sample.
-- A cohort is one (scheme, as_of_date); one row per pick. weight is the
-- redundancy-group-capped construction weight at entry.
CREATE TABLE IF NOT EXISTS forward_test_cohorts (
  cohort_scheme TEXT NOT NULL,
  as_of_date    DATE NOT NULL,
  asset_id      TEXT NOT NULL,
  rank          INTEGER,
  score         DOUBLE,
  weight        DOUBLE,
  entry_price   DOUBLE,
  created_at    TIMESTAMP DEFAULT now(),
  PRIMARY KEY (cohort_scheme, as_of_date, asset_id)
);

-- Phase B1 (opportunity engine): SEC EDGAR filing metadata. One row per
-- (asset, accession). Stores filing METADATA only — form type, filing/report
-- dates, and the primary-document URL — never the document text or any LLM
-- output. This is the raw feed the event-driven pre-filter (Phase B2) scans for
-- "something forward just happened" triggers (e.g. a new 8-K). ``accession_number``
-- is EDGAR's globally unique filing id, so (asset_id, accession_number) is a
-- stable natural key for idempotent re-ingestion.
CREATE TABLE IF NOT EXISTS disclosures (
  asset_id          TEXT NOT NULL,
  accession_number  TEXT NOT NULL,
  form_type         TEXT NOT NULL,   -- '10-K' | '10-Q' | '8-K'
  filed_date        DATE NOT NULL,
  report_date       DATE,            -- period the filing reports on; may be absent
  primary_doc_url   TEXT,            -- URL to the primary document on sec.gov
  title             TEXT,            -- primaryDocDescription; NULL when EDGAR gives none
  source            TEXT NOT NULL,   -- 'sec_edgar'
  created_at        TIMESTAMP DEFAULT now(),
  PRIMARY KEY (asset_id, accession_number)
);

-- Phase B2 (opportunity engine): deterministic event-driven pre-filter output.
-- One row per (asset, as_of_date, event_type): a cheap "something forward just
-- happened" signal computed with NO LLM from prices, valuation, and disclosures.
-- This is the candidate funnel methodologies A/B later apply an LLM thesis to;
-- nothing here sizes or executes a trade. ``magnitude`` is detector-specific
-- (z-score / signed sigma-multiple / days-ago / upside fraction); ``direction``
-- is 'up' | 'down' | 'neutral'. New detectors add new ``event_type`` values
-- without a schema change.
CREATE TABLE IF NOT EXISTS events (
  asset_id    TEXT NOT NULL,
  as_of_date  DATE NOT NULL,
  event_type  TEXT NOT NULL,   -- 'abnormal_volume'|'abnormal_return'|'recent_disclosure'|'valuation_dislocation'
  direction   TEXT,            -- 'up' | 'down' | 'neutral'
  magnitude   DOUBLE,          -- detector-specific strength
  detail      TEXT,            -- human-readable one-liner
  source      TEXT NOT NULL,   -- source table: 'prices_daily'|'valuation_snapshots'|'disclosures'
  created_at  TIMESTAMP DEFAULT now(),
  PRIMARY KEY (asset_id, as_of_date, event_type)
);

-- Phase C1 (opportunity engine): SEC filing BODY TEXT. One row per
-- (asset, accession), holding the cleaned plain text of the filing's primary
-- document (fetched from disclosures.primary_doc_url). This is the textual
-- evidence the structural-thesis grader (Phase C2) reads — it stores extracted
-- text only, never an LLM judgement. ``status`` is 'fetched' | 'empty' | 'failed';
-- ``char_count`` is the stored text length (text is capped to bound DB size).
CREATE TABLE IF NOT EXISTS disclosure_texts (
  asset_id          TEXT NOT NULL,
  accession_number  TEXT NOT NULL,
  source_url        TEXT,
  char_count        INTEGER,
  text              TEXT,
  status            TEXT NOT NULL,   -- 'fetched' | 'empty' | 'failed'
  source            TEXT NOT NULL,   -- 'sec_edgar'
  created_at        TIMESTAMP DEFAULT now(),
  PRIMARY KEY (asset_id, accession_number)
);
