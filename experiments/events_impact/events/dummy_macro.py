"""Dummy macro category — extensibility proof of concept.

8 arbitrary dates used to demonstrate that compute_event_study()
works with any event list without code changes.
"""
from pathlib import Path

from events.schema import load_events_csv

_CSV_PATH = Path(__file__).parent / "dummy_macro.csv"


def get_events():
    return load_events_csv(_CSV_PATH, "dummy_macro")
