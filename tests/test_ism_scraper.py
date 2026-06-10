from __future__ import annotations

from croesus.macro.data_sources.ism_scraper import _discover_report_urls

# Trimmed snapshot of the ISM report index page (2026), with the relevant
# month-keyed report links the scraper must discover.
_INDEX_HTML = """
<html><body>
  <a href="/login/?returnurl=/supply-management-news-and-reports/reports/ism-pmi-reports/">Login</a>
  <a href="/supply-management-news-and-reports/reports/ism-pmi-reports/pmi/april/">April Mfg</a>
  <a href="/supply-management-news-and-reports/reports/ism-pmi-reports/pmi/may/">May Mfg</a>
  <a href="/supply-management-news-and-reports/reports/ism-pmi-reports/services/april/">April Svc</a>
  <a href="/supply-management-news-and-reports/reports/ism-pmi-reports/services/may/">May Svc</a>
  <a href="/globalassets/pub/research-and-surveys/rob/pmi/irun202605pmi.pdf">PDF</a>
</body></html>
"""


def test_discover_manufacturing_report_urls() -> None:
    urls = _discover_report_urls(_INDEX_HTML, "ism_mfg_pmi")

    assert urls == [
        "https://www.ismworld.org/supply-management-news-and-reports/reports/ism-pmi-reports/pmi/april/",
        "https://www.ismworld.org/supply-management-news-and-reports/reports/ism-pmi-reports/pmi/may/",
    ]


def test_discover_services_report_urls_excludes_manufacturing() -> None:
    urls = _discover_report_urls(_INDEX_HTML, "ism_svc_pmi")

    assert all("/services/" in url for url in urls)
    assert all("/pmi/" not in url for url in urls)
    assert urls[-1].endswith("/services/may/")


def test_discover_returns_empty_when_no_links() -> None:
    assert _discover_report_urls("<html><body>no reports</body></html>", "ism_mfg_pmi") == []
