class FreeCnMarketDataProvider:
    name = "free_cn"

    def get_daily_bars(self, *args, **kwargs):
        raise NotImplementedError("Free CN provider is implemented after fixture contracts are stable.")

    def get_snapshot(self, *args, **kwargs):
        raise NotImplementedError("Free CN provider is implemented after fixture contracts are stable.")
