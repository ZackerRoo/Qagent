from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from qagent.storage.replay_evidence import ReplayEvidenceRepository
from qagent.strategy_data.providers import BaseStrategyDataProvider


class ReplayMarketDataProvider:
    name = "historical_replay"

    def __init__(self, repository: ReplayEvidenceRepository, revision: int):
        self.repository = repository
        self.revision = revision
        self.last_errors: list[str] = []

    def get_daily_bars(
        self, instrument_ids: list[str], start: date, end: date
    ) -> pd.DataFrame:
        rows = self.repository.replay_bars(
            instrument_ids,
            start,
            end,
            self.revision,
        )
        records = []
        for item in rows:
            adjusted = all(
                value is not None
                for value in (
                    item.adjusted_open,
                    item.adjusted_high,
                    item.adjusted_low,
                    item.adjusted_close,
                )
            )
            if not adjusted and item.adjustment_mode != "none":
                self.last_errors.append(
                    f"{item.instrument_id} {item.trade_date}: adjusted OHLC is missing"
                )
                continue
            records.append(
                {
                    "instrument_id": item.instrument_id,
                    "trade_date": item.trade_date,
                    "open": float(item.raw_open),
                    "high": float(item.raw_high),
                    "low": float(item.raw_low),
                    "close": float(item.raw_close),
                    "volume": float(item.volume),
                    "turnover": (
                        float(item.turnover) if item.turnover is not None else None
                    ),
                    "provider": item.source_provider,
                    "adjusted_open": (
                        float(item.adjusted_open)
                        if item.adjusted_open is not None
                        else None
                    ),
                    "adjusted_high": (
                        float(item.adjusted_high)
                        if item.adjusted_high is not None
                        else None
                    ),
                    "adjusted_low": (
                        float(item.adjusted_low)
                        if item.adjusted_low is not None
                        else None
                    ),
                    "adjusted_close": (
                        float(item.adjusted_close)
                        if item.adjusted_close is not None
                        else None
                    ),
                    "adjustment_factor": (
                        float(item.adjustment_factor)
                        if item.adjustment_factor is not None
                        else None
                    ),
                    "adjustment_type": item.adjustment_mode,
                }
            )
        return pd.DataFrame.from_records(records)

    def get_snapshot(self, instrument_ids: list[str]) -> pd.DataFrame:
        return pd.DataFrame()

    def get_minute_bars(
        self,
        instrument_ids: list[str],
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        return pd.DataFrame()


class ReplayStrategyDataProvider(BaseStrategyDataProvider):
    name = "historical_replay_point_in_time"

    def __init__(self, repository: ReplayEvidenceRepository, revision: int):
        super().__init__()
        self.repository = repository
        self.revision = revision

    def get_fundamentals(self, instrument_ids, start, end):
        snapshots = self.repository.fundamentals_as_of(
            instrument_ids,
            end,
            self.revision,
        )
        return [
            item
            for item in snapshots.values()
            if start <= item.as_of_date <= end
        ]
