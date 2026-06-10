from pathlib import Path

import pytest

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.profiles.config_io import write_profile_config
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
    def __init__(self, answers=None, *, confirmed=True, confirm_answers=None) -> None:
        self.answers = answers or {}
        self.confirmed = confirmed
        self.confirm_answers = confirm_answers or {}
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
        return self.confirm_answers.get(key, self.confirmed)

    def prompted_keys(self) -> set[str]:
        return {e["key"] for e in self.seen if e["key"]}


class InterruptingPrompter(GuidedPrompter):
    def confirm(self, key, message, default):
        raise KeyboardInterrupt


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
        profile_id = run_profile_guided(
            conn, prompter=prompter, profile_id="guided", skip_guidance=True
        )
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
                skip_guidance=True,
            )
        assert ProfileRepository(conn).get_profile("declined") is None


def test_profile_init_guided_yes_uses_configured_db_path(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "guided.duckdb"
    monkeypatch.setenv("CROESUS_DB_PATH", str(db_path))

    profile_init_main(
        ["--guided", "--yes"],
        prompter=GuidedPrompter({"name": "Guided CLI", "anchor_type": "가이드 건너뛰기"}),
    )

    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT profile_id, name FROM investor_profiles"
        ).fetchall()
        target_count = conn.execute("SELECT COUNT(*) FROM policy_targets").fetchone()[0]

    assert len(rows) == 1
    assert rows[0][1] == "Guided CLI"
    assert target_count == 4


