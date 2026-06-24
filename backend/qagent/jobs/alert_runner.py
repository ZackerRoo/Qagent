from decimal import Decimal

from pydantic import BaseModel

from qagent.jobs.intraday_check import evaluate_snapshot_alerts
from qagent.market.instruments import format_instrument_label
from qagent.monitoring.alerts import Alert, AlertRule
from qagent.providers.base import MarketDataProvider
from qagent.storage.repository import DeliveryOutboxRecord, QagentRepository


class AlertRunSummary(BaseModel):
    provider: str
    rules: int
    instruments: int
    triggered: int
    queued: bool


class AlertRunResult(BaseModel):
    summary: AlertRunSummary
    alerts: list[Alert]
    latest_prices: dict[str, Decimal]
    delivery: DeliveryOutboxRecord | None = None
    data_health: dict[str, str]


def run_alert_rules(
    repo: QagentRepository,
    provider: MarketDataProvider,
    queue_delivery: bool = False,
    recipient: str | None = None,
) -> AlertRunResult:
    stored_rules = repo.list_alert_rules()
    rules = [
        AlertRule(
            rule_id=rule.rule_id,
            instrument_id=rule.instrument_id,
            kind=rule.kind,
            operator=rule.operator,
            threshold=rule.threshold,
        )
        for rule in stored_rules
    ]
    instrument_ids = sorted({rule.instrument_id for rule in rules})
    latest_prices = _latest_prices(provider, instrument_ids)
    alerts = evaluate_snapshot_alerts(latest_prices, rules)
    delivery = None
    if queue_delivery and alerts:
        delivery = repo.enqueue_delivery(
            subject=f"Qagent Alerts: {len(alerts)} triggered",
            markdown=render_alert_run_markdown(provider.name, alerts, latest_prices),
            channel="markdown",
            recipient=recipient,
            payload={
                "provider": provider.name,
                "rules": len(rules),
                "triggered": len(alerts),
                "instruments": instrument_ids,
            },
        )

    data_health = {
        "provider": provider.name,
        "rules": str(len(rules)),
        "instruments": str(len(instrument_ids)),
        "triggered": str(len(alerts)),
    }
    provider_errors = getattr(provider, "last_errors", [])
    if provider_errors:
        data_health["errors"] = " | ".join(provider_errors[:3])
    return AlertRunResult(
        summary=AlertRunSummary(
            provider=provider.name,
            rules=len(rules),
            instruments=len(instrument_ids),
            triggered=len(alerts),
            queued=delivery is not None,
        ),
        alerts=alerts,
        latest_prices=latest_prices,
        delivery=delivery,
        data_health=data_health,
    )


def render_alert_run_markdown(
    provider_name: str,
    alerts: list[Alert],
    latest_prices: dict[str, Decimal],
) -> str:
    lines = [
        "# Qagent Alert Run",
        "",
        f"Provider: {provider_name}",
        f"Triggered: {len(alerts)}",
        "",
        "## Alerts",
    ]
    if not alerts:
        lines.append("- No triggered alerts.")
    for alert in alerts:
        price = latest_prices.get(alert.instrument_id)
        lines.append(
            f"- **{format_instrument_label(alert.instrument_id)}** `{alert.kind}` {alert.rule_id}: "
            f"{alert.message}; latest {price if price is not None else '-'}."
        )
    return "\n".join(lines).strip() + "\n"


def _latest_prices(
    provider: MarketDataProvider,
    instrument_ids: list[str],
) -> dict[str, Decimal]:
    if not instrument_ids:
        return {}
    snapshot = provider.get_snapshot(instrument_ids)
    prices: dict[str, Decimal] = {}
    if snapshot.empty:
        return prices
    for _, row in snapshot.iterrows():
        prices[str(row["instrument_id"])] = Decimal(str(row["close"]))
    return prices
