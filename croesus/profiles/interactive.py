from __future__ import annotations

from typing import Any, Callable, Protocol

from croesus.profiles.models import (
    AssetType,
    Currency,
    InvestorProfile,
    PolicyTarget,
    TradeMode,
)

_VALID_TRADE_MODES = (TradeMode.PROPOSE_ONLY, TradeMode.APPROVAL_REQUIRED)

# Per-field help shown next to each prompt (Korean — adjust freely).
FIELD_DESCRIPTIONS: dict[str, str] = {
    "profile_id": "프로필 고유 ID (여러 프로필을 구분하는 키)",
    "name": "프로필 이름 (사람이 읽는 설명)",
    "base_currency": "기준 통화 (모든 금액·비중의 기준)",
    "expected_annual_return": "기대 연수익률 (예: 0.10 = 연 10%)",
    "max_tolerable_drawdown": "감내 가능한 최대 낙폭, 음수 (예: -0.25 = 고점 대비 -25%)",
    "investment_horizon_years": "투자 기간(년)",
    "monthly_contribution": "매월 추가 납입액 (기준 통화)",
    "liquidity_buffer_months": "비상금으로 확보할 현금 개월 수",
    "allowed_asset_types": "허용 자산군: 포트폴리오에 담을 수 있는 자산 종류",
    "disallowed_asset_types": "금지 자산군: 절대 담지 않을 자산 종류",
    "max_single_position_weight": "한 종목이 차지할 수 있는 최대 비중 (0~1)",
    "max_sector_weight": "한 섹터의 최대 비중 (0~1)",
    "max_industry_weight": "한 산업의 최대 비중 (0~1)",
    "max_theme_weight": "한 테마의 최대 비중 (0~1)",
    "max_country_weight": "한 국가의 최대 비중 (0~1)",
    "max_currency_weight": "한 통화의 최대 비중 (0~1)",
    "max_monthly_turnover": "월 최대 매매 회전율 (과도한 거래 방지)",
    "rebalance_band": "리밸런싱 허용 밴드 (타깃 대비 이만큼 벗어나면 조정, 예: 0.05)",
    "trade_mode": "실행 모드: propose_only=제안만, approval_required=승인 후 실행",
}

SLEEVE_DESCRIPTIONS: dict[str, str] = {
    "core_us_equity": "핵심 미국주식",
    "satellite_equity": "위성(테마/성장) 주식",
    "defensive_bonds": "방어용 채권",
    "cash": "현금",
}


class Prompter(Protocol):
    """Interaction surface for the wizard, so the UI can be swapped/tested."""

    def info(self, message: str) -> None: ...

    def text(
        self, key: str, message: str, description: str, default: Any,
        parse: Callable[[str], Any],
    ) -> Any: ...

    def select(
        self, key: str, message: str, description: str, choices: list, default: Any
    ) -> Any: ...

    def checkbox(
        self, key: str, message: str, description: str, choices: list, default: list
    ) -> list: ...


class QuestionaryPrompter:
    """Real terminal UI: one item per screen, checkboxes for multi-select."""

    def __init__(self) -> None:
        import questionary  # lazy: only needed when actually running interactively
        from prompt_toolkit.shortcuts import clear

        self._q = questionary
        self._clear = clear

    def info(self, message: str) -> None:
        print(message)

    def text(self, key, message, description, default, parse) -> Any:
        self._clear()

        def _validate(raw: str) -> bool | str:
            try:
                parse(raw)
                return True
            except (ValueError, KeyError) as exc:
                return str(exc)

        answer = self._q.text(
            f"{message}\n  ({description})",
            default=_default_str(default),
            validate=_validate,
        ).ask()
        if answer is None:
            raise KeyboardInterrupt
        return parse(answer)

    def select(self, key, message, description, choices, default) -> Any:
        self._clear()
        q_choices = [self._q.Choice(title=_label(c), value=c) for c in choices]
        answer = self._q.select(
            f"{message}\n  ({description})", choices=q_choices, default=default
        ).ask()
        if answer is None:
            raise KeyboardInterrupt
        return answer

    def checkbox(self, key, message, description, choices, default) -> list:
        self._clear()
        chosen = set(default)
        q_choices = [
            self._q.Choice(title=_label(c), value=c, checked=c in chosen) for c in choices
        ]
        answer = self._q.checkbox(
            f"{message}\n  ({description})", choices=q_choices
        ).ask()
        if answer is None:
            raise KeyboardInterrupt
        return answer


