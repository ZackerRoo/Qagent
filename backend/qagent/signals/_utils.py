from datetime import datetime, time, timezone

import pandas as pd


def observed_at_from_latest_bar(bars: pd.DataFrame) -> datetime:
    latest_date = bars.sort_values("trade_date").iloc[-1]["trade_date"]
    return datetime.combine(latest_date, time(hour=21), tzinfo=timezone.utc)
