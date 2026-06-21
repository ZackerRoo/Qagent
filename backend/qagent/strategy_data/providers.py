from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Protocol

import pandas as pd

from qagent.strategy_data.models import EarningsEvent


class StrategyDataProvider(Protocol):
    name: str

    def get_earnings_events(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> list[EarningsEvent]:
        ...


class FixtureStrategyDataProvider:
    name = "fixture_strategy_data"

    def __init__(self, fixture_dir: Path | None = None):
        self.fixture_dir = fixture_dir or Path(__file__).parents[2] / "tests" / "fixtures"

    def get_earnings_events(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> list[EarningsEvent]:
        path = self.fixture_dir / "earnings_events.csv"
        if not path.exists():
            return []

        frame = pd.read_csv(path, parse_dates=["announcement_date"])
        frame["announcement_date"] = frame["announcement_date"].dt.date
        mask = (
            frame["instrument_id"].isin(instrument_ids)
            & (frame["announcement_date"] >= start)
            & (frame["announcement_date"] <= end)
        )
        events = []
        for row in frame.loc[mask].to_dict("records"):
            events.append(
                EarningsEvent(
                    instrument_id=str(row["instrument_id"]),
                    announcement_date=row["announcement_date"],
                    announcement_time=str(row["announcement_time"]),
                    actual_eps=_decimal_or_none(row.get("actual_eps")),
                    estimated_eps=_decimal_or_none(row.get("estimated_eps")),
                    actual_revenue=_decimal_or_none(row.get("actual_revenue")),
                    estimated_revenue=_decimal_or_none(row.get("estimated_revenue")),
                    guidance=_string_or_none(row.get("guidance")),
                    provider=self.name,
                )
            )
        return events


class EmptyStrategyDataProvider:
    name = "empty_strategy_data"

    def get_earnings_events(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> list[EarningsEvent]:
        return []


def build_strategy_data_provider(mode: str) -> StrategyDataProvider:
    if mode.strip().lower() == "fixture":
        return FixtureStrategyDataProvider()
    return EmptyStrategyDataProvider()


def _decimal_or_none(value: object) -> Decimal | None:
    if value is None or pd.isna(value) or value == "":
        return None
    return Decimal(str(value))


def _string_or_none(value: object) -> str | None:
    if value is None or pd.isna(value) or value == "":
        return None
    return str(value)
