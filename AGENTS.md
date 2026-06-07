# AGENTS.md

This file provides guidance to AI agents (Claude, Codex, Gemini, Copilot, etc.) operating in this repository.

## Role of Agents in Croesus

Agents assist with implementation, analysis, and research. They must respect the hard boundary between **deterministic computation** (code's responsibility) and **qualitative interpretation** (agent's responsibility).

### What agents should compute with code

- Returns, momentum, volatility, drawdown
- Moving averages, liquidity metrics
- Valuation and balance-sheet ratios
- Factor normalization and percentile ranking
- Portfolio weights and concentration checks
- Macro indicator percentile scores and regime classification

### What agents may interpret with language

- News, earnings call transcripts, SEC filings
- Competitive positioning and industry narratives
- Regulatory risk and management commentary
- Explanation and synthesis of quantitative outputs

Never use an agent to produce a factor value, risk metric, or portfolio constraint result that code could compute deterministically.

## Architecture Agents Must Respect

Read `docs/architecture/system-overview.md` before making structural changes. The eight-component pipeline has strict one-way dependencies:

```
Asset Registry → Prices → Factors → Screening → [Research Agent] → Portfolio → Reports
```

- The **Research Agent** runs only after quantitative screening has narrowed the universe
- The **Macro Analysis Layer** (`MacroState`) feeds into Screening parameters only — macro module does not know about screening internals
- The **Portfolio Engine** proposes; it does not execute without explicit user approval

## Implementation Rules

### Asset Registry

Never hard-code ticker lists anywhere. All modules that need an asset universe must query the `assets` table. Use `is_active = true` to filter.

### DuckDB

The database file lives at `storage/croesus.duckdb`. Use the connection module (`croesus/db/connection.py` once implemented). DuckDB does not support concurrent writes well — do not open multiple write connections simultaneously.

### Factor Values Table

The `factor_values` table is long-format: `(asset_id, date, factor_name, value)`. Add new factors as new rows, not new columns. Keep `factor_name` strings stable — they are part of the primary key.

### Module Separation

Each module has one responsibility. Do not mix concerns:

| Module | Responsibility |
|---|---|
| `data_sources/` | Fetch raw external data |
| `prices/` | Store and retrieve OHLCV |
| `factors/` | Compute signals from prices |
| `screening/` | Filter and rank using factor values |
| `reports/` | Format outputs |
| `jobs/` | Orchestrate; call modules in sequence |

### Error Handling

The pipeline must not crash when a single asset fails. Wrap per-asset operations with try/except, log the failure, and continue. This applies to price ingestion, factor computation, and screening.

### Experiments

`experiments/` is for standalone research prototypes. Code there is not part of the main `croesus/` package and should not import from it. Promote validated patterns from experiments to the main package; do not import from experiments in production code.

## Git Workflow

Follow the same workflow as documented in `CLAUDE.md`.

- **Never commit to `main` directly.** Always create a branch first.
- **Branch prefixes:** `feat/`, `fix/`, `chore/`, `docs/`
- **Atomic commits:** one logical change per commit — do not batch unrelated changes
- **Commit messages use gitmoji.** Examples for this codebase:

  ```
  ✨ feat: add asset registry seed for AAPL, MSFT, NVDA
  🗃️ chore: create prices_daily and factor_values tables
  🐛 fix: continue pipeline when single ticker fetch fails
  ♻️ refactor: move factor normalization to common helper
  📝 docs: update sprint 001 acceptance criteria
  ```

When an agent completes a logical unit of work (e.g., one module, one schema change, one bug fix), commit immediately rather than accumulating all changes.

## Safety

- **No trade execution** — the Portfolio Engine may propose but must require explicit user confirmation before any trade is submitted to a broker or trading API
- **No autonomous LLM research on the full universe** — the Research Agent runs only on the candidate shortlist produced by the Screening Engine
- **No destructive DuckDB operations in jobs** — `DROP TABLE`, `DELETE FROM`, and schema migrations require explicit invocation, not automatic runs

## Sprint 001 Scope

The first implementation sprint is deliberately small:

1. `croesus/db/` — connection, schema, migrations
2. `croesus/assets/` — models, repository, seed (AAPL, MSFT, NVDA only)
3. `croesus/data_sources/` + `croesus/prices/` — yfinance ingestion
4. `croesus/factors/` — six common factors
5. `croesus/jobs/bootstrap.py` and `croesus/jobs/daily_run.py`

Do not implement the Screening Engine, Research Agent, Portfolio Engine, or macro layer in Sprint 001.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, invoke the `skill` tool with `skill: "graphify"` before doing anything else.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
