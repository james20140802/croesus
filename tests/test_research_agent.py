"""Sprint 010: local-LLM research agent — generation, failure modes, report."""
from __future__ import annotations

import json
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from croesus.assets.models import Asset
from croesus.assets.repository import AssetRepository
from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.portfolio.actions import ProposedAction
from croesus.portfolio.repository import PortfolioRepository
from croesus.reports.portfolio_action import write_portfolio_action_reports
from croesus.research.agent import generate_research_notes, parse_note_payload
from croesus.research.llm_client import ChatCompletionsClient, LlmUnavailable
from croesus.research.models import STATUS_FAILED, STATUS_GENERATED
from croesus.research.prompt_builder import build_research_messages
from croesus.research.repository import ResearchNoteRepository
from croesus.screening.models import ScreeningCandidate

AS_OF = date(2026, 6, 1)

_NOTE_JSON = json.dumps(
    {
        "business_summary": "Enterprise software with durable cash flows.",
        "catalysts": "Verify whether cloud revenue growth still supports the DCF.",
        "risk_factors": "Trades 60% above modeled intrinsic value.",
    }
)


class FakeChatClient:
    base_url = "http://fake.local/v1"
    model = "fake-model"

    def __init__(self, responses=None, error: Exception | None = None):
        self._responses = list(responses or [])
        self._error = error
        self.requests: list[list[dict[str, str]]] = []

    def chat(self, messages):
        self.requests.append(messages)
        if self._error is not None:
            raise self._error
        return self._responses.pop(0)


def _watch_action(asset_id: str = "US_EQ_MSFT", index: int = 1) -> ProposedAction:
    return ProposedAction(
        action_id=f"run-1-{index:03d}",
        run_id="run-1",
        asset_id=asset_id,
        sleeve_name="satellite_equity",
        action_type="watch",
        current_weight=None,
        target_weight=None,
        proposed_weight=None,
        estimated_trade_value=None,
        reason_codes=["VALUATION_TOO_EXPENSIVE", "QUALITATIVE_RESEARCH_REQUIRED"],
        human_readable_reason="Candidate trades 60% above DCF intrinsic value.",
        requires_research=True,
        requires_user_approval=True,
    )


def _candidate(asset_id: str = "US_EQ_MSFT") -> ScreeningCandidate:
    return ScreeningCandidate(
        run_id="screen-1",
        asset_id=asset_id,
        score=0.9,
        rank=1,
        decision_bucket="candidate",
        reason="passes screen",
        reason_codes=[],
        factor_scores={"price_to_intrinsic": 1.6, "valuation_score": 0.3},
        metadata={"sleeve_name": "satellite_equity"},
    )


def _open(tmp_path: Path):
    db_path = tmp_path / "r.duckdb"
    migrate(db_path)
    return get_connection(db_path)


def _seed_asset(conn) -> None:
    AssetRepository(conn).upsert_many(
        [
            Asset(
                asset_id="US_EQ_MSFT", symbol="MSFT", name="Microsoft Corporation",
                asset_type="equity", country="US", currency="USD",
                sector="Technology", industry="Software", source="test",
            )
        ]
    )


# ── agent behaviour ───────────────────────────────────────────────────────────

def test_generates_and_persists_notes_with_think_tags_and_fences(tmp_path) -> None:
    raw = f"<think>let me reason about this…</think>\n```json\n{_NOTE_JSON}\n```"
    client = FakeChatClient(responses=[raw])
    with _open(tmp_path) as conn:
        _seed_asset(conn)
        result = generate_research_notes(
            conn, run_id="run-1", as_of_date=AS_OF,
            actions=[_watch_action()], screening_candidates=[_candidate()],
            macro_state=None, client=client, log=lambda m: None,
        )
        persisted = ResearchNoteRepository(conn).list_for_run("run-1")

    assert result.generated == 1 and result.failed == 0
    note = persisted[0]
    assert note.status == STATUS_GENERATED
    assert note.model == "fake-model"
    assert note.business_summary.startswith("Enterprise software")
    assert note.knowledge_cutoff_caveat is True
    assert note.metadata["base_url"] == "http://fake.local/v1"


