from __future__ import annotations

from datetime import date
from time import monotonic as _monotonic
from typing import Callable, Iterable

import duckdb

from croesus.assets.repository import AssetRepository
from croesus.research.llm_client import ChatClient, LlmError, LlmUnavailable
from croesus.research.thesis_evidence import assemble_thesis_evidence
from croesus.research.thesis_models import (
    STATUS_FAILED,
    STATUS_GENERATED,
    ThesisGrade,
    ThesisRunResult,
)
from croesus.research.thesis_parse import parse_thesis_payload
from croesus.research.thesis_prompt import build_thesis_messages
from croesus.research.thesis_repository import ThesisGradeRepository


def grade_theses(
    conn: duckdb.DuckDBPyConnection,
    *,
    run_id: str,
    as_of_date: date | None = None,
    only_asset_ids: Iterable[str] | None = None,
    client: ChatClient | None = None,
    log: Callable[[str], None] = print,
    deadline: float | None = None,
    monotonic: Callable[[], float] = _monotonic,
) -> ThesisRunResult:
    """Grade the structural thesis of a prefiltered candidate shortlist.

    Funnel (LLM only on the shortlist):
      - default → assets with an event on ``as_of_date`` (the opportunity engine's
        event-driven cohort).
      - ``only_asset_ids`` given → exactly those assets (e.g. the screening
        candidates + current holdings), bypassing the event gate. The explicit
        list is itself the cost control, so it does not require an event row.

    ``as_of_date`` defaults to the latest event cohort (``MAX(events.as_of_date)``)
    so a weekend/holiday run grades the last trading day's events; when an explicit
    shortlist is given and no events exist yet it falls back to ``date.today()``.
    Failure contract: ``LlmUnavailable`` stops the whole run (server down);
    ``LlmError`` / unparseable response persists a ``failed`` grade and continues.

    Time budget: with ``deadline`` (a ``monotonic()`` timestamp) set, the loop
    stops cleanly before the next asset once the clock passes it, counting the
    unreached candidates in ``result.budget_skipped``. This bounds a degraded
    LLM (each call can crawl up to ``CROESUS_LLM_TIMEOUT``) so the automated
    refresh cannot hold the DuckDB write lock for hours and 503 the web.
    """
    if client is None:
        from croesus.research.llm_client import ChatCompletionsClient

        client = ChatCompletionsClient()

    result = ThesisRunResult(run_id=run_id)
    repo = ThesisGradeRepository(conn)

    if as_of_date is None:
        row = conn.execute("SELECT MAX(as_of_date) FROM events").fetchone()
        as_of_date = row[0] if row else None
        if as_of_date is None:
            if only_asset_ids is not None:
                as_of_date = date.today()  # explicit shortlist, no event cohort yet
            else:
                log("thesis_grader: no events to grade")
                return result

    if only_asset_ids is not None:
        candidate_ids = sorted(set(only_asset_ids))
    else:
        candidate_ids = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT asset_id FROM events WHERE as_of_date = ? ORDER BY asset_id",
                [as_of_date],
            ).fetchall()
        ]
    if not candidate_ids:
        log("thesis_grader: no candidates to grade")
        return result

    assets_by_id = {a.asset_id: a for a in AssetRepository(conn).list_active()}

    for i, asset_id in enumerate(candidate_ids):
        if deadline is not None and monotonic() >= deadline:
            remaining = len(candidate_ids) - i
            result.budget_skipped += remaining
            log(
                f"thesis_grader: time budget reached — skipping {remaining} "
                f"remaining asset(s)"
            )
            break
        asset = assets_by_id.get(asset_id)
        if asset is None:
            # Candidate has an event but isn't in the active universe (e.g.
            # delisted). Count it so "grader produced nothing" is diagnosable.
            result.skipped += 1
            continue
        try:
            evidence = assemble_thesis_evidence(conn, asset, as_of_date)
            messages = build_thesis_messages(asset, evidence)
            raw = client.chat(messages)
        except LlmUnavailable as exc:
            result.skipped_reason = str(exc)
            log(f"thesis_grader: LLM unavailable, aborting: {exc}")
            break
        except LlmError as exc:
            repo.upsert(_failed_grade(asset_id, as_of_date, run_id, client.model, str(exc)))
            result.failed += 1
            log(f"thesis_grader: failed {asset.symbol}: {exc}")
            continue

        try:
            payload = parse_thesis_payload(raw)
        except ValueError as exc:
            repo.upsert(_failed_grade(
                asset_id, as_of_date, run_id, client.model,
                f"unparseable model response: {exc}",
            ))
            result.failed += 1
            log(f"thesis_grader: unparseable {asset.symbol}: {exc}")
            continue

        try:
            grade = ThesisGrade(
                asset_id=asset_id, as_of_date=as_of_date, run_id=run_id,
                model=client.model, status=STATUS_GENERATED, **payload,
            )
        except TypeError as exc:
            # A parser/model field that ThesisGrade doesn't accept must fail
            # this one asset, never abort the whole run.
            repo.upsert(_failed_grade(
                asset_id, as_of_date, run_id, client.model,
                f"grade construction failed: {exc}",
            ))
            result.failed += 1
            log(f"thesis_grader: bad payload {asset.symbol}: {exc}")
            continue
        repo.upsert(grade)
        result.grades.append(grade)
        result.generated += 1
        log(f"thesis_grader: graded {asset.symbol} moat={grade.moat_grade}")

    return result


def _failed_grade(
    asset_id: str, as_of_date: date, run_id: str, model: str, error: str
) -> ThesisGrade:
    return ThesisGrade(
        asset_id=asset_id, as_of_date=as_of_date, run_id=run_id,
        model=model, status=STATUS_FAILED, error=error,
    )
