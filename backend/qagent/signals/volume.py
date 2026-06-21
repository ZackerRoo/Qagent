import pandas as pd

from qagent.domain.enums import Direction, SignalType
from qagent.domain.models import Signal
from qagent.market.indicators import add_volume_ratio
from qagent.signals._utils import observed_at_from_latest_bar


def detect_volume_anomaly(instrument_id: str, bars: pd.DataFrame) -> Signal | None:
    enriched = add_volume_ratio(bars.sort_values("trade_date"), window=20)
    latest = enriched.iloc[-1]
    ratio = latest["volume_ratio"]
    if pd.isna(ratio) or float(ratio) < 1.8:
        return None
    return Signal(
        instrument_id=instrument_id,
        signal_type=SignalType.VOLUME_ANOMALY,
        direction=Direction.BULLISH,
        observed_at=observed_at_from_latest_bar(enriched),
        horizon="5d",
        score=min(round(float(ratio) / 3.0, 4), 1.0),
        evidence={"volume_ratio": float(ratio), "volume": int(latest["volume"])},
    )
