"""FOMC meeting dates loader.

Primary: scrape Fed calendar and historical pages.
Fallback: curated fomc_dates.csv committed to this repo.
"""
import re
import sys
import datetime
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

from config import DB_PATH
from events.schema import load_events_csv

_CSV_PATH = Path(__file__).parent / "fomc_dates.csv"
_HEADERS = {"User-Agent": "Mozilla/5.0 (event-study research)"}
_TIMEOUT = 12
_MIN_SCRAPED = 50  # fall back to CSV if we scrape fewer dates than this


def _scrape_historical(year: int) -> list[datetime.date]:
    url = f"https://www.federalreserve.gov/monetarypolicy/fomchistorical{year}.htm"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        if resp.status_code != 200:
            return []
        soup = BeautifulSoup(resp.text, "html.parser")
        dates = []
        for a in soup.find_all("a", attrs={"name": True}):
            name = a["name"]
            m = re.match(r"^(\d{4})(\d{2})(\d{2})$", name)
            if m:
                d = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                if d.year == year:
                    dates.append(d)
        return dates
    except Exception:
        return []


def _scrape_calendar() -> list[datetime.date]:
    url = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        if resp.status_code != 200:
            return []
        soup = BeautifulSoup(resp.text, "html.parser")
        current_year = datetime.date.today().year
        dates = []
        for a in soup.find_all("a", attrs={"name": True}):
            name = a["name"]
            m = re.match(r"^(\d{4})(\d{2})(\d{2})$", name)
            if m:
                d = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                if d.year >= current_year - 1:
                    dates.append(d)
        return dates
    except Exception:
        return []


def _scrape_fomc_dates() -> list[datetime.date]:
    current_year = datetime.date.today().year
    dates: list[datetime.date] = []
    for year in range(2010, current_year):
        year_dates = _scrape_historical(year)
        dates.extend(year_dates)
    dates.extend(_scrape_calendar())
    return sorted(set(dates))


def _save_to_duckdb(df: pd.DataFrame) -> None:
    import duckdb
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            date DATE,
            category TEXT,
            magnitude DOUBLE,
            scope TEXT,
            metadata JSON,
            PRIMARY KEY (date, category)
        )
    """)
    conn.execute("""
        INSERT OR REPLACE INTO events
        SELECT date, category, magnitude, scope, metadata FROM df
    """)
    conn.close()


def get_events() -> pd.DataFrame:
    """Return FOMC meeting dates conforming to the standard event schema."""
    scraped = _scrape_fomc_dates()
    if len(scraped) >= _MIN_SCRAPED:
        print(f"[fomc] scraped {len(scraped)} dates from Fed website", file=sys.stderr)
        df = pd.DataFrame({"date": scraped})
        df["category"] = "fomc"
        df["magnitude"] = float("nan")
        df["scope"] = "US"
        df["metadata"] = None
        df["regime"] = None
        df["is_emergency"] = False
        # merge magnitude/metadata/regime/is_emergency from curated CSV where available
        csv_df = load_events_csv(_CSV_PATH, "fomc")
        merge_cols = [c for c in ["magnitude", "metadata", "regime", "is_emergency"]
                      if c in csv_df.columns]
        csv_lookup = csv_df.set_index("date")[merge_cols]
        df["date_key"] = df["date"]
        df = df.set_index("date_key")
        df.update(csv_lookup)
        df = df.reset_index(drop=True)
        if "regime" not in df.columns:
            df["regime"] = None
        if "is_emergency" not in df.columns:
            df["is_emergency"] = False
        df["is_emergency"] = df["is_emergency"].fillna(False).astype(bool)
    else:
        if scraped:
            print(
                f"[fomc] only scraped {len(scraped)} dates; falling back to CSV",
                file=sys.stderr,
            )
        else:
            print("[fomc] scraping failed; using curated CSV", file=sys.stderr)
        df = load_events_csv(_CSV_PATH, "fomc")

    _save_to_duckdb(df)
    return df
