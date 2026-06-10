from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Callable, Sequence

import duckdb

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.profiles.config_io import read_profile_config, write_profile_config
from croesus.profiles.guidance import (
    ABOVE_HIGHEST,
    ProfileGuidance,
    anchor_on_drawdown,
    apply_guidance_to_profile,
    apply_resolution_to_profile,
    detect_conflict,
)
from croesus.profiles.interactive import (
    Prompter,
    QuestionaryPrompter,
    _negative_float,
    _positive_float,
    build_profile_interactively,
    build_profile_inputs_interactively,
)
from croesus.profiles.models import InvestorProfile, PolicyTarget
from croesus.profiles.onboarding import recommend_policy
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
    for warning in profile_result.warnings + target_result.warnings:
        log(f"warning: {warning}")

    ProfileRepository(conn).save_profile(profile, targets)

    _log_summary(profile.profile_id, profile.name, targets, log)
    return profile.profile_id


def _new_profile_id() -> str:
    """Generate a fresh system-managed profile key (never user-typed)."""
    import uuid

    return f"profile_{uuid.uuid4().hex[:8]}"


def run_profile_interactive(
    conn: duckdb.DuckDBPyConnection,
    profile_defaults: InvestorProfile = DEFAULT_PROFILE,
    target_defaults: list[PolicyTarget] | None = None,
    *,
    prompter: Prompter | None = None,
    save_path: str | Path | None = None,
    profile_id: str | None = None,
) -> str:
    """Prompt the user for profile values, validate, and upsert.

    ``profile_id`` is system-managed: pass an existing id to update it in place
    (e.g. when editing a loaded config), or leave it None to generate a new one.
    Expects an already-migrated connection. Optionally also writes the result
    to ``save_path`` as a reusable YAML config. Returns the profile_id.
    """
    if prompter is None:
        prompter = QuestionaryPrompter()
    if target_defaults is None:
        target_defaults = DEFAULT_POLICY_TARGETS
    resolved_id = profile_id if profile_id is not None else _new_profile_id()
    prompter.info(f"profile id: {resolved_id}")

    profile, targets = build_profile_interactively(
        profile_defaults,
        target_defaults,
        prompter=prompter,
        profile_id=resolved_id,
    )

    profile_result = validate_profile(profile)
    target_result = validate_policy_targets(targets)
    errors = profile_result.errors + target_result.errors
    if errors:
        raise ValueError(f"invalid profile: {errors}")
    for warning in profile_result.warnings + target_result.warnings:
        prompter.info(f"warning: {warning}")

    ProfileRepository(conn).save_profile(profile, targets)

    if save_path is not None:
        write_profile_config(save_path, profile, targets, overwrite=True)
        prompter.info(f"saved config to {save_path}")

    _log_summary(profile.profile_id, profile.name, targets, prompter.info)
    return profile.profile_id


ANCHOR_RETURN = "목표 수익률"
ANCHOR_DRAWDOWN = "감내 가능한 손실폭"
ANCHOR_SKIP = "가이드 건너뛰기"


def _optional_positive_float(raw: str) -> float | None:
    if raw.strip().lower() in {"none", "null", ""}:
        return None
    return _positive_float(raw)


def _display_guidance(guidance: ProfileGuidance, prompter: Prompter) -> None:
    """Render a ProfileGuidance as human-readable info lines via the prompter."""
    if guidance.matched_band == ABOVE_HIGHEST:
        for warning in guidance.warnings:
            prompter.info(f"warning: {warning}")
        return

    prompter.info(f"가이드: '{guidance.matched_band}' 구간")
    if guidance.implied_return_range is not None:
        lo, hi = guidance.implied_return_range
        prompter.info(f"  예상 수익률 범위: {lo:.1%} ~ {hi:.1%}")
    if guidance.implied_drawdown_range is not None:
        worse, milder = guidance.implied_drawdown_range
        prompter.info(f"  역사적 손실폭 범위: {worse:.0%} ~ {milder:.0%}")
    prompter.info(f"  권장 최소 투자기간: {guidance.min_recommended_horizon_years}년")
    for warning in guidance.warnings:
        prompter.info(f"warning: {warning}")
    if guidance.scenarios:
        prompter.info("  역사적 사례 (근사치):")
        for line in guidance.scenarios:
            text = f"    {line.episode_year} {line.episode_label}: 약 {line.approximate_drawdown:.0%}"
            if line.currency_amount is not None and line.currency:
                text += f" ({abs(line.currency_amount):,.0f} {line.currency} 손실)"
            prompter.info(text)


