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
