"""로드맵 ①~④ 종합 보고서 빌드 — 템플릿의 {{FIG:name}}에 PNG를 base64로 인라인.

Run from repo root (roadmap_figs 선행 필요):
  python3 -m experiments.market_signals.report.build_roadmap_report
Output: results/roadmap_report/roadmap_1_4_report.html (자기완결 단일 파일, gitignore)
"""
from __future__ import annotations

import base64
import re
from pathlib import Path

from experiments.market_signals.common.config import RESULTS_DIR

TEMPLATE = Path(__file__).with_name("roadmap_report_template.html")
FIG_DIR = RESULTS_DIR / "roadmap_report" / "fig"
OUT = RESULTS_DIR / "roadmap_report" / "roadmap_1_4_report.html"


def main() -> None:
    html = TEMPLATE.read_text(encoding="utf-8")

    def inline(match: re.Match) -> str:
        png = FIG_DIR / f"{match.group(1)}.png"
        b64 = base64.b64encode(png.read_bytes()).decode()
        return f"data:image/png;base64,{b64}"

    html, n = re.subn(r"\{\{FIG:([a-z0-9_]+)\}\}", inline, html)
    OUT.write_text(html, encoding="utf-8")
    print(f"[report] inlined {n} figures -> {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
