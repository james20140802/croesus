"""
Research agent (Sprint 010): local-LLM notes for proposals needing research.

For every rebalance action flagged ``requires_research``, the agent assembles
the pipeline's quantitative evidence (screening sub-scores, DCF snapshot,
macro regime), asks the local model for a business / catalysts / risks note,
and persists the result to ``research_notes``.

Failure contract (the pipeline is never blocked by a missing LLM):
  - LLM server unreachable → log a warning, return with ``skipped_reason``;
    the rebalance run completes without notes.
  - One asset's response unparseable or one request failing → that note is
    persisted as ``failed`` with the error, and the agent continues.

The agent only ever *annotates* existing proposals. It does not create,
modify, size, or execute actions — there is no code path from a note to a
trade.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable
from uuid import uuid4

import duckdb

from croesus.factors.equity.repository import ValuationSnapshotRepository
from croesus.research.llm_client import (
    ChatClient,
    ChatCompletionsClient,
    LlmError,
    LlmUnavailable,
)
from croesus.research.models import (
    STATUS_FAILED,
    STATUS_GENERATED,
    ResearchNote,
)
from croesus.research.prompt_builder import build_research_messages
from croesus.research.repository import ResearchNoteRepository

# qwen3-style reasoning traces wrap deliberation in <think> tags; the note is
# whatever follows.
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)

_NOTE_KEYS = ("business_summary", "catalysts", "risk_factors")

# Positionings under which a new-position note must carry a macro warning.
_DEFENSIVE_POSITIONINGS = {"Cautious", "Defensive"}


@dataclass(frozen=True)
class ResearchRunResult:
    run_id: str
    notes: list[ResearchNote] = field(default_factory=list)
    generated: int = 0
    failed: int = 0
    skipped_reason: str | None = None


def generate_research_notes(
    conn: duckdb.DuckDBPyConnection,
    *,
    run_id: str,
    as_of_date: date,
    actions: list,
    screening_candidates: list,
    macro_state: Any | None,
    client: ChatClient | None = None,
    log: Callable[[str], None] = print,
) -> ResearchRunResult:
    """Generate and persist notes for every ``requires_research`` action."""
    targets = [a for a in actions if a.requires_research and a.asset_id]
    if not targets:
        return ResearchRunResult(run_id=run_id)

    # Default client is built lazily so runs with nothing to research never
    # touch the network or require a configured LLM.
    client = client or ChatCompletionsClient()
    candidates_by_asset = {c.asset_id: c for c in screening_candidates}
    valuation_repo = ValuationSnapshotRepository(conn)
    assets_by_id = _load_asset_rows(conn, [a.asset_id for a in targets])

    notes: list[ResearchNote] = []
    generated = failed = 0
    skipped_reason: str | None = None

    for action in targets:
        messages = build_research_messages(
            asset=assets_by_id.get(action.asset_id),
            action=action,
            candidate=candidates_by_asset.get(action.asset_id),
            valuation=valuation_repo.get(action.asset_id, as_of_date),
            macro_state=macro_state,
        )
        try:
            raw = client.chat(messages)
        except LlmUnavailable as exc:
            # The server itself is down — every further request would fail the
            # same way. Skip the rest; the rebalance run continues without notes.
            skipped_reason = str(exc)
            log(f"research agent: LLM unavailable, notes skipped — {exc}")
            break
        except LlmError as exc:
            notes.append(_failed_note(action, run_id, as_of_date, client, str(exc)))
            failed += 1
            log(f"research agent: {action.asset_id} failed — {exc}")
            continue

        try:
            payload = parse_note_payload(raw)
        except ValueError as exc:
            notes.append(
                _failed_note(
                    action, run_id, as_of_date, client,
                    f"unparseable model response: {exc}",
                )
            )
            failed += 1
            log(f"research agent: {action.asset_id} response unparseable — {exc}")
            continue

        risk_factors = payload["risk_factors"]
        if macro_state is not None and macro_state.positioning in _DEFENSIVE_POSITIONINGS:
            # Deterministic guardrail, not an LLM judgment: a new-position note
            # written under a defensive macro posture must say so up front.
            risk_factors = (
                f"[Macro warning] Current regime is {macro_state.regime} with "
                f"{macro_state.positioning} positioning — treat any new position "
                f"conservatively. {risk_factors}"
            )

        notes.append(
            ResearchNote(
                note_id=uuid4().hex,
                run_id=run_id,
                action_id=action.action_id,
                asset_id=action.asset_id,
                as_of_date=as_of_date,
                model=client.model,
                status=STATUS_GENERATED,
                business_summary=payload["business_summary"],
                catalysts=payload["catalysts"],
                risk_factors=risk_factors,
                metadata={"base_url": client.base_url},
            )
        )
        generated += 1

    ResearchNoteRepository(conn).save_many(notes)
    if generated or failed:
        log(f"research agent: {generated} note(s) generated, {failed} failed")
    return ResearchRunResult(
        run_id=run_id,
        notes=notes,
        generated=generated,
        failed=failed,
        skipped_reason=skipped_reason,
    )


def parse_note_payload(raw: str) -> dict[str, str]:
    """Extract the note JSON from a model response.

    Tolerates reasoning traces (``<think>…</think>``), markdown code fences,
    and prose around the object. Raises ``ValueError`` when no valid note
    object can be recovered.
    """
    text = _THINK_RE.sub("", raw)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found in model response")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in model response: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("model response JSON is not an object")

    payload: dict[str, str] = {}
    for key in _NOTE_KEYS:
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"missing or empty {key!r} in model response")
        payload[key] = value.strip()
    return payload


def _failed_note(
    action: Any,
    run_id: str,
    as_of_date: date,
    client: ChatClient,
    error: str,
) -> ResearchNote:
    return ResearchNote(
        note_id=uuid4().hex,
        run_id=run_id,
        action_id=action.action_id,
        asset_id=action.asset_id,
        as_of_date=as_of_date,
        model=client.model,
        status=STATUS_FAILED,
        error=error,
        metadata={"base_url": client.base_url},
    )


def _load_asset_rows(
    conn: duckdb.DuckDBPyConnection, asset_ids: list[str]
) -> dict[str, dict[str, Any]]:
    lookup = sorted(set(asset_ids))
    if not lookup:
        return {}
    placeholders = ", ".join("?" for _ in lookup)
    rows = conn.execute(
        f"""
        SELECT asset_id, name, sector, industry
        FROM assets WHERE asset_id IN ({placeholders})
        """,
        lookup,
    ).fetchall()
    return {
        row[0]: {"asset_id": row[0], "name": row[1], "sector": row[2], "industry": row[3]}
        for row in rows
    }
