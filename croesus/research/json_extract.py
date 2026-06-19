from __future__ import annotations

import json
import re

# qwen3-style reasoning traces wrap deliberation in <think> tags; the JSON the
# caller wants is whatever follows.
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def extract_json_object(raw: str) -> dict:
    """Strip ``<think>`` reasoning and return the first balanced JSON object.

    Scans each ``{`` in order and decodes from there with ``raw_decode``, which
    respects brace nesting and stops at the end of the object. This tolerates
    prose with stray ``{``/``}`` both before AND after the object — a leading
    ``{company}`` no longer derails extraction, and trailing prose is ignored.

    Raises ``ValueError`` if no ``{`` begins a valid JSON object.
    """
    text = _THINK_RE.sub("", raw)
    decoder = json.JSONDecoder()
    idx = text.find("{")
    while idx != -1:
        try:
            obj, _ = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            idx = text.find("{", idx + 1)
            continue
        if isinstance(obj, dict):
            return obj
        idx = text.find("{", idx + 1)
    raise ValueError("no JSON object found in model response")
