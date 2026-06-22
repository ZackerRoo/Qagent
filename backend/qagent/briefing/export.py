from qagent.briefing.daily import DailyBrief


def render_daily_brief_markdown(brief: DailyBrief) -> str:
    lines = [
        "# Qagent Daily Brief",
        "",
        f"Generated: {brief.generated_at.isoformat()}",
        f"Provider: {brief.provider}",
        f"Symbols: {', '.join(brief.symbols)}",
        "",
        f"**Headline:** {brief.headline}",
        "",
        "## Top Opportunities",
    ]
    if brief.top_opportunities:
        for item in brief.top_opportunities:
            lines.extend(
                [
                    f"- **{item.instrument_id}** `{item.status}`",
                    f"  - Strategy: {item.primary_strategy_id or 'None'}",
                    f"  - Rank: {item.rank_score:.2f}",
                    f"  - Trigger / Stop / Target: {_level(item.trigger_price)} / "
                    f"{_level(item.initial_stop)} / {_level(item.target_1)}",
                    f"  - Risk/reward: {_number(item.risk_reward)}",
                    f"  - Scenario: {item.scenario_summary}",
                ]
            )
    else:
        lines.append("- No ranked opportunities.")

    lines.extend(["", "## Entry Watch"])
    if brief.entry_watch:
        for item in brief.entry_watch:
            lines.append(
                f"- **{item.instrument_id}** trigger {_level(item.trigger_price)}, "
                f"stop {_level(item.initial_stop)}, target {_level(item.target_1)}."
            )
    else:
        lines.append("- No entry watch items.")

    lines.extend(["", "## Strategy Validation"])
    if brief.strategy_validation:
        for item in brief.strategy_validation:
            lines.append(
                f"- **{item.strategy_id}**: samples {item.sample_count}, "
                f"target hit {_ratio(item.target_hit_rate)}, "
                f"positive 10D {_ratio(item.positive_rate_10d)}, "
                f"avg 10D {_pct(item.avg_return_10d)}."
            )
    else:
        lines.append("- No strategy validation samples.")

    lines.extend(["", "## Catalyst Watch"])
    if brief.catalyst_watch:
        for item in brief.catalyst_watch:
            lines.append(
                f"- **{item.instrument_id}** `{item.catalyst_type}`: "
                f"{item.investment_hypothesis} Verify: {item.verification_path}"
            )
    else:
        lines.append("- No catalyst hypotheses.")

    lines.extend(["", "## Risk Alerts"])
    if brief.risk_alerts:
        for item in brief.risk_alerts:
            lines.append(f"- **{item.instrument_id}** `{item.status}`: {item.message}")
    else:
        lines.append("- No position risk alerts.")

    lines.extend(["", "## Data Caveats"])
    if brief.data_caveats:
        lines.extend(f"- {item}" for item in brief.data_caveats)
    else:
        lines.append("- No data caveats.")

    lines.extend(["", "## Next Steps"])
    lines.extend(f"- {item}" for item in brief.next_steps)
    return "\n".join(lines).strip() + "\n"


def _level(value) -> str:
    return "-" if value is None else str(value)


def _number(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}%"


def _ratio(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.0f}%"
