from __future__ import annotations

import re

import lxml.etree
import lxml.html

# Filing bodies (esp. 10-Ks) can be very large; cap stored text to bound DB size.
# The Phase C2 grader chunks/sections this for the model context.
MAX_TEXT_CHARS = 1_000_000

_WHITESPACE = re.compile(r"\s+")


def extract_filing_text(html: str, *, max_chars: int = MAX_TEXT_CHARS) -> str:
    """Strip an HTML filing to clean, whitespace-normalized plain text.

    Drops ``script``/``style`` content, collapses runs of whitespace to single
    spaces, and caps the result at ``max_chars``. Returns ``""`` for empty or
    unparseable input. Pure and network-free.
    """
    if not html or not html.strip():
        return ""
    try:
        doc = lxml.html.fromstring(html)
    except (lxml.etree.ParserError, ValueError):
        return ""
    for element in doc.xpath("//script | //style"):
        element.drop_tree()
    # Collect all text chunks from each element, joined with a space so adjacent
    # block elements (e.g. consecutive <p> tags) are separated rather than run
    # together. text_content() alone concatenates without separators.
    parts = [chunk for chunk in doc.itertext() if chunk and chunk.strip()]
    text = _WHITESPACE.sub(" ", " ".join(parts)).strip()
    return text[:max_chars]
