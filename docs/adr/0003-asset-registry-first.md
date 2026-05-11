# ADR 0003: Build the Asset Registry Before the Screener

## Status

Accepted

## Context

Croesus is intended to expand beyond a few manually selected tickers.

The desired expansion path is:

1. A few seed US equities.
2. All US-listed equities.
3. Global equities.
4. ETFs, REITs, bonds, commodities, FX, crypto, and other investment products.

If the first implementation hard-codes ticker lists directly into the screener, the system will become difficult to extend.

## Decision

Croesus will build an asset registry before building the main screener.

All downstream modules should read assets from the registry instead of defining independent ticker lists.

## Rationale

An asset registry makes the system more extensible because:

- Tickers are not globally unique.
- Symbols can change.
- Assets can be delisted.
- Different asset classes require different metadata.
- Different strategies need different universe filters.
- The system must eventually support non-equity products.

## Initial Asset Registry Fields

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

## Consequences

### Positive

- Cleaner expansion from seed assets to large universes.
- Better separation between asset discovery and analysis.
- Easier support for multiple asset classes.
- Clearer data model for future portfolio and research modules.

### Negative

- Slightly more setup before the first screener works.
- Requires early schema discipline.
- Initial MVP may feel slower than hard-coding tickers.

## Implementation Guidance

The first sprint should still remain small:

- Create the `assets` table.
- Seed only AAPL, MSFT, and NVDA.
- Ingest prices only for active seed assets.
- Compute common factors only after prices are stored.

This gives Croesus the right architecture without overbuilding the universe ingestion layer too early.
