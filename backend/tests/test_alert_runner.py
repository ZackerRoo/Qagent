from decimal import Decimal

from qagent.jobs.alert_runner import run_alert_rules
from qagent.providers.fixtures import FixtureMarketDataProvider
from qagent.storage.repository import AlertRuleCreate

from test_state_repository import make_repo


def test_alert_runner_evaluates_latest_prices_and_queues_delivery(tmp_path):
    repo = make_repo(tmp_path)
    repo.upsert_alert_rule(
        AlertRuleCreate(
            rule_id="entry-US-TEST",
            instrument_id="US:TEST",
            kind="entry_trigger",
            operator=">=",
            threshold=Decimal("82.00"),
        )
    )

    result = run_alert_rules(
        repo=repo,
        provider=FixtureMarketDataProvider(),
        queue_delivery=True,
        recipient="local",
    )
    queued = repo.list_delivery_outbox(status="queued", limit=5)

    assert result.summary.provider == "fixture"
    assert result.summary.rules == 1
    assert result.summary.triggered == 1
    assert result.alerts[0].instrument_id == "US:TEST"
    assert result.latest_prices["US:TEST"] == Decimal("82.00")
    assert result.delivery is not None
    assert queued[0].delivery_id == result.delivery.delivery_id
    assert "entry_trigger" in queued[0].markdown
