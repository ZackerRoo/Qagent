import pandas as pd

from qagent.domain.enums import Direction, SignalType
from qagent.domain.models import Signal
from qagent.signals._utils import observed_at_from_latest_bar


def detect_limit_status(instrument_id: str, bars: pd.DataFrame) -> Signal | None:
    if not instrument_id.startswith("CN:"):
        return None
    ordered = bars.sort_values("trade_date").reset_index(drop=True)
    if len(ordered) < 2:
        return None

    latest = ordered.iloc[-1]
    previous = ordered.iloc[-2]
    previous_close = float(previous["close"])
    close = float(latest["close"])
    pct_change = close / previous_close - 1
    if pct_change >= 0.095:
        direction = Direction.BULLISH
        score = min(round(0.7 + pct_change, 4), 1.0)
    elif pct_change <= -0.095:
        direction = Direction.BEARISH
        score = min(round(0.7 + abs(pct_change), 4), 1.0)
    else:
        return None

    return Signal(
        instrument_id=instrument_id,
        signal_type=SignalType.LIMIT_STATUS,
        direction=direction,
        observed_at=observed_at_from_latest_bar(ordered),
        horizon="1d",
        score=score,
        evidence={"pct_change": round(pct_change * 100, 4), "close": close},
    )
