from datetime import date

import pandas as pd


def compute_forward_returns(
    bars: pd.DataFrame,
    signal_date: date,
    horizons: tuple[int, ...] = (1, 5, 10, 20, 60),
) -> dict[str, float | None]:
    ordered = bars.sort_values("trade_date").reset_index(drop=True)
    matches = ordered.index[ordered["trade_date"] == signal_date].tolist()
    if not matches:
        raise ValueError("signal_date not found in bars")

    base_index = matches[0]
    base_close = float(ordered.loc[base_index, "close"])
    result: dict[str, float | None] = {}

    for horizon in horizons:
        target_index = base_index + horizon
        key = f"return_{horizon}d"
        if target_index >= len(ordered):
            result[key] = None
        else:
            future_close = float(ordered.loc[target_index, "close"])
            result[key] = round((future_close / base_close - 1) * 100, 4)

    return result
