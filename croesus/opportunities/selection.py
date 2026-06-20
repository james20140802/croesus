from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class OpportunityMethodology:
    key: str
    label: str
    description: str
    available: bool


class OpportunityPrompter(Protocol):
    def select(
        self,
        key: str,
        message: str,
        description: str,
        choices: list,
        default: Any,
    ) -> Any: ...


class MethodologyUnavailable(ValueError):
    pass


class QuestionaryOpportunityPrompter:
    def __init__(self) -> None:
        import questionary

        self._q = questionary

    def select(self, key, message, description, choices, default) -> Any:
        q_choices = [
            self._q.Choice(title=OPPORTUNITY_METHODOLOGIES[c].label, value=c)
            for c in choices
        ]
        answer = self._q.select(
            f"{message}\n  ({description})", choices=q_choices, default=default
        ).ask()
        if answer is None:
            raise KeyboardInterrupt
        return answer


OPPORTUNITY_METHODOLOGIES: dict[str, OpportunityMethodology] = {
    "moat_adjusted_intrinsic_value": OpportunityMethodology(
        key="moat_adjusted_intrinsic_value",
        label="Moat-adjusted intrinsic value",
        description=(
            "Methodology A: thesis grades mapped through fixed DCF knobs into "
            "bear/base/bull intrinsic-value bands."
        ),
        available=True,
    ),
    "event_driven_thesis": OpportunityMethodology(
        key="event_driven_thesis",
        label="Event-driven thesis",
        description=(
            "Methodology B: catalyst repricing thesis. Designed but not "
            "implemented yet."
        ),
        available=False,
    ),
}


def available_methodology_keys() -> list[str]:
    return [key for key, method in OPPORTUNITY_METHODOLOGIES.items() if method.available]


def select_methodology(
    methodology_key: str | None = None,
    *,
    prompter: OpportunityPrompter | None = None,
) -> OpportunityMethodology:
    if methodology_key is None:
        choices = available_methodology_keys()
        if not choices:
            raise MethodologyUnavailable("no opportunity methodologies are implemented")
        selected = (prompter or QuestionaryOpportunityPrompter()).select(
            "methodology",
            "Opportunity methodology",
            "Select the opportunity engine methodology to run.",
            choices,
            choices[0],
        )
        methodology_key = str(selected)

    methodology = OPPORTUNITY_METHODOLOGIES.get(methodology_key)
    if methodology is None:
        known = ", ".join(sorted(OPPORTUNITY_METHODOLOGIES))
        raise ValueError(f"unknown methodology {methodology_key!r}; known: {known}")
    if not methodology.available:
        raise MethodologyUnavailable(
            f"{methodology.label} is designed but not implemented yet"
        )
    return methodology