def build_profile_interactively(
    profile_defaults: InvestorProfile,
    target_defaults: list[PolicyTarget],
    *,
    prompter: Prompter,
) -> tuple[InvestorProfile, list[PolicyTarget]]:
    """Walk the user through every profile field, then the policy targets."""
    prompter.info("투자자 프로필 설정 — 각 항목을 입력하세요 (Enter = 기본값).")
    p = profile_defaults

    def txt(field: str, default: Any, parse: Callable[[str], Any]) -> Any:
        return prompter.text(field, field, FIELD_DESCRIPTIONS[field], default, parse)

    profile = InvestorProfile(
        profile_id=txt("profile_id", p.profile_id, str),
        name=txt("name", p.name, str),
        base_currency=prompter.select(
            "base_currency", "base_currency", FIELD_DESCRIPTIONS["base_currency"],
            list(Currency), p.base_currency,
        ),
        expected_annual_return=txt(
            "expected_annual_return", p.expected_annual_return, _positive_float
        ),
        max_tolerable_drawdown=txt(
            "max_tolerable_drawdown", p.max_tolerable_drawdown, _negative_float
        ),
        investment_horizon_years=txt(
            "investment_horizon_years", p.investment_horizon_years, _positive_int
        ),
        monthly_contribution=txt(
            "monthly_contribution", p.monthly_contribution, _nonnegative_float
        ),
        liquidity_buffer_months=txt(
            "liquidity_buffer_months", p.liquidity_buffer_months, _nonnegative_float
        ),
        allowed_asset_types=prompter.checkbox(
            "allowed_asset_types", "allowed_asset_types",
            FIELD_DESCRIPTIONS["allowed_asset_types"], list(AssetType), p.allowed_asset_types,
        ),
        disallowed_asset_types=prompter.checkbox(
            "disallowed_asset_types", "disallowed_asset_types",
            FIELD_DESCRIPTIONS["disallowed_asset_types"], list(AssetType),
            p.disallowed_asset_types,
        ),
        max_single_position_weight=txt(
            "max_single_position_weight", p.max_single_position_weight, _fraction
        ),
        max_sector_weight=txt("max_sector_weight", p.max_sector_weight, _fraction),
        max_industry_weight=txt("max_industry_weight", p.max_industry_weight, _fraction),
        max_theme_weight=txt("max_theme_weight", p.max_theme_weight, _fraction),
        max_country_weight=txt("max_country_weight", p.max_country_weight, _fraction),
        max_currency_weight=txt("max_currency_weight", p.max_currency_weight, _fraction),
        max_monthly_turnover=txt(
            "max_monthly_turnover", p.max_monthly_turnover, _positive_float
        ),
        rebalance_band=txt("rebalance_band", p.rebalance_band, _positive_float),
        trade_mode=prompter.select(
            "trade_mode", "trade_mode", FIELD_DESCRIPTIONS["trade_mode"],
            list(_VALID_TRADE_MODES), p.trade_mode,
        ),
        metadata=p.metadata,
    )

    targets = _prompt_policy_targets(profile.profile_id, target_defaults, prompter)
    return profile, targets


def _prompt_policy_targets(
    profile_id: str,
    target_defaults: list[PolicyTarget],
    prompter: Prompter,
) -> list[PolicyTarget]:
    prompter.info("정책 포트폴리오 타깃 — 목표 비중의 합은 1.0이어야 합니다.")
    while True:
        targets = []
        for default in target_defaults:
            sleeve = default.sleeve_name
            desc = SLEEVE_DESCRIPTIONS.get(sleeve, "정책 슬리브")
            target_weight = prompter.text(
                f"{sleeve}.target_weight", f"{sleeve} 목표 비중",
                f"{desc} — 목표 비중 (0~1)", default.target_weight, _fraction,
            )
            min_weight = prompter.text(
                f"{sleeve}.min_weight", f"{sleeve} 최소 비중",
                f"{desc} — 최소 비중 (0~1, 없으면 none)", default.min_weight, _optional_fraction,
            )
            max_weight = prompter.text(
                f"{sleeve}.max_weight", f"{sleeve} 최대 비중",
                f"{desc} — 최대 비중 (0~1, 없으면 none)", default.max_weight, _optional_fraction,
            )
            targets.append(
                PolicyTarget(
                    profile_id=profile_id,
                    sleeve_name=sleeve,
                    target_weight=target_weight,
                    min_weight=min_weight,
                    max_weight=max_weight,
                    metadata=default.metadata,
                )
            )
        total = sum(t.target_weight for t in targets)
        if abs(total - 1.0) <= 1e-9:
            return targets
        prompter.info(f"  목표 비중 합이 {total} 입니다. 1.0이 되도록 다시 입력하세요.")


def _label(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _default_str(default: Any) -> str:
    if isinstance(default, list):
        return ", ".join(_label(item) for item in default)
    return _label(default)


def _positive_float(raw: str) -> float:
    value = float(raw)
    if value <= 0:
        raise ValueError("must be greater than 0")
    return value


def _negative_float(raw: str) -> float:
    value = float(raw)
    if value >= 0:
        raise ValueError("must be negative")
    return value


def _nonnegative_float(raw: str) -> float:
    value = float(raw)
    if value < 0:
        raise ValueError("must be 0 or greater")
    return value


def _fraction(raw: str) -> float:
    value = float(raw)
    if not 0.0 <= value <= 1.0:
        raise ValueError("must be between 0 and 1")
    return value


def _optional_fraction(raw: str) -> float | None:
    if raw.strip().lower() in {"none", "null", ""}:
        return None
    return _fraction(raw)


def _positive_int(raw: str) -> int:
    value = int(raw)
    if value < 1:
        raise ValueError("must be at least 1")
    return value
