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
