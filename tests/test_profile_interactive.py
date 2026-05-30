from pathlib import Path

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.jobs.profile_init import main as profile_init_main
from croesus.jobs.profile_init import run_profile_interactive
from croesus.profiles.config_io import read_profile_config
from croesus.profiles.interactive import ask, build_profile_interactively
from croesus.profiles.models import Currency, TradeMode
from croesus.profiles.repository import ProfileRepository
from croesus.profiles.seed_default_profile import (
    DEFAULT_POLICY_TARGETS,
    DEFAULT_PROFILE,
)


def _scripted(answers: dict[str, str]):
    """Return inputs by matching a substring of the prompt; '' (default) otherwise."""

    def _fn(prompt: str) -> str:
        for key, value in answers.items():
            if key in prompt:
                return value
        return ""

    return _fn


def _silent(_msg: str) -> None:
    return None


def test_ask_reprompts_until_valid() -> None:
    values = iter(["abc", "0.12"])
    out: list[str] = []

    result = ask(lambda _p: next(values), out.append, "expected_annual_return", 0.1, float)

    assert result == 0.12
    assert any("invalid" in line.lower() for line in out)


def test_ask_returns_default_on_empty_input() -> None:
    result = ask(lambda _p: "", _silent, "name", "Default Name", str)
    assert result == "Default Name"


def test_interactive_all_defaults_yields_default_profile() -> None:
    profile, targets = build_profile_interactively(
        DEFAULT_PROFILE,
        DEFAULT_POLICY_TARGETS,
        input_fn=lambda _p: "",
        output_fn=_silent,
    )

    assert profile == DEFAULT_PROFILE
    assert targets == DEFAULT_POLICY_TARGETS


def test_interactive_applies_user_overrides() -> None:
    answers = {
        "profile_id": "chase",
        "name": "My account",
        "base_currency": "EUR",
        "max_tolerable_drawdown": "-0.30",
        "trade_mode": "approval_required",
    }

    profile, _targets = build_profile_interactively(
        DEFAULT_PROFILE,
        DEFAULT_POLICY_TARGETS,
        input_fn=_scripted(answers),
        output_fn=_silent,
    )

    assert profile.profile_id == "chase"
    assert profile.name == "My account"
    assert profile.base_currency is Currency.EUR
    assert profile.max_tolerable_drawdown == -0.30
    assert profile.trade_mode is TradeMode.APPROVAL_REQUIRED


def test_run_profile_interactive_upserts_to_db(tmp_path: Path) -> None:
    db_path = tmp_path / "i.duckdb"
    migrate(db_path)

    with get_connection(db_path) as conn:
        pid = run_profile_interactive(
            conn,
            input_fn=_scripted({"profile_id": "chase", "name": "My account"}),
            output_fn=_silent,
        )
        loaded = ProfileRepository(conn).get_profile("chase")

    assert pid == "chase"
    assert loaded is not None
    assert loaded.name == "My account"


def test_run_profile_interactive_optionally_saves_yaml(tmp_path: Path) -> None:
    db_path = tmp_path / "i.duckdb"
    migrate(db_path)
    cfg = tmp_path / "out.yaml"

    with get_connection(db_path) as conn:
        run_profile_interactive(
            conn,
            input_fn=_scripted({"profile_id": "chase"}),
            output_fn=_silent,
            save_path=cfg,
        )

    assert cfg.exists()
    saved, _targets = read_profile_config(cfg)
    assert saved.profile_id == "chase"


def test_interactive_main_wires_stdin(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "configured.duckdb"
    monkeypatch.setenv("CROESUS_DB_PATH", str(db_path))
    monkeypatch.setattr("builtins.input", _scripted({"profile_id": "chase"}))

    profile_init_main(["--interactive"])

    with get_connection(db_path) as conn:
        loaded = ProfileRepository(conn).get_profile("chase")

    assert loaded is not None
