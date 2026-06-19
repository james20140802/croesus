from __future__ import annotations

import json
import re

from croesus.research.thesis_models import (
    CONFIDENCE_LEVELS,
    DISRUPTION_GRADES,
    EVIDENCE_SOURCES,
    MOAT_GRADES,
    SECTOR_GRADES,
    TECH_GRADES,
)

# qwen3-style reasoning traces wrap deliberation in <think> tags; the grades are
# whatever JSON follows.
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)

# (grade key, evidence key, allowed values).
_DIMENSIONS = (
    ("moat_grade", "moat_evidence", MOAT_GRADES),
    ("tech_grade", "tech_evidence", TECH_GRADES),
    ("sector_grade", "sector_evidence", SECTOR_GRADES),
    ("disruption_grade", "disruption_evidence", DISRUPTION_GRADES),
)


def _require_str(data: dict, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"missing or empty {key!r}")
    return value.strip()


def parse_thesis_payload(raw: str) -> dict[str, str]:
    """Strip reasoning, extract the JSON object, and validate it.

    Tolerates markdown fences and prose around the object (first ``{`` to last
    ``}``). Raises ValueError on any missing field, empty evidence, or
    out-of-vocabulary grade so the grader can record a ``failed`` grade.
    """
    text = _THINK_RE.sub("", raw)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found in model response")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("model response JSON is not an object")

    payload: dict[str, str] = {}
    for grade_key, evidence_key, allowed in _DIMENSIONS:
        grade = _require_str(data, grade_key)
        if grade not in allowed:
            raise ValueError(f"{grade_key}={grade!r} not in {allowed}")
        payload[grade_key] = grade
        payload[evidence_key] = _require_str(data, evidence_key)

    payload["bear_case"] = _require_str(data, "bear_case")

    confidence = _require_str(data, "confidence")
    if confidence not in CONFIDENCE_LEVELS:
        raise ValueError(f"confidence={confidence!r} not in {CONFIDENCE_LEVELS}")
    payload["confidence"] = confidence

    source = _require_str(data, "evidence_source")
    if source not in EVIDENCE_SOURCES:
        raise ValueError(f"evidence_source={source!r} not in {EVIDENCE_SOURCES}")
    payload["evidence_source"] = source

    return payload
