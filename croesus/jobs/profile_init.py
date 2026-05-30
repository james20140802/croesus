from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable, Sequence

import duckdb

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.profiles.config_io import read_profile_config, write_profile_config
from croesus.profiles.repository import ProfileRepository
from croesus.profiles.seed_default_profile import (
    DEFAULT_POLICY_TARGETS,
    DEFAULT_PROFILE,
    seed_default_profile,
)
from croesus.profiles.validation import validate_policy_targets, validate_profile


def run_profile_init(
    conn: duckdb.DuckDBPyConnection,
    log: Callable[[str], None] = print,
) -> str:
    """Seed the default profile + policy targets and log a summary.

    Expects an already-migrated connection. Returns the seeded profile_id.
    """
    seed_default_profile(conn)

    repo = ProfileRepository(conn)
    profile = repo.get_profile(DEFAULT_PROFILE.profile_id)
    assert profile is not None  # just seeded
    targets = repo.get_policy_targets(profile.profile_id)

    _log_summary(profile.profile_id, profile.name, targets, log)
    return profile.profile_id


def run_profile_load(
    conn: duckdb.DuckDBPyConnection,
    path: str | Path,
    log: Callable[[str], None] = print,
) -> str:
    """Load a profile config YAML, validate it, and upsert it.

    Expects an already-migrated connection. Raises ValueError (without writing)
    if the profile or its policy targets are invalid. Returns the profile_id.
    """
    profile, targets = read_profile_config(path)

    profile_result = validate_profile(profile)
    target_result = validate_policy_targets(targets)
    errors = profile_result.errors + target_result.errors
    if errors:
        raise ValueError(f"invalid profile config: {errors}")
    for warning in profile_result.warnings:
        log(f"warning: {warning}")

    repo = ProfileRepository(conn)
    repo.upsert_profile(profile)
    repo.upsert_policy_targets(targets)

    _log_summary(profile.profile_id, profile.name, targets, log)
    return profile.profile_id


def _log_summary(profile_id: str, name: str, targets, log: Callable[[str], None]) -> None:
    log(f"profile: {profile_id} ({name})")
    log("policy targets:")
    for target in targets:
        log(
            f"  {target.sleeve_name}: target={target.target_weight}"
            f" min={target.min_weight} max={target.max_weight}"
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m croesus.jobs.profile_init",
        description=(
            "Manage the investor profile. With no flags, seeds the built-in default "
            "profile. Use --init-config to scaffold an editable YAML, then --config "
            "to load an edited file into the database."
        ),
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--init-config",
        metavar="PATH",
        help="write an editable profile template to PATH (does not touch the database)",
    )
    group.add_argument(
        "--config",
        metavar="PATH",
        help="load a profile config YAML from PATH, validate it, and upsert it",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="with --init-config, overwrite PATH if it already exists",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)

    if args.init_config:
        try:
            write_profile_config(
                args.init_config,
                DEFAULT_PROFILE,
                DEFAULT_POLICY_TARGETS,
                overwrite=args.force,
            )
        except FileExistsError as exc:
            print(exc, file=sys.stderr)
            raise SystemExit(1) from exc
        print(f"wrote profile template to {args.init_config}")
        print("edit it, then load with: --config " + str(args.init_config))
        return

    migrate()
    with get_connection() as conn:
        if args.config:
            try:
                run_profile_load(conn, args.config)
            except (ValueError, FileNotFoundError) as exc:
                print(exc, file=sys.stderr)
                raise SystemExit(1) from exc
        else:
            run_profile_init(conn)


if __name__ == "__main__":
    main()
