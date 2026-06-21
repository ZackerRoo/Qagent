import pandas as pd

from qagent.domain.models import Signal
from qagent.signals.breakout import detect_breakout
from qagent.signals.limit_status import detect_limit_status
from qagent.signals.pullback import detect_pullback
from qagent.signals.trend import detect_trend_strength
from qagent.signals.volume import detect_volume_anomaly


class SignalEngine:
    def generate(self, instrument_id: str, bars: pd.DataFrame) -> list[Signal]:
        if bars.empty or len(bars) < 50:
            return []

        detectors = [
            detect_trend_strength,
            detect_pullback,
            detect_breakout,
            detect_volume_anomaly,
            detect_limit_status,
        ]
        signals: list[Signal] = []
        for detector in detectors:
            signal = detector(instrument_id, bars)
            if signal is not None:
                signals.append(signal)
        return signals