def _run_return_anchor_phase(
    profile_defaults: InvestorProfile,
    *,
    prompter: Prompter,
) -> tuple[InvestorProfile, ProfileGuidance | None]:
    """Ask the return/drawdown anchor question and derive a consistent draft.

    Returns ``(draft_profile, guidance)``. The draft becomes the defaults for the
    subsequent field-by-field build, so every derived value remains editable.
    ``guidance`` is ``None`` only when the user skips guidance. Nothing is saved
    here, and ``validate_profile`` is still the only gate downstream.
    """
    anchor_type = prompter.select(
        "anchor_type",
        "어떤 기준으로 프로필을 설계할까요?",
        "목표 수익률 또는 감내 가능한 손실폭 중 하나를 고르거나, 가이드를 건너뜁니다.",
        [ANCHOR_RETURN, ANCHOR_DRAWDOWN, ANCHOR_SKIP],
        ANCHOR_RETURN,
    )
    if anchor_type == ANCHOR_SKIP:
        return profile_defaults, None

    currency = profile_defaults.base_currency.value
    portfolio_size = prompter.text(
        "portfolio_size",
        f"대략적인 포트폴리오 규모 ({currency}, 선택)",
        f"손실폭을 {currency} 금액으로 환산해 보여줍니다. 없으면 비워두세요.",
        None,
        _optional_positive_float,
    )

    if anchor_type == ANCHOR_DRAWDOWN:
        drawdown_val = prompter.text(
            "anchor_drawdown_value",
            "감내 가능한 최대 손실폭 (음수, 예: -0.20)",
            "이 수준의 하락을 견딜 수 있다고 가정합니다.",
            profile_defaults.max_tolerable_drawdown,
            _negative_float,
        )
        guidance = anchor_on_drawdown(
            drawdown_val, portfolio_size=portfolio_size, portfolio_currency=currency
        )
        _display_guidance(guidance, prompter)
        return apply_guidance_to_profile(profile_defaults, guidance), guidance

    return_val = prompter.text(
        "anchor_return_value",
        "목표 연간 수익률 (예: 0.08)",
        "이 수익률을 장기 목표로 가정합니다.",
        profile_defaults.expected_annual_return,
        _positive_float,
    )
    guidance = detect_conflict(
        return_val,
        profile_defaults.max_tolerable_drawdown,
        portfolio_size=portfolio_size,
        portfolio_currency=currency,
    )
    _display_guidance(guidance, prompter)

    if guidance.matched_band == ABOVE_HIGHEST:
        # Do not invent a draft; keep the user's stated value and let the
        # downstream validator surface its warning.
        return profile_defaults, guidance

    if guidance.conflicts:
        conflict = guidance.conflicts[0]
        prompter.info(f"warning: {conflict.description}")
        choice = prompter.select(
            "conflict_resolution",
            "목표 수익률과 손실폭이 일치하지 않습니다. 어떻게 조정할까요?",
            "선택한 옵션에 맞춰 프로필 초안을 구성합니다 (이후 단계에서 수정 가능).",
            [option.key for option in conflict.options],
            conflict.options[0].key,
        )
        option = next(o for o in conflict.options if o.key == choice)
        prompter.info(f"선택: {option.key} — {option.description}")
        return apply_resolution_to_profile(profile_defaults, option), guidance

    return apply_guidance_to_profile(profile_defaults, guidance), guidance


def run_profile_guided(
    conn: duckdb.DuckDBPyConnection,
    profile_defaults: InvestorProfile = DEFAULT_PROFILE,
    *,
    prompter: Prompter | None = None,
    save_path: str | Path | None = None,
    profile_id: str | None = None,
    existing_targets: list[PolicyTarget] | None = None,
    auto_confirm: bool = False,
    skip_guidance: bool = False,
) -> str:
    """Prompt for profile fields, recommend policy targets, and save on approval.

    When ``skip_guidance`` is False (the default), a return/drawdown anchor phase
    runs first and pre-fills the field defaults; pass ``skip_guidance=True`` to
    reproduce the pre-003c flow exactly.
    """
    if prompter is None:
        prompter = QuestionaryPrompter()
    resolved_id = profile_id if profile_id is not None else _new_profile_id()
    prompter.info(f"profile id: {resolved_id}")

    if not skip_guidance:
        profile_defaults, _ = _run_return_anchor_phase(
            profile_defaults, prompter=prompter
        )

    profile = build_profile_inputs_interactively(
        profile_defaults,
        prompter=prompter,
        profile_id=resolved_id,
    )

    profile_result = validate_profile(profile)
    if profile_result.errors:
        raise ValueError(f"invalid profile: {profile_result.errors}")

    recommendation = recommend_policy(profile)
    target_result = validate_policy_targets(recommendation.targets)
    if target_result.errors:
        raise ValueError(f"invalid recommended policy: {target_result.errors}")

    _log_recommendation(
        recommendation.template_id,
        recommendation.rationale,
        recommendation.targets,
        prompter.info,
    )
    for warning in recommendation.warnings:
        prompter.info(f"warning: {warning}")

    if existing_targets and _targets_differ(existing_targets, recommendation.targets):
        prompter.info(
            "warning: guided mode will replace policy targets loaded from --from "
            "with the recommended template targets"
        )
        if not auto_confirm and not prompter.confirm(
            "replace_policy_targets",
            "Replace the policy targets loaded from --from with the recommended template?",
            False,
        ):
            raise RuntimeError("guided profile setup cancelled before replacing targets")

    if not auto_confirm and not prompter.confirm(
        "save_profile",
        "Save this profile and recommended policy targets?",
        True,
    ):
        raise RuntimeError("guided profile setup cancelled before save")

    ProfileRepository(conn).save_profile(profile, recommendation.targets)

    if save_path is not None:
        write_profile_config(save_path, profile, recommendation.targets, overwrite=True)
        prompter.info(f"saved config to {save_path}")

    _log_summary(profile.profile_id, profile.name, recommendation.targets, prompter.info)
    return profile.profile_id


