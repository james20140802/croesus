# ADR 0007: Investor Profile First Portfolio Workflow

## Status

Accepted

## Context

Croesus began as an investment research pipeline: collect market and company data, compute deterministic factors, screen assets, and generate research reports.

That pipeline is still necessary, but it does not fully explain how the product helps an individual investor make money over time. A stock or sector can look attractive in isolation while still being inappropriate for a specific investor's portfolio because of concentration, drawdown tolerance, liquidity needs, or existing exposure.

The product direction is therefore shifting from "research interesting assets" to "operate a personalized portfolio under explicit constraints."

## Decision

Croesus will make the investor profile the top-level input to the system.

The primary workflow becomes:

```text
Investor Profile
  -> Policy Portfolio
  -> Current Portfolio
  -> Macro / Sector / Company Analysis
  -> Rebalancing Proposal
```

The existing macro, factor, screening, valuation, and research modules remain part of the system. They become inputs to portfolio decisions rather than independent sources of trade recommendations.

Level 1 MVP will end at deterministic rebalancing proposals. It will not execute trades.

## Rationale

- Individual investors need a process that matches their risk tolerance, return expectations, time horizon, and constraints.
- Portfolio fit matters more than isolated asset attractiveness.
- Deterministic rules are better suited than LLMs for enforcing risk limits and rebalancing thresholds.
- Macro analysis is valuable as a risk-budget modifier, but it should not override the investor mandate.
- A proposal-first workflow preserves a clear path toward future approval-based or bounded automated execution without introducing execution risk early.

## Consequences

### Positive

- Product direction becomes clearer: personal portfolio management rather than generic stock research.
- Existing analysis work remains useful as portfolio decision inputs.
- MVP success can be measured by whether Croesus produces coherent rebalance/no-action proposals.
- Future broker integration has a natural approval and guardrail boundary.

### Negative

- Requires new portfolio and profile data models before the Research Agent becomes useful.
- Screening outputs must be evaluated through profile and portfolio constraints.
- Some existing docs that emphasize asset research need to be read as subordinate to the profile-first workflow.

## Follow-Up Decisions

- Define the initial policy portfolio format.
- Decide whether Level 1 holdings input is manual CSV, YAML, or both.
- Decide how sector and theme tags are assigned in the initial asset registry.
- Decide when to introduce approval-based order generation.
