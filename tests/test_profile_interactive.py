from pathlib import Path
from typing import Any

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.jobs.profile_init import main as profile_init_main
from croesus.jobs.profile_init import run_profile_interactive
from croesus.profiles.config_io import read_profile_config
from croesus.profiles.interactive import (
    QuestionaryPrompter,
    build_profile_interactively,
)
from croesus.profiles.models import AssetType, Currency, TradeMode
from croesus.profiles.repository import ProfileRepository
from croesus.profiles.seed_default_profile import (
    DEFAULT_POLICY_TARGETS,
    DEFAULT_PROFILE,
)


class ScriptedPrompter:
    """Test double: returns scripted answers by key, records what was shown."""

    def __init__(self, answers: dict[str, Any] | None = None) -> None:
        self.answers = answers or {}
        self.seen: list[dict[str, Any]] = []

    def info(self, message: str) -> None:
        self.seen.append({"kind": "info", "message": message, "description": ""})

    def text(self, key, message, description, default, parse) -> Any:
        self.seen.append({"kind": "text", "key": key, "description": description})
        return self.answers.get(key, default)

    def select(self, key, message, description, choices, default) -> Any:
        self.seen.append({"kind": "select", "key": key, "description": description})
        return self.answers.get(key, default)

    def checkbox(self, key, message, description, choices, default) -> Any:
        self.seen.append({"kind": "checkbox", "key": key, "description": description})
        return self.answers.get(key, list(default))

    def prompted_keys(self) -> set:
        return {e["key"] for e in self.seen if e["kind"] in {"text", "select", "checkbox"}}


def test_build_uses_defaults_when_unanswered() -> None:
    profile, targets = build_profile_interactively(
        DEFAULT_PROFILE, DEFAULT_POLICY_TARGETS, prompter=ScriptedPrompter(), profile_id="default"
    )

    assert profile == DEFAULT_PROFILE
    assert targets == DEFAULT_POLICY_TARGETS


def test_build_does_not_prompt_for_profile_id() -> None:
    prompter = ScriptedPrompter()

    profile, _targets = build_profile_interactively(
        DEFAULT_PROFILE, DEFAULT_POLICY_TARGETS, prompter=prompter, profile_id="given-id"
    )

    # profile_id comes from the argument, never from a prompt
    assert profile.profile_id == "given-id"
    assert "profile_id" not in prompter.prompted_keys()


def test_build_applies_user_overrides() -> None:
    answers = {
        "name": "My account",
        "base_currency": Currency.EUR,
        "max_tolerable_drawdown": -0.30,
        "trade_mode": TradeMode.APPROVAL_REQUIRED,
    }

    profile, _targets = build_profile_interactively(
        DEFAULT_PROFILE, DEFAULT_POLICY_TARGETS, prompter=ScriptedPrompter(answers),
        profile_id="chase",
    )

    assert profile.profile_id == "chase"
    assert profile.name == "My account"
    assert profile.base_currency is Currency.EUR
    assert profile.max_tolerable_drawdown == -0.30
    assert profile.trade_mode is TradeMode.APPROVAL_REQUIRED


def test_asset_type_fields_use_checkbox() -> None:
    prompter = ScriptedPrompter({"allowed_asset_types": [AssetType.EQUITY]})

    profile, _targets = build_profile_interactively(
        DEFAULT_PROFILE, DEFAULT_POLICY_TARGETS, prompter=prompter, profile_id="x"
    )

    assert profile.allowed_asset_types == [AssetType.EQUITY]
    checkbox_keys = {e["key"] for e in prompter.seen if e["kind"] == "checkbox"}
    assert {"allowed_asset_types", "disallowed_asset_types"} <= checkbox_keys


def test_enum_scalar_fields_use_select() -> None:
    prompter = ScriptedPrompter()

    build_profile_interactively(
        DEFAULT_PROFILE, DEFAULT_POLICY_TARGETS, prompter=prompter, profile_id="x"
    )

    select_keys = {e["key"] for e in prompter.seen if e["kind"] == "select"}
    assert {"base_currency", "trade_mode"} <= select_keys


def test_every_prompt_has_a_description() -> None:
    prompter = ScriptedPrompter()

    build_profile_interactively(
        DEFAULT_PROFILE, DEFAULT_POLICY_TARGETS, prompter=prompter, profile_id="x"
    )

    field_prompts = [e for e in prompter.seen if e["kind"] in {"text", "select", "checkbox"}]
    assert field_prompts
    assert all(e["description"].strip() for e in field_prompts)


def test_run_profile_interactive_auto_generates_id(tmp_path: Path) -> None:
    db_path = tmp_path / "i.duckdb"
    migrate(db_path)

    with get_connection(db_path) as conn:
        pid = run_profile_interactive(
            conn, prompter=ScriptedPrompter({"name": "My account"})
        )
        loaded = ProfileRepository(conn).get_profile(pid)

    assert pid  # non-empty, system-generated
    assert pid != "default"
    assert loaded is not None
    assert loaded.name == "My account"


def test_run_profile_interactive_keeps_explicit_id(tmp_path: Path) -> None:
    db_path = tmp_path / "i.duckdb"
    migrate(db_path)

    with get_connection(db_path) as conn:
        pid = run_profile_interactive(
            conn, prompter=ScriptedPrompter({"name": "Edited"}), profile_id="chase"
        )
        loaded = ProfileRepository(conn).get_profile("chase")

    assert pid == "chase"
    assert loaded is not None
    assert loaded.name == "Edited"


def test_run_profile_interactive_optionally_saves_yaml(tmp_path: Path) -> None:
    db_path = tmp_path / "i.duckdb"
    migrate(db_path)
    cfg = tmp_path / "out.yaml"

    with get_connection(db_path) as conn:
        pid = run_profile_interactive(
            conn, prompter=ScriptedPrompter({"name": "Saved"}), save_path=cfg
        )

    assert cfg.exists()
    saved, _targets = read_profile_config(cfg)
    assert saved.profile_id == pid


def test_interactive_main_auto_generates_and_persists(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "configured.duckdb"
    monkeypatch.setenv("CROESUS_DB_PATH", str(db_path))

    profile_init_main(["--interactive"], prompter=ScriptedPrompter({"name": "From CLI"}))

    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT profile_id, name FROM investor_profiles"
        ).fetchall()

    assert len(rows) == 1
    profile_id, name = rows[0]
    assert profile_id != "default"
    assert name == "From CLI"


def test_questionary_prompter_constructs() -> None:
    assert QuestionaryPrompter() is not None
