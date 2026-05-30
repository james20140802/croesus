# ADR 0008: Enum Types for Investor Profile Fields

## Status

Accepted

## Context

Sprint 003 introduced the investor profile data model (ADR 0007). The original profile spec (`docs/architecture/investor-profile.md`, `docs/planning/sprint-003-investor-profile-policy.md`) typed several closed-value fields as plain strings:

- `base_currency: str`
- `trade_mode: str`
- `allowed_asset_types: list[str]`
- `disallowed_asset_types: list[str]`

The existing codebase convention is plain validated strings (`Asset.asset_type`, macro regime/positioning literals). However, these four profile fields have constrained value sets — `trade_mode` in particular is gated by validation rules and MVP restrictions — and an invalid value should fail fast.

During implementation the type representation was raised as an explicit decision point and resolved in favor of enums.

## Decision

The closed-value profile fields are modeled as `str`-based enums in `croesus/profiles/models.py`:

- `class TradeMode(str, Enum)` — `propose_only`, `approval_required`, `bounded_auto`
- `class AssetType(str, Enum)` — `equity`, `etf`, `reit`, `cash`, `option`, `leveraged_etf`, `short_position`
- `class Currency(str, Enum)` — pragmatic ISO 4217 subset (`USD`, `EUR`, `GBP`, `JPY`, `KRW`, `CNY`, `HKD`, `CAD`, `AUD`, `CHF`)

`InvestorProfile` uses `base_currency: Currency`, `trade_mode: TradeMode`, and `allowed/disallowed_asset_types: list[AssetType]`.

Python 3.10 is the floor (`requires-python = ">=3.10"`), so `enum.StrEnum` (3.11+) is unavailable; the `class X(str, Enum)` pattern is used instead. Because members are `str` subclasses, equality and DuckDB storage stay compatible. The repository (`ProfileRepository`) serializes via `.value` at the DB boundary (TEXT / JSON) and reconstructs enums on read.

## Rationale

- Closed value sets benefit from fail-fast construction: an unknown `trade_mode` or currency raises `ValueError` at the boundary instead of silently persisting.
- `str` subclassing keeps the change low-friction — no schema change (columns stay TEXT / JSON) and string comparisons still work.
- Centralizes the allowed-value lists in one place rather than scattering string literals.

## Consequences

### Positive

- Invalid enum values are rejected at object construction, before validation or persistence.
- Downstream sprints (004–007) reading profiles through `ProfileRepository` receive typed enums.

### Negative

- Diverges from the plain-string convention used elsewhere in the codebase (`Asset.asset_type`, macro literals).
- The `Currency` enum is an intentionally partial ISO 4217 list; an unsupported base currency must be added to the enum before it can be used.
- Sprints 004–007 were drafted against `str`/`list[str]` profile fields; code that consumes profiles must handle enum-typed values (which are still `str` instances).

## Follow-Up

- `docs/architecture/investor-profile.md` and `docs/planning/sprint-003-investor-profile-policy.md` updated to reflect enum typing.
- Revisit whether `Asset.asset_type` and other string-typed fields should also become enums for consistency.
