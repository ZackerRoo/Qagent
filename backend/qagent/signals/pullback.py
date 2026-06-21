import pandas as pd

from qagent.domain.enums import Direction, SignalType
from qagent.domain.models import Signal
from qagent.market.indicators import add_moving_averages
from qagent.signals._utils import observed_at_from_latest_bar


def detect_pullback(instrument_id: str, bars: pd.DataFrame) -> Signal | None:
    enriched = add_moving_averages(bars.sort_values("trade_date"), windows=(20, 50))
    latest = enriched.iloc[-1]
    if pd.isna(latest["ma_20"]) or pd.isna(latest["ma_50"]):
        return None

    close = float(latest["close"])
    ma_20 = float(latest["ma_20"])
    ma_50 = float(latest["ma_50"])
    near_20 = abs(close / ma_20 - 1) <= 0.03
    trend_ok = ma_20 > ma_50
    if not (near_20 and trend_ok):
        return None

    return Signal(
        instrument_id=instrument_id,
        signal_type=SignalType.PULLBACK,
        direction=Direction.BULLISH,
        observed_at=observed_at_from_latest_bar(enriched),
        horizon="20d",
        score=0.65,
        evidence={"close": close, "ma_20": round(ma_20, 4), "ma_50": round(ma_50, 4)},
    )