def test_unreachable_server_skips_all_notes_without_blocking(tmp_path) -> None:
    client = FakeChatClient(error=LlmUnavailable("no LLM server reachable"))
    with _open(tmp_path) as conn:
        _seed_asset(conn)
        result = generate_research_notes(
            conn, run_id="run-1", as_of_date=AS_OF,
            actions=[_watch_action(), _watch_action("US_EQ_AAPL", 2)],
            screening_candidates=[], macro_state=None,
            client=client, log=lambda m: None,
        )
        persisted = ResearchNoteRepository(conn).list_for_run("run-1")

    assert result.skipped_reason is not None  # warned, not raised
    assert result.generated == 0 and result.failed == 0
    assert persisted == []
    assert len(client.requests) == 1  # stopped after the first refusal


def test_one_unparseable_response_fails_that_note_and_continues(tmp_path) -> None:
    client = FakeChatClient(responses=["I cannot answer in JSON, sorry.", _NOTE_JSON])
    with _open(tmp_path) as conn:
        _seed_asset(conn)
        result = generate_research_notes(
            conn, run_id="run-1", as_of_date=AS_OF,
            actions=[_watch_action("US_EQ_AAPL", 1), _watch_action("US_EQ_MSFT", 2)],
            screening_candidates=[], macro_state=None,
            client=client, log=lambda m: None,
        )
        persisted = ResearchNoteRepository(conn).list_for_run("run-1")

    assert result.failed == 1 and result.generated == 1
    by_asset = {n.asset_id: n for n in persisted}
    assert by_asset["US_EQ_AAPL"].status == STATUS_FAILED
    assert "unparseable" in by_asset["US_EQ_AAPL"].error
    assert by_asset["US_EQ_MSFT"].status == STATUS_GENERATED


def test_defensive_macro_prepends_risk_warning(tmp_path) -> None:
    macro = SimpleNamespace(
        regime="Deflation", positioning="Defensive", regime_confidence=0.7
    )
    client = FakeChatClient(responses=[_NOTE_JSON])
    with _open(tmp_path) as conn:
        _seed_asset(conn)
        generate_research_notes(
            conn, run_id="run-1", as_of_date=AS_OF,
            actions=[_watch_action()], screening_candidates=[],
            macro_state=macro, client=client, log=lambda m: None,
        )
        note = ResearchNoteRepository(conn).list_for_run("run-1")[0]

    assert note.risk_factors.startswith("[Macro warning] Current regime is Deflation")
    assert "Trades 60% above" in note.risk_factors  # model text preserved after


# ── prompt construction ───────────────────────────────────────────────────────

def test_prompt_carries_quant_data_and_forbids_trades() -> None:
    macro = SimpleNamespace(
        regime="Stagflation", positioning="Cautious", regime_confidence=0.6
    )
    messages = build_research_messages(
        asset={"asset_id": "US_EQ_MSFT", "name": "Microsoft", "sector": "Technology"},
        action=_watch_action(),
        candidate=_candidate(),
        valuation=SimpleNamespace(
            intrinsic_value_per_share=280.0, current_price=448.0, upside_pct=-0.375,
            wacc=0.085, fcf_growth_rate=0.10, terminal_growth_rate=0.025,
        ),
        macro_state=macro,
    )
    system, user = messages[0]["content"], messages[1]["content"]
    assert "Never recommend, propose, or size a trade" in system
    assert "NO web access" in system
    assert "price_to_intrinsic: 1.6" in user
    assert "intrinsic_value_per_share: 280" in user
    assert "regime: Stagflation" in user
    assert "VALUATION_TOO_EXPENSIVE" in user
    # Deterministic: identical inputs → identical prompt.
    assert messages == build_research_messages(
        asset={"asset_id": "US_EQ_MSFT", "name": "Microsoft", "sector": "Technology"},
        action=_watch_action(),
        candidate=_candidate(),
        valuation=SimpleNamespace(
            intrinsic_value_per_share=280.0, current_price=448.0, upside_pct=-0.375,
            wacc=0.085, fcf_growth_rate=0.10, terminal_growth_rate=0.025,
        ),
        macro_state=macro,
    )


