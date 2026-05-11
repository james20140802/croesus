# Asset Universe

## Purpose

The asset universe is the foundation of Croesus. Analysis code should not receive arbitrary hard-coded ticker lists. Instead, it should operate on assets stored in a registry and selected through explicit universe filters.

## Core Idea

Croesus should model investment targets as assets, not just stock tickers.

An asset may be:

- A US common stock.
- A foreign common stock.
- An ADR.
- An ETF.
- A REIT.
- A bond ETF.
- A commodity ETF.
- A crypto asset.
- A currency pair.
- An index.
- Another investment product.

## Asset Model

Initial fields:

```text
asset_id
symbol
name
asset_type
country
exchange
currency
sector
industry
is_active
source
metadata
```

Example:

```json
{
  "asset_id": "US_EQ_AAPL",
  "symbol": "AAPL",
  "name": "Apple Inc.",
  "asset_type": "equity",
  "country": "US",
  "exchange": "NASDAQ",
  "currency": "USD",
  "sector": "Technology",
  "industry": "Consumer Electronics",
  "is_active": true,
  "source": "manual_seed"
}
```

## Asset ID Convention

Use stable internal IDs instead of relying only on tickers.

Suggested format:

```text
{COUNTRY_OR_SCOPE}_{ASSET_TYPE}_{SYMBOL_OR_IDENTIFIER}
```

Examples:

```text
US_EQ_AAPL
US_ETF_SPY
KR_EQ_005930
CRYPTO_BTC_USD
FX_USD_KRW
```

Tickers can be reused, renamed, delisted, or ambiguous across exchanges. Internal IDs reduce ambiguity.

## Universe Layers

Croesus should distinguish between the global registry and strategy-specific universes.

```text
Asset Registry
  -> Supported Assets
  -> Tradable Universe
  -> Strategy Universe
  -> Today's Screening Target
```

Example universes:

```text
All US Listed Equities
  -> US Large Cap
  -> US Mid Cap
  -> US Small Cap
  -> S&P 500
  -> NASDAQ 100
  -> Profitable Growth Stocks
```

Later:

```text
Global Assets
  -> US Equities
  -> Korea Equities
  -> Japan Equities
  -> Europe Equities
  -> ETFs
  -> REITs
  -> Bonds
  -> Commodities
  -> Crypto
  -> FX
```

## Filter-Based Universe Definition

A universe should be represented by filters rather than static code.

Example:

```yaml
universe:
  name: us_listed_profitable_large_cap
  filters:
    asset_type: equity
    country: US
    min_market_cap_usd: 10000000000
    require_positive_net_income: true
    min_avg_daily_volume_usd: 50000000
```

## Initial Implementation Strategy

Do not begin with the full US equity universe. Build in stages:

1. Manual seed assets: AAPL, MSFT, NVDA.
2. Expanded seed: 30-100 liquid US equities.
3. S&P 500 / NASDAQ 100.
4. All US-listed common stocks.
5. ETFs and REITs.
6. International equities.
7. Other asset classes.

## Data Source Considerations

Initial possible sources:

- Manual seed file.
- yfinance ticker metadata.
- NASDAQ listed symbol files.
- SEC company tickers.
- Exchange listing files.
- Paid data providers later if needed.

The source layer should normalize external identifiers into the internal `assets` table.

## Asset Types

Initial supported values:

```text
equity
etf
reit
adr
preferred_stock
bond_etf
commodity_etf
crypto
fx
index
fund
other
```

The system should not assume that every asset type supports the same factors.

## Asset-Specific Factor Logic

Common factors can apply broadly:

- Momentum.
- Volatility.
- Liquidity.

Equity-only factors:

- Valuation.
- Quality.
- Growth.
- Profitability.
- Leverage.

ETF factors:

- Expense ratio.
- Tracking error.
- AUM.
- Holdings concentration.
- Exposure.

Bond-related factors:

- Duration.
- Yield.
- Credit quality.
- Rate sensitivity.

Crypto factors:

- Liquidity.
- Volatility.
- On-chain metrics.
- Exchange coverage.

## Initial Database Table

```sql
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
```

## Implementation Rule

All downstream modules should query assets from the registry. They should not define their own ticker universe inline.
