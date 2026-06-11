from __future__ import annotations

from typing import Protocol, TypedDict

import pandas as pd


class Financials(TypedDict):
    """Raw financial statements for one symbol.

    Each value is a DataFrame whose columns are period-end dates and whose index
    is the provider's own line-item labels. Ingestion maps those labels onto
    Croesus' stable ``metric_name`` vocabulary — the provider does not normalize.
    """

    income_annual: pd.DataFrame
    income_quarterly: pd.DataFrame
    balance_annual: pd.DataFrame
    cashflow_annual: pd.DataFrame


class FundamentalsProvider(Protocol):
    def get_financials(self, symbol: str) -> Financials:
        """Return the four financial statements for ``symbol``.

        Empty DataFrames are returned for statements the provider cannot supply;
        callers must not assume any particular label is present.
        """
