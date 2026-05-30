from dataclasses import replace
from pathlib import Path

import pytest

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.jobs.profile_init import main as profile_init_main
from croesus.jobs.profile_init import run_profile_load
from croesus.profiles.config_io import read_profile_config, write_profile_config
from croesus.profiles.models import AssetType, Currency, PolicyTarget, TradeMode
from croesus.profiles.repository import ProfileRepository
from croesus.profiles.seed_default_profile import (
    DEFAULT_POLICY_TARGETS,
    DEFAULT_PROFILE,
)


def _write_custom_config(path: Path, **profile_overrides: str) -> None:
    profile = DEFAULT_PROFILE
    if profile_overrides:
        from dataclasses import replace

        profile = replace(profile, **profile_overrides)
    write_profile_config(path, profile, DEFAULT_POLICY_TARGETS, overwrite=True)


def test_write_then_read_round_trips_default(tmp_path: Path) -> None:
    path = tmp_path / "profile.yaml"

    write_profile_config(path, DEFAULT_PROFILE, DEFAULT_POLICY_TARGETS)
    profile, targets = read_profile_config(path)

    assert profile == DEFAULT_PROFILE
    assert targets == DEFAULT_POLICY_TARGETS
    assert profile.base_currency is Currency.USD
    assert profile.trade_mode is TradeMode.PROPOSE_ONLY
    assert profile.allowed_asset_types[0] is AssetType.EQUITY


def test_write_refuses_overwrite_without_force(tmp_path: Path) -> None:
    path = tmp_path / "profile.yaml"
    write_profile_config(path, DEFAULT_PROFILE, DEFAULT_POLICY_TARGETS)

    with pytest.raises(FileExistsError):
        write_profile_config(path, DEFAULT_PROFILE, DEFAULT_POLICY_TARGETS)

    # overwrite=True succeeds
    write_profile_config(path, DEFAULT_PROFILE, DEFAULT_POLICY_TARGETS, overwrite=True)


def test_read_rejects_unknown_enum_value(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    write_profile_config(path, DEFAULT_PROFILE, DEFAULT_POLICY_TARGETS)
    text = path.read_text(encoding="utf-8").replace("base_currency: USD", "base_currency: XYZ")
    path.write_text(text, encoding="utf-8")

    with pytest.raises(ValueError):
        read_profile_config(path)


def test_init_config_cli_writes_editable_template(tmp_path: Path) -> None:
    out = tmp_path / "my_profile.yaml"

    profile_init_main(["--init-config", str(out)])

    assert out.exists()
    profile, targets = read_profile_config(out)
    assert profile.profile_id == "default"
    assert len(targets) == 4


def test_config_cli_loads_existing_file_and_upserts(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "configured.duckdb"
    monkeypatch.setenv("CROESUS_DB_PATH", str(db_path))
    cfg = tmp_path / "mine.yaml"
    _write_custom_config(cfg, profile_id="chase", name="My account")

    profile_init_main(["--config", str(cfg)])

    with get_connection(db_path) as conn:
        loaded = ProfileRepository(conn).get_profile("chase")

    assert loaded is not None
    assert loaded.name == "My account"


def test_config_reload_replaces_stale_policy_targets(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "configured.duckdb"
    monkeypatch.setenv("CROESUS_DB_PATH", str(db_path))
    profile_p = replace(DEFAULT_PROFILE, profile_id="p")

    # First load: the default four sleeves.
    cfg1 = tmp_path / "first.yaml"
    write_profile_config(cfg1, profile_p, DEFAULT_POLICY_TARGETS, overwrite=True)
    profile_init_main(["--config", str(cfg1)])

    # Reload with a different, smaller sleeve set (still sums to 1.0).
    two_sleeves = [
        PolicyTarget("p", "core_us_equity", 0.60, 0.50, 0.70),
        PolicyTarget("p", "defensive_bonds", 0.40, 0.30, 0.50),
    ]
    cfg2 = tmp_path / "second.yaml"
    write_profile_config(cfg2, profile_p, two_sleeves, overwrite=True)
    profile_init_main(["--config", str(cfg2)])

    with get_connection(db_path) as conn:
        sleeves = {
            row[0]
            for row in conn.execute(
                "SELECT sleeve_name FROM policy_targets WHERE profile_id = 'p'"
            ).fetchall()
        }

    assert sleeves == {"core_us_equity", "defensive_bonds"}


def test_run_profile_load_rejects_invalid_profile(tmp_path: Path) -> None:
    db_path = tmp_path / "configured.duckdb"
    migrate(db_path)
    cfg = tmp_path / "bad.yaml"
    _write_custom_config(cfg, profile_id="bad", trade_mode=TradeMode.BOUNDED_AUTO)

    with get_connection(db_path) as conn:
        with pytest.raises(ValueError):
            run_profile_load(conn, cfg, log=lambda _msg: None)
        # invalid profile must not be persisted
        assert ProfileRepository(conn).get_profile("bad") is None
