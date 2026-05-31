from pathlib import Path

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.jobs.profile_init import main as profile_init_main
from croesus.jobs.profile_init import run_profile_init
from croesus.profiles.models import PolicyTarget
from croesus.profiles.repository import ProfileRepository
from croesus.profiles.seed_default_profile import (
    DEFAULT_POLICY_TARGETS,
    DEFAULT_PROFILE,
    seed_default_profile,
)
from croesus.profiles.validation import validate_policy_targets, validate_profile


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
