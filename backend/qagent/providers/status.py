from pydantic import BaseModel

from qagent.config import Settings, get_settings


class ProviderStatus(BaseModel):
    provider_id: str
    name: str
    status: str
    capabilities: list[str]
    notes: str


def build_provider_status(settings: Settings | None = None) -> list[ProviderStatus]:
    settings = settings or get_settings()
    return [
        ProviderStatus(
            provider_id="fixture",
            name="Fixture data",
            status="ready",
            capabilities=["daily_ohlcv", "earnings", "fundamentals", "analyst_revisions"],
            notes="Deterministic development data for US:TEST and CN:000001.",
        ),
        ProviderStatus(
            provider_id="yfinance",
            name="Yahoo Finance via yfinance",
            status="ready",
            capabilities=["us_daily_ohlcv", "snapshots"],
            notes="Free market data; may be delayed or incomplete.",
        ),
        ProviderStatus(
            provider_id="akshare_baostock",
            name="AKShare with BaoStock fallback",
            status="ready",
            capabilities=["cn_daily_ohlcv", "snapshots", "limit_status"],
            notes="Free A-share market data with provider-dependent coverage.",
        ),
        _keyed_status(
            provider_id="alpha_vantage",
            name="Alpha Vantage",
            configured=bool(settings.alpha_vantage_api_key),
            capabilities=["earnings", "fundamentals", "ratings_snapshot"],
            notes="Used for company overview, earnings history, and current analyst ratings.",
        ),
        _keyed_status(
            provider_id="fmp",
            name="Financial Modeling Prep",
            configured=bool(settings.fmp_api_key),
            capabilities=["earnings", "fundamentals", "analyst_estimates", "price_targets"],
            notes="Needed for true analyst revision scoring when estimate history is available.",
        ),
        _keyed_status(
            provider_id="finnhub",
            name="Finnhub",
            configured=bool(settings.finnhub_api_key),
            capabilities=["earnings", "fundamentals", "recommendation_trends"],
            notes="Adds earnings calendar, basic financials, and recommendation trends.",
        ),
        ProviderStatus(
            provider_id="sec_edgar",
            name="SEC EDGAR",
            status="ready",
            capabilities=["filings", "insider_forms", "institutional_filings"],
            notes="Requires a clear SEC user agent; current config supplies one.",
        ),
        ProviderStatus(
            provider_id="cninfo",
            name="CNINFO",
            status="ready",
            capabilities=["a_share_announcements"],
            notes="Free A-share announcements; live access can be rate-limited.",
        ),
        ProviderStatus(
            provider_id="tushare",
            name="Tushare",
            status="configured" if settings.tushare_token else "missing_config",
            capabilities=["a_share_financials", "money_flow", "dragon_tiger"],
            notes="Configured by token, but deeper normalized adapters are still provider-dependent.",
        ),
    ]


def _keyed_status(
    provider_id: str,
    name: str,
    configured: bool,
    capabilities: list[str],
    notes: str,
) -> ProviderStatus:
    return ProviderStatus(
        provider_id=provider_id,
        name=name,
        status="ready" if configured else "missing_config",
        capabilities=capabilities,
        notes=notes,
    )
