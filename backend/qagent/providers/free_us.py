class FreeUsMarketDataProvider:
    name = "free_us"

    def get_daily_bars(self, *args, **kwargs):
        raise NotImplementedError("Free US provider is implemented after fixture contracts are stable.")

    def get_snapshot(self, *args, **kwargs):
        raise NotImplementedError("Free US provider is implemented after fixture contracts are stable.")
