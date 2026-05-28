# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Status

Croesus is currently in the **documentation and prototyping phase**. The `docs/` tree is the authoritative spec. The `experiments/events_impact/` directory contains a working FOMC event-study prototype. The main `croesus/` package has not been implemented yet — Sprint 001 is the first implementation sprint.

## Running the Pipeline (once implemented)

```bash
python -m croesus.jobs.bootstrap   # create DB schema, seed assets
python -m croesus.jobs.daily_run   # ingest prices, compute factors
```

Manual verification:
```python
from croesus.db.connection import get_connection

with get_connection() as conn:
    print(conn.execute("SELECT * FROM assets").df())
    print(conn.execute("SELECT * FROM prices_daily LIMIT 5").df())
    print(conn.execute("SELECT * FROM factor_values").df())
```

## Architecture

Eight-component pipeline:

```
Asset Universe → Data Ingestion → Data Store → Factor Engine
  → Screening Engine → Research Agent → Portfolio Engine → Report Generator
```

Key flow: the system uses **broad quantitative screening first, then LLM deep research only on the shortlist**. The Research Agent runs after the Screening Engine has narrowed the universe — never the reverse.

### Data Store

- DuckDB file at `storage/croesus.duckdb`
- Core tables: `assets`, `prices_daily`, `fundamentals`, `factor_values`, `screening_results`, `reports`
- Schema uses a long-format `factor_values` table: `(asset_id, date, factor_name, value)` — new factors are added as rows, not columns

### Asset Registry

All downstream modules read assets from the `assets` table. Never hard-code ticker lists. The registry fields include `asset_id, symbol, name, asset_type, country, exchange, currency, sector, industry, is_active, source, metadata`.

### Factor Engine

Factors are computed deterministically. Initial common factors: `momentum_1m`, `momentum_3m`, `momentum_6m`, `volatility_3m`, `liquidity_1m`, `above_200d_ma`. Equity-specific factors (valuation, quality, growth) are out of scope for Sprint 001.

### Macro Analysis Layer (ADR 0004, planned)

A 3-layer macro score engine sits above individual screening:
- **Layer 1 (Regime)**: Growth direction × Inflation direction → 4 regimes
- **Layer 2 (Risk Amplifier)**: Liquidity / credit / rates adjust regime intensity
- **Layer 3 (Confirmation)**: Volatility / trend / sentiment / FX validate or warn

`MacroState` feeds into Screening Engine parameter adjustments only (one-way dependency).

### Experiments

`experiments/events_impact/` is a standalone FOMC event-study prototype. Modules: `config`, `event_study`, `fomc`, `dummy_macro`, `prices`, `rates`, `schema`, `stats`, `surprise`, `viz`. It uses its own DuckDB cache and is independent from the main `croesus/` package.

## Key Design Decisions (see `docs/adr/`)

- **Python-first CLI** — no web app until the research pipeline is proven
- **DuckDB** for local analytical storage; consider PostgreSQL later for multi-user
- **Asset registry before screener** — ADR 0003 explains why hard-coded tickers break extension
- **Deterministic code for all calculable signals** — LLMs interpret, never compute factors
- **No trade execution without explicit user approval**

## Core Constraints

- Do not mix data ingestion, factor computation, and reporting in one module
- Do not assume every asset is a US common stock
- Factor computation must skip assets with insufficient data without crashing the run
- Keep `factor_name` strings stable — they are primary key components

## Git Workflow

All work happens on a dedicated branch — never commit directly to `main`.

**Branch naming:**

```
feat/<short-description>     # new functionality
fix/<short-description>      # bug fixes
chore/<short-description>    # tooling, deps, config
docs/<short-description>     # documentation only
```

**Commits:** Commit frequently and atomically — one logical change per commit. Do not batch unrelated changes into a single commit.

**Commit messages use gitmoji:**

```
✨ feat: add yfinance price ingestion
🐛 fix: skip assets with missing OHLCV rows
♻️ refactor: extract factor normalization into helper
📝 docs: add factor engine architecture
🔧 chore: add duckdb to pyproject.toml
🗃️ chore: add initial DuckDB schema migration
```

Common gitmoji for this project: `✨` new feature · `🐛` bugfix · `♻️` refactor · `📝` docs · `🔧` config/tooling · `🗃️` database · `🧪` tests · `⚡️` performance · `🔥` remove code/files

## Dependency Management

Use `uv` (preferred) or `poetry`. Core runtime dependencies: `duckdb`, `pandas`, `yfinance`, `pydantic`, `pyyaml`, `python-dotenv`.
