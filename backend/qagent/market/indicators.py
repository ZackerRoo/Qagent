import pandas as pd


def add_moving_averages(frame: pd.DataFrame, windows: tuple[int, ...]) -> pd.DataFrame:
    result = frame.copy()
    for window in windows:
        result[f"ma_{window}"] = result["close"].rolling(window=window).mean()
    return result


def add_volume_ratio(frame: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    result = frame.copy()
    average_volume = result["volume"].rolling(window=window).mean().shift(1)
    result["volume_ratio"] = (result["volume"] / average_volume).round(4)
    return result


def percent_distance(value: float, reference: float) -> float:
    if reference == 0:
        raise ValueError("reference cannot be zero")
    return round((value - reference) / reference * 100, 4)
