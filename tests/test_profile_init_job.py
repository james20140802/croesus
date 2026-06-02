from pathlib import Path

import pytest

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.jobs.profile_init import main as profile_init_main
from croesus.jobs.profile_init import run_profile_guided
from croesus.jobs.profile_init import run_profile_init
from croesus.profiles.models import PolicyTarget
from croesus.profiles.repository import ProfileRepository
from croesus.profiles.seed_default_profile import (
    DEFAULT_POLICY_TARGETS,
    DEFAULT_PROFILE,
    seed_default_profile,
)
from croesus.profiles.validation import validate_policy_targets, validate_profile


class GuidedPrompter:
    def __init__(self, answers=None, *, confirmed=True) -> None:
        self.answers = answers or {}
        self.confirmed = confirmed
        self.seen: list[dict[str, str]] = []

    def info(self, message: str) -> None:
        self.seen.append({"kind": "info", "key": "", "message": message})

    def text(self, key, message, description, default, parse):
        self.seen.append({"kind": "text", "key": key, "message": message})
        return self.answers.get(key, default)

    def select(self, key, message, description, choices, default):
        self.seen.append({"kind": "select", "key": key, "message": message})
        return self.answers.get(key, default)

    def checkbox(self, key, message, description, choices, default):
        self.seen.append({"kind": "checkbox", "key": key, "message": message})
        return self.answers.get(key, list(default))

    def confirm(self, key, message, default):
        self.seen.append({"kind": "confirm", "key": key, "message": message})
        return self.confirmed

    def prompted_keys(self) -> set[str]:
        return {e["key"] for e in self.seen if e["key"]}


def test_default_seed_data_is_valid() -> None:
    assert validate_profile(DEFAULT_PROFILE).is_valid
    assert validate_policy_targets(DEFAULT_POLICY_TARGETS).is_valid


def test_seed_default_profile_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "profiles.duckdb"
    migrate(db_path)

    with get_connection(db_path) as conn:
        seed_default_profile(conn)
        seed_default_profile(conn)
        profile_count = conn.execute("SELECT COUNT(*) FROM investor_profiles").fetchone()[0]
        target_count = conn.execute("SELECT COUNT(*) FROM policy_targets").fetchone()[0]

    assert profile_count == 1
    assert target_count == 4


def test_seed_default_removes_stale_custom_sleeves(tmp_path: Path) -> None:
    db_path = tmp_path / "profiles.duckdb"
    migrate(db_path)

    with get_connection(db_path) as conn:
        repo = ProfileRepository(conn)
        # A prior --config run left 'default' with a non-default sleeve.
        repo.replace_policy_targets(
            "default", [PolicyTarget("default", "bonds_intl", 1.0, None, None)]
        )
        seed_default_profile(conn)
        sleeves = {t.sleeve_name for t in repo.get_policy_targets("default")}

    assert "bonds_intl" not in sleeves
    assert sleeves == {"core_us_equity", "satellite_equity", "defensive_bonds", "cash"}


def test_run_profile_init_seeds_profile_and_targets(tmp_path: Path) -> None:
    db_path = tmp_path / "profiles.duckdb"
    migrate(db_path)
    logs: list[str] = []

    with get_connection(db_path) as conn:
        run_profile_init(conn, log=logs.append)
        repo = ProfileRepository(conn)
        profile = repo.get_profile("default")
        targets = repo.get_policy_targets("default")

    assert profile is not None
    assert profile.profile_id == "default"
    assert len(targets) == 4
    assert any("default" in line for line in logs)


def test_profile_init_main_uses_configured_db_path(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "configured.duckdb"
    monkeypatch.setenv("CROESUS_DB_PATH", str(db_path))

    profile_init_main([])

    with get_connection(db_path) as conn:
        profile_count = conn.execute("SELECT COUNT(*) FROM investor_profiles").fetchone()[0]
        target_count = conn.execute("SELECT COUNT(*) FROM policy_targets").fetchone()[0]

    assert profile_count == 1
    assert target_count == 4


def test_run_profile_guided_saves_recommended_policy_after_confirmation(tmp_path: Path) -> None:
    db_path = tmp_path / "guided.duckdb"
    migrate(db_path)
    prompter = GuidedPrompter(
        {
            "name": "Guided account",
            "investment_horizon_years": 15,
            "max_tolerable_drawdown": -0.30,
            "expected_annual_return": 0.11,
        }
    )

    with get_connection(db_path) as conn:
        profile_id = run_profile_guided(conn, prompter=prompter, profile_id="guided")
        repo = ProfileRepository(conn)
        profile = repo.get_profile(profile_id)
        targets = repo.get_policy_targets(profile_id)

    assert profile is not None
    assert profile.name == "Guided account"
    assert {t.profile_id for t in targets} == {"guided"}
    assert next(t for t in targets if t.sleeve_name == "satellite_equity").target_weight == 0.20
    assert "core_us_equity.target_weight" not in prompter.prompted_keys()
    assert "save_profile" in prompter.prompted_keys()


def test_run_profile_guided_does_not_save_without_confirmation(tmp_path: Path) -> None:
    db_path = tmp_path / "guided.duckdb"
    migrate(db_path)

    with get_connection(db_path) as conn:
        with pytest.raises(RuntimeError):
            run_profile_guided(
                conn,
                prompter=GuidedPrompter(confirmed=False),
                profile_id="declined",
            )
        assert ProfileRepository(conn).get_profile("declined") is None


def test_profile_init_guided_yes_uses_configured_db_path(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "guided.duckdb"
    monkeypatch.setenv("CROESUS_DB_PATH", str(db_path))

    profile_init_main(
        ["--guided", "--yes"],
        prompter=GuidedPrompter({"name": "Guided CLI"}),
    )

    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT profile_id, name FROM investor_profiles"
        ).fetchall()
        target_count = conn.execute("SELECT COUNT(*) FROM policy_targets").fetchone()[0]

    assert len(rows) == 1
    assert rows[0][1] == "Guided CLI"
    assert target_count == 4
