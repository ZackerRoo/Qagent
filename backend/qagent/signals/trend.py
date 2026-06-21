import pandas as pd

from qagent.domain.enums import Direction, SignalType
from qagent.domain.models import Signal
from qagent.market.indicators import add_moving_averages
from qagent.signals._utils import observed_at_from_latest_bar


def detect_trend_strength(instrument_id: str, bars: pd.DataFrame) -> Signal | None:
    enriched = add_moving_averages(bars.sort_values("trade_date"), windows=(20, 50))
    latest = enriched.iloc[-1]
    if pd.isna(latest["ma_20"]) or pd.isna(latest["ma_50"]):
        return None

    close = float(latest["close"])
    ma_20 = float(latest["ma_20"])
    ma_50 = float(latest["ma_50"])
    is_uptrend = close > ma_20 > ma_50
    if not is_uptrend:
        return None

    spread = min((close / ma_50 - 1) * 4, 1.0)
    return Signal(
        instrument_id=instrument_id,
        signal_type=SignalType.TREND_STRENGTH,
        direction=Direction.BULLISH,
        observed_at=observed_at_from_latest_bar(enriched),
        horizon="20d",
        score=round(max(0.55, spread), 4),
        evidence={"close": close, "ma_20": round(ma_20, 4), "ma_50": round(ma_50, 4)},
    )
