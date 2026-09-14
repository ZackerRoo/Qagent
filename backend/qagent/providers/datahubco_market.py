"""Basic-service raw daily fallback, sharing the bounded daily validation contract."""

from qagent.providers.datahubco import DatahubcoError
from qagent.providers.tushare_relay import RelayError
from qagent.providers.tushare_relay_market import TushareRelayMarketDataProvider


class DatahubcoMarketDataProvider(TushareRelayMarketDataProvider):
    name = "datahubco_daily_raw"

    def get_daily_bars(self, instrument_ids, start, end):
        result = super().get_daily_bars(instrument_ids, start, end)
        # Shared validators use RelayError; attribute diagnostics to the actual service.
        self.last_errors = [error.replace("tushare_relay:", "datahubco:")
                            for error in self.last_errors]
        return result

    get_historical_daily_bars = get_daily_bars

    def _load(self, instrument_id, start, end):
        try:
            return super()._load(instrument_id, start, end)
        except DatahubcoError as exc:
            raise RelayError(exc.kind, status_code=exc.status_code) from None
