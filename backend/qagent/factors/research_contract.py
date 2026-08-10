from __future__ import annotations


FACTOR_RESEARCH_VERSION = "factor-research-v2-online-shadow-contract"

FEATURE_COLUMNS = (
    "momentum_20",
    "momentum_60",
    "momentum_120",
    "return_5",
    "trend_slope_60",
    "trend_r2_60",
    "volatility_20",
    "downside_risk_60",
    "max_drawdown_60",
    "turnover_log_20",
    "volume_ratio_5_20",
    "distance_ma20",
    "earnings_yield",
    "return_on_equity",
    "gross_margin",
    "revenue_growth",
    "earnings_growth",
)

BASELINE_SIGNS = {
    "momentum_20": 1.0,
    "momentum_60": 1.0,
    "momentum_120": 1.0,
    "return_5": -0.5,
    "trend_slope_60": 1.0,
    "trend_r2_60": 1.0,
    "volatility_20": -1.0,
    "downside_risk_60": -1.0,
    "max_drawdown_60": 1.0,
    "turnover_log_20": 0.5,
    "volume_ratio_5_20": 0.25,
    "distance_ma20": 0.25,
    "earnings_yield": 1.0,
    "return_on_equity": 1.0,
    "gross_margin": 0.5,
    "revenue_growth": 0.5,
    "earnings_growth": 0.5,
}