def test_parse_note_payload_rejects_missing_keys() -> None:
    with pytest.raises(ValueError, match="risk_factors"):
        parse_note_payload('{"business_summary": "a", "catalysts": "b"}')
    with pytest.raises(ValueError, match="no JSON object"):
        parse_note_payload("plain prose, no json")


# ── report attachment ─────────────────────────────────────────────────────────

def test_report_renders_research_notes_with_caveat(tmp_path) -> None:
    with _open(tmp_path) as conn:
        _seed_asset(conn)
        repo = PortfolioRepository(conn)
        repo.upsert_rebalance_run(
            "run-1", "default", "default", AS_OF,
            decision="research_required", summary="1 non-trade action generated.",
            macro_regime="Goldilocks", macro_positioning="Neutral", metadata={},
        )
        repo.replace_proposed_actions("run-1", [_watch_action()])
        generate_research_notes(
            conn, run_id="run-1", as_of_date=AS_OF,
            actions=[_watch_action()], screening_candidates=[_candidate()],
            macro_state=None, client=FakeChatClient(responses=[_NOTE_JSON]),
            log=lambda m: None,
        )
        markdown_path, _ = write_portfolio_action_reports(
            conn, "run-1", reports_dir=tmp_path
        )

    markdown = markdown_path.read_text(encoding="utf-8")
    assert "## Research Notes" in markdown
    assert "### US_EQ_MSFT (fake-model)" in markdown
    assert "Enterprise software with durable cash flows." in markdown
    assert "training cutoff" in markdown  # knowledge-cutoff caveat always shown
    assert "never constitute trade advice" in markdown


def test_report_without_notes_has_no_research_section(tmp_path) -> None:
    with _open(tmp_path) as conn:
        repo = PortfolioRepository(conn)
        repo.upsert_rebalance_run(
            "run-1", "default", "default", AS_OF,
            decision="no_action", summary="0 actions.", metadata={},
        )
        repo.replace_proposed_actions("run-1", [])
        markdown_path, _ = write_portfolio_action_reports(
            conn, "run-1", reports_dir=tmp_path
        )
    assert "## Research Notes" not in markdown_path.read_text(encoding="utf-8")


# ── real HTTP protocol (any OpenAI-compatible launcher) ──────────────────────

def test_chat_client_speaks_openai_protocol_against_local_server() -> None:
    seen_paths: list[str] = []
    seen_bodies: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - http.server API
            length = int(self.headers.get("Content-Length", "0"))
            seen_paths.append(self.path)
            seen_bodies.append(json.loads(self.rfile.read(length)))
            data = json.dumps(
                {"choices": [{"message": {"role": "assistant", "content": _NOTE_JSON}}]}
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):  # silence test output
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        # Same client works against Ollama / LM Studio / llama.cpp / vLLM —
        # only the base URL differs.
        client = ChatCompletionsClient(
            base_url=f"http://127.0.0.1:{port}/v1", model="any-model", timeout=5
        )
        content = client.chat([{"role": "user", "content": "note please"}])
    finally:
        server.shutdown()
        thread.join(timeout=5)

    assert seen_paths == ["/v1/chat/completions"]
    assert seen_bodies[0]["model"] == "any-model"
    assert seen_bodies[0]["stream"] is False
    assert json.loads(content)["business_summary"]


def test_chat_client_raises_unavailable_when_no_server() -> None:
    import socket

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    free_port = probe.getsockname()[1]
    probe.close()  # nothing listens here now

    client = ChatCompletionsClient(
        base_url=f"http://127.0.0.1:{free_port}/v1", model="m", timeout=2
    )
    with pytest.raises(LlmUnavailable, match="no LLM server reachable"):
        client.chat([{"role": "user", "content": "hi"}])
