import pandas as pd

from qagent.domain.enums import Direction, SignalType
from qagent.domain.models import Signal
from qagent.signals._utils import observed_at_from_latest_bar


def detect_breakout(instrument_id: str, bars: pd.DataFrame) -> Signal | None:
    ordered = bars.sort_values("trade_date").reset_index(drop=True)
    if len(ordered) < 21:
        return None
    latest = ordered.iloc[-1]
    prior_high = float(ordered.iloc[-21:-1]["high"].max())
    close = float(latest["close"])
    if close <= prior_high:
        return None
    breakout_pct = close / prior_high - 1
    return Signal(
        instrument_id=instrument_id,
        signal_type=SignalType.BREAKOUT,
        direction=Direction.BULLISH,
        observed_at=observed_at_from_latest_bar(ordered),
        horizon="20d",
        score=min(round(0.6 + breakout_pct * 5, 4), 1.0),
        evidence={"close": close, "prior_20d_high": round(prior_high, 4)},
    )
