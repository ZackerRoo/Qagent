from datetime import date

import pandas as pd

from qagent.providers.base import MarketDataProvider


BAR_COLUMNS = ["instrument_id", "trade_date", "open", "high", "low", "close", "volume", "provider"]


class CompositeMarketDataProvider:
    def __init__(self, providers_by_market: dict[str, MarketDataProvider], name: str = "composite"):
        self.providers_by_market = providers_by_market
        self.name = name
        self.last_errors: list[str] = []

    def get_daily_bars(
        self, instrument_ids: list[str], start: date, end: date
    ) -> pd.DataFrame:
        self.last_errors = []
        frames: list[pd.DataFrame] = []
        for market, market_instruments in self._group_by_market(instrument_ids).items():
            provider = self._provider_for_market(market)
            bars = provider.get_daily_bars(market_instruments, start, end)
            self.last_errors.extend(getattr(provider, "last_errors", []))
            if not bars.empty:
                frames.append(bars)
        if not frames:
            return pd.DataFrame(columns=BAR_COLUMNS)
        return pd.concat(frames, ignore_index=True)

    def get_snapshot(self, instrument_ids: list[str]) -> pd.DataFrame:
        self.last_errors = []
        frames: list[pd.DataFrame] = []
        for market, market_instruments in self._group_by_market(instrument_ids).items():
            provider = self._provider_for_market(market)
            snapshot = provider.get_snapshot(market_instruments)
            self.last_errors.extend(getattr(provider, "last_errors", []))
            if not snapshot.empty:
                frames.append(snapshot)
        if not frames:
            return pd.DataFrame(columns=BAR_COLUMNS)
        return pd.concat(frames, ignore_index=True)

    def _provider_for_market(self, market: str) -> MarketDataProvider:
        provider = self.providers_by_market.get(market)
        if provider is None:
            raise ValueError(f"unsupported market prefix: {market}")
        return provider

    @staticmethod
    def _group_by_market(instrument_ids: list[str]) -> dict[str, list[str]]:
        grouped: dict[str, list[str]] = {}
        for instrument_id in instrument_ids:
            if ":" not in instrument_id:
                raise ValueError(f"instrument id must include market prefix: {instrument_id}")
            market = instrument_id.split(":", 1)[0].upper()
            grouped.setdefault(market, []).append(instrument_id)
        return grouped
