from __future__ import annotations

from datetime import date
from typing import Callable

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
    as_of_date: date,
    client: ChatClient | None = None,
    log: Callable[[str], None] = print,
) -> ThesisRunResult:
    """Grade the structural thesis of every event-prefiltered candidate.

    Funnel = assets with an event on ``as_of_date`` (LLM only on the shortlist).
    Failure contract: ``LlmUnavailable`` stops the whole run (server down);
    ``LlmError`` / unparseable response persists a ``failed`` grade and continues.
    """
    if client is None:
        from croesus.research.llm_client import ChatCompletionsClient

        client = ChatCompletionsClient()

    result = ThesisRunResult(run_id=run_id)
    repo = ThesisGradeRepository(conn)

    candidate_ids = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT asset_id FROM events WHERE as_of_date = ? ORDER BY asset_id",
            [as_of_date],
        ).fetchall()
    ]
    if not candidate_ids:
        log("thesis_grader: no event candidates")
        return result

    assets_by_id = {a.asset_id: a for a in AssetRepository(conn).list_active()}

    for asset_id in candidate_ids:
        asset = assets_by_id.get(asset_id)
        if asset is None:
            continue  # candidate not in the active universe (e.g. delisted)
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

        grade = ThesisGrade(
            asset_id=asset_id, as_of_date=as_of_date, run_id=run_id,
            model=client.model, status=STATUS_GENERATED, **payload,
        )
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
