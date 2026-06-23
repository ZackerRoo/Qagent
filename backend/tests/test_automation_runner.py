from qagent.jobs.automation import run_research_automation
from qagent.providers.fixtures import FixtureMarketDataProvider

from test_state_repository import make_repo


def test_research_automation_saves_scan_brief_delivery_and_backtest(tmp_path):
    repo = make_repo(tmp_path)

    result = run_research_automation(
        repo=repo,
        provider=FixtureMarketDataProvider(),
        provider_mode="fixture",
        symbols=["US:TEST"],
        include_news=False,
        queue_brief=True,
        run_backtest=True,
    )

    assert result.summary.provider == "fixture"
    assert result.summary.symbols == 1
    assert result.summary.cards == 1
    assert result.scan_run_id.startswith("scan-")
    assert result.brief_id.startswith("brief-")
    assert result.brief_delivery_id is not None
    assert result.backtest is not None
    assert result.backtest.summary.evaluated_signals >= 1
    assert result.summary.paper_created == 1
    assert result.summary.paper_total == 1
    assert repo.list_scan_runs(limit=5)[0].run_id == result.scan_run_id
    assert repo.list_brief_runs(limit=5)[0].brief_id == result.brief_id
    assert repo.list_delivery_outbox(status="queued", limit=5)
