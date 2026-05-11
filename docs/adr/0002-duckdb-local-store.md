# ADR 0002: Use DuckDB as the Initial Local Research Store

## Status

Accepted

## Context

Croesus needs a local store for asset metadata, prices, computed factors, and screening outputs.

The system is currently an early research prototype. It does not yet require distributed storage, multi-user access, or production-grade database operations.

## Decision

Croesus will use DuckDB as the initial local analytical database.

The initial database file should live under:

```text
storage/croesus.duckdb
```

## Rationale

DuckDB is suitable for the MVP because:

- It is simple to run locally.
- It works well with pandas.
- It supports analytical queries.
- It does not require a database server.
- It is easy to reset during early experimentation.
- It can later export data to Parquet or other analytical formats.

## Alternatives Considered

### SQLite

SQLite is simple and reliable, but DuckDB is more convenient for analytical workflows and dataframe-heavy research.

### PostgreSQL

PostgreSQL is a strong production database, but it adds operational overhead too early.

### Files only: CSV/Parquet

Files are simple, but schema management and incremental updates become messy as the system grows.

## Consequences

### Positive

- Fast local iteration.
- Simple setup.
- Good analytical ergonomics.
- Clear migration path toward larger analytical storage later.

### Negative

- Not designed as the final multi-user production database.
- Concurrent writes need care.
- Production deployment may eventually require PostgreSQL, object storage, or a warehouse.

## Follow-Up Decisions

- Decide when to introduce PostgreSQL for app/backend state.
- Decide whether historical market data should eventually move to Parquet/object storage.
- Decide how to separate local research storage from production user data.
