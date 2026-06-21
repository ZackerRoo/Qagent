import pandas as pd

from qagent.market.indicators import add_moving_averages, add_volume_ratio, percent_distance


def test_add_moving_averages():
    frame = pd.DataFrame({"close": list(range(1, 61))})
    result = add_moving_averages(frame, windows=(20, 50))
    assert round(result["ma_20"].iloc[-1], 2) == 50.5
    assert round(result["ma_50"].iloc[-1], 2) == 35.5


def test_percent_distance():
    assert percent_distance(110, 100) == 10.0
    assert percent_distance(90, 100) == -10.0


def test_add_volume_ratio_uses_prior_window():
    frame = pd.DataFrame({"volume": [100] * 20 + [300]})
    result = add_volume_ratio(frame, window=20)
    assert result["volume_ratio"].iloc[-1] == 3.0
