from datetime import date

from qagent.backtesting.engine import run_historical_backtest
from qagent.briefing.daily import build_daily_brief
from qagent.catalysts.models import CatalystHypothesis
from qagent.jobs.daily_scan import run_daily_scan
from qagent.providers.fixtures import FixtureMarketDataProvider
from qagent.providers.status import build_provider_status


def test_build_daily_brief_summarizes_opportunities_validation_and_caveats():
    provider = FixtureMarketDataProvider()
    symbols = ["US:TEST", "CN:000001"]
    scan = run_daily_scan(symbols, provider)
    backtest = run_historical_backtest(
        symbols,
        provider,
        start=date(2026, 1, 30),
        end=date(2026, 3, 20),
    )
    catalysts = [
        CatalystHypothesis(
            instrument_id="US:TEST",
            news_id="news-1",
            title="US:TEST wins large AI infrastructure order",
            catalyst_type="demand",
            investment_hypothesis="Map the order to backlog and revenue before acting.",
            verification_path="Check order size, backlog, revenue timing, and margin effect.",
            confidence=0.62,
        )
    ]

    brief = build_daily_brief(
        provider="fixture",
        symbols=symbols,
        scan_result=scan,
        backtest_result=backtest,
        catalyst_hypotheses=catalysts,
        provider_statuses=build_provider_status(),
        limit=5,
    )

    assert brief.provider == "fixture"
    assert brief.symbols == symbols
    assert brief.headline
    assert brief.top_opportunities
    assert brief.entry_watch
    assert brief.strategy_validation
    assert brief.catalyst_watch[0].instrument_id == "US:TEST"
    assert brief.data_caveats
    assert brief.next_steps
    dumped = brief.model_dump_json().lower()
    assert "guarantee" not in dumped
    assert "必涨" not in dumped