def _targets_differ(
    existing_targets: list[PolicyTarget],
    recommended_targets: list[PolicyTarget],
) -> bool:
    return {
        _target_identity(target) for target in existing_targets
    } != {_target_identity(target) for target in recommended_targets}


def _target_identity(target: PolicyTarget) -> tuple[Any, ...]:
    return (
        target.sleeve_name,
        target.target_weight,
        target.min_weight,
        target.max_weight,
        _freeze_metadata(target.metadata),
    )


def _freeze_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple(sorted((key, _freeze_metadata(item)) for key, item in value.items()))
    if isinstance(value, list):
        return tuple(_freeze_metadata(item) for item in value)
    return value


def _log_summary(profile_id: str, name: str, targets, log: Callable[[str], None]) -> None:
    log(f"profile: {profile_id} ({name})")
    log("policy targets:")
    _log_targets(targets, log)


def _log_recommendation(
    template_id: str,
    rationale: list[str],
    targets: list[PolicyTarget],
    log: Callable[[str], None],
) -> None:
    log(f"recommended policy template: {template_id}")
    for reason in rationale:
        log(f"  rationale: {reason}")
    log("proposed policy targets:")
    _log_targets(targets, log)


def _log_targets(targets: list[PolicyTarget], log: Callable[[str], None]) -> None:
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
        "-i",
        "--interactive",
        action="store_true",
        help="set up the profile interactively in the terminal (prompts for each field)",
    )
    group.add_argument(
        "--guided",
        action="store_true",
        help="set up profile fields interactively, then recommend editable policy targets",
    )
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
        "--from",
        dest="from_path",
        metavar="PATH",
        help="with --interactive or --guided, pre-fill prompts from an existing YAML config",
    )
    parser.add_argument(
        "--save",
        metavar="PATH",
        help="with --interactive or --guided, also write the result to PATH as a YAML config",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="with --guided, save the recommended policy without an interactive confirm prompt",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="with --init-config, overwrite PATH if it already exists",
    )
    return parser


def main(argv: Sequence[str] | None = None, *, prompter: Prompter | None = None) -> None:
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
        if args.interactive:
            profile_defaults, target_defaults = DEFAULT_PROFILE, DEFAULT_POLICY_TARGETS
            keep_id = None  # fresh run -> generate a new id
            try:
                if args.from_path:
                    profile_defaults, target_defaults = read_profile_config(args.from_path)
                    keep_id = profile_defaults.profile_id  # editing -> update same row
                run_profile_interactive(
                    conn,
                    profile_defaults,
                    target_defaults,
                    prompter=prompter,
                    save_path=args.save,
                    profile_id=keep_id,
                )
            except (ValueError, FileNotFoundError) as exc:
                print(exc, file=sys.stderr)
                raise SystemExit(1) from exc
            except KeyboardInterrupt as exc:
                print("cancelled", file=sys.stderr)
                raise SystemExit(130) from exc
        elif args.guided:
            profile_defaults = DEFAULT_PROFILE
            existing_targets = None
            keep_id = None  # fresh run -> generate a new id
            try:
                if args.from_path:
                    profile_defaults, existing_targets = read_profile_config(args.from_path)
                    keep_id = profile_defaults.profile_id  # editing -> update same row
                run_profile_guided(
                    conn,
                    profile_defaults,
                    prompter=prompter,
                    save_path=args.save,
                    profile_id=keep_id,
                    existing_targets=existing_targets,
                    auto_confirm=args.yes,
                )
            except (ValueError, FileNotFoundError, RuntimeError) as exc:
                print(exc, file=sys.stderr)
                raise SystemExit(1) from exc
            except KeyboardInterrupt as exc:
                print("cancelled", file=sys.stderr)
                raise SystemExit(130) from exc
        elif args.config:
            try:
                run_profile_load(conn, args.config)
            except (ValueError, FileNotFoundError) as exc:
                print(exc, file=sys.stderr)
                raise SystemExit(1) from exc
        else:
            run_profile_init(conn)


if __name__ == "__main__":
    main()
