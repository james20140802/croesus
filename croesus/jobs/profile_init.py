from __future__ import annotations

from typing import Callable

import duckdb

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.profiles.repository import ProfileRepository
from croesus.profiles.seed_default_profile import DEFAULT_PROFILE, seed_default_profile


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

    log(f"seeded profile: {profile.profile_id} ({profile.name})")
    log("policy targets:")
    for target in targets:
        log(
            f"  {target.sleeve_name}: target={target.target_weight}"
            f" min={target.min_weight} max={target.max_weight}"
        )
    return profile.profile_id


def main() -> None:
    migrate()
    with get_connection() as conn:
        run_profile_init(conn)


if __name__ == "__main__":
    main()