def test_profile_init_guided_from_missing_file_exits_cleanly(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "guided.duckdb"
    missing = tmp_path / "missing.yaml"
    monkeypatch.setenv("CROESUS_DB_PATH", str(db_path))

    with pytest.raises(SystemExit) as excinfo:
        profile_init_main(["--guided", "--from", str(missing)])

    assert excinfo.value.code == 1


def test_profile_init_guided_from_invalid_config_exits_cleanly(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "guided.duckdb"
    bad_config = tmp_path / "bad.yaml"
    bad_config.write_text("not_profile: true\n", encoding="utf-8")
    monkeypatch.setenv("CROESUS_DB_PATH", str(db_path))

    with pytest.raises(SystemExit) as excinfo:
        profile_init_main(["--guided", "--from", str(bad_config)])

    assert excinfo.value.code == 1


def test_profile_init_interactive_from_missing_file_exits_cleanly(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "interactive.duckdb"
    missing = tmp_path / "missing.yaml"
    monkeypatch.setenv("CROESUS_DB_PATH", str(db_path))

    with pytest.raises(SystemExit) as excinfo:
        profile_init_main(["--interactive", "--from", str(missing)])

    assert excinfo.value.code == 1


def test_profile_init_interactive_from_invalid_config_exits_cleanly(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "interactive.duckdb"
    bad_config = tmp_path / "bad.yaml"
    bad_config.write_text("not_profile: true\n", encoding="utf-8")
    monkeypatch.setenv("CROESUS_DB_PATH", str(db_path))

    with pytest.raises(SystemExit) as excinfo:
        profile_init_main(["--interactive", "--from", str(bad_config)])

    assert excinfo.value.code == 1


def test_profile_init_guided_from_custom_policy_requires_replace_confirmation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "guided.duckdb"
    cfg = tmp_path / "custom.yaml"
    custom_targets = [
        PolicyTarget("default", "core_us_equity", 0.50, 0.40, 0.60),
        PolicyTarget("default", "defensive_bonds", 0.30, 0.20, 0.40),
        PolicyTarget(
            "default",
            "cash",
            0.20,
            0.10,
            0.30,
            metadata={"asset_ids": ["CASH_USD"]},
        ),
    ]
    write_profile_config(cfg, DEFAULT_PROFILE, custom_targets)
    monkeypatch.setenv("CROESUS_DB_PATH", str(db_path))

    with pytest.raises(SystemExit) as excinfo:
        profile_init_main(
            ["--guided", "--from", str(cfg)],
            prompter=GuidedPrompter(
                {"anchor_type": "가이드 건너뛰기"},
                confirm_answers={
                    "replace_policy_targets": False,
                    "save_profile": True,
                },
            ),
        )

    assert excinfo.value.code == 1

    with get_connection(db_path) as conn:
        assert ProfileRepository(conn).get_profile("default") is None


def test_profile_init_guided_keyboard_interrupt_exits_cleanly(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "guided.duckdb"
    monkeypatch.setenv("CROESUS_DB_PATH", str(db_path))

    with pytest.raises(SystemExit) as excinfo:
        profile_init_main(
            ["--guided"],
            prompter=InterruptingPrompter({"anchor_type": "가이드 건너뛰기"}),
        )

    assert excinfo.value.code == 130


# --- Sprint 003c: return-anchored guidance integration ------------------------


def test_guided_return_anchor_prefills_band_defaults(tmp_path: Path) -> None:
    db_path = tmp_path / "anchor.duckdb"
    migrate(db_path)
    # Return target lands in the growth band; no competing answers for the
    # derived fields, so the band-derived defaults flow through to the save.
    prompter = GuidedPrompter(
        {
            "anchor_type": "목표 수익률",
            "anchor_return_value": 0.075,
            "max_tolerable_drawdown": -0.375,
        }
    )

    with get_connection(db_path) as conn:
        run_profile_guided(conn, prompter=prompter, profile_id="anchored")
        profile = ProfileRepository(conn).get_profile("anchored")

    assert profile is not None
    assert 0.065 <= profile.expected_annual_return <= 0.085
    assert profile.investment_horizon_years == 7
    assert "anchor_return_value" in prompter.prompted_keys()


def test_guided_skip_guidance_matches_legacy_flow(tmp_path: Path) -> None:
    db_path = tmp_path / "skip.duckdb"
    migrate(db_path)
    prompter = GuidedPrompter({"name": "Skip test"})

    with get_connection(db_path) as conn:
        run_profile_guided(
            conn, prompter=prompter, profile_id="skip", skip_guidance=True
        )
        profile = ProfileRepository(conn).get_profile("skip")

    assert profile is not None
    assert profile.name == "Skip test"
    assert "anchor_type" not in prompter.prompted_keys()


def test_guided_conflict_resolution_keep_return_applies_return_band(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "conflict.duckdb"
    migrate(db_path)
    # Default drawdown -0.25 (balanced) vs a 0.10 return target (equity_max)
    # is a conflict; choosing keep_return pushes the profile into equity_max.
    prompter = GuidedPrompter(
        {
            "anchor_type": "목표 수익률",
            "anchor_return_value": 0.10,
            "conflict_resolution": "keep_return",
        }
    )

    with get_connection(db_path) as conn:
        run_profile_guided(conn, prompter=prompter, profile_id="conflict_r")
        profile = ProfileRepository(conn).get_profile("conflict_r")

    assert profile is not None
    assert profile.expected_annual_return >= 0.085
    assert "conflict_resolution" in prompter.prompted_keys()


def test_guided_above_band_warns_and_keeps_stated_return(tmp_path: Path) -> None:
    db_path = tmp_path / "above.duckdb"
    migrate(db_path)
    prompter = GuidedPrompter(
        {
            "anchor_type": "목표 수익률",
            "anchor_return_value": 0.50,
            "expected_annual_return": 0.50,
            "max_tolerable_drawdown": -0.55,
        }
    )

    with get_connection(db_path) as conn:
        run_profile_guided(
            conn, prompter=prompter, profile_id="above", auto_confirm=True
        )

    warnings = [
        e["message"]
        for e in prompter.seen
        if "above the highest" in e["message"].lower()
    ]
    assert warnings
    # Above-band path never prompts for a resolution.
    assert "conflict_resolution" not in prompter.prompted_keys()
