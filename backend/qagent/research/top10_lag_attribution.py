"""Read-only Top 10 versus Top 5 attribution from a saved walk-forward payload."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Mapping


_DIMENSIONS = (
    "strategy",
    "factor_signal",
    "market_regime",
    "industry",
    "entry_timing",
    "exit_reason",
    "execution_constraint_evidence",
    "holding_period",
)
_RECONCILIATION_TOLERANCE_PCT = 0.001


def build_top10_lag_attribution(
    payload: Mapping[str, object],
    *,
    source_run_id: str | None = None,
    source_reproducibility_digest: str | None = None,
) -> dict[str, object]:
    """Reconcile the observed gap and describe, but do not promote, ranks 6-10."""

    base = {
        "schema_version": "top10-lag-attribution-v2",
        "scope": "shadow_only",
        "official_release_allowed": False,
        "decision_weight": False,
        "source": {
            "kind": "validated_walk_forward_result_payload",
            "run_id": source_run_id,
            "reproducibility_digest": source_reproducibility_digest,
        },
    }
    top5_portfolio = _mapping(payload.get("top_5_portfolio"))
    top10_portfolio = _mapping(payload.get("top_10_portfolio"))
    top5_trades = _list_of_mappings(top5_portfolio.get("trades"))
    top10_trades = _list_of_mappings(top10_portfolio.get("trades"))
    snapshots = _list_of_mappings(payload.get("snapshots"))
    top5_capital = _decimal(_mapping(top5_portfolio.get("summary")).get("initial_capital"))
    top10_capital = _decimal(_mapping(top10_portfolio.get("summary")).get("initial_capital"))
    top5_return = _number(_mapping(payload.get("top_5_metrics")).get("total_return_pct"))
    top10_return = _number(_mapping(payload.get("top_10_metrics")).get("total_return_pct"))
    observed_gap = _subtract(top10_return, top5_return)
    missing = []
    for present, field in (
        (top5_trades, "top_5_portfolio.trades"),
        (top10_trades, "top_10_portfolio.trades"),
        (snapshots, "snapshots"),
        (top5_capital is not None and top5_capital > 0, "top_5_portfolio.summary.initial_capital"),
        (top10_capital is not None and top10_capital > 0, "top_10_portfolio.summary.initial_capital"),
        (top5_return is not None, "top_5_metrics.total_return_pct"),
        (top10_return is not None, "top_10_metrics.total_return_pct"),
    ):
        if not present:
            missing.append(field)
    if missing:
        return _unsupported(base, observed_gap, missing)
    assert top5_capital is not None and top10_capital is not None
    assert top5_return is not None and top10_return is not None

    snapshot_index = {
        str(item.get("decision_date")): item
        for item in snapshots
        if item.get("decision_date") is not None
    }
    snapshot_mismatches = [
        str(snapshot.get("decision_date") or "unknown")
        for snapshot in snapshots
        if _ordered_selection_ids(snapshot, "top_5")
        != _ordered_selection_ids(snapshot, "top_10")[:5]
    ]
    top5_rows = [_trade_row(trade, snapshot_index, "top5") for trade in top5_trades]
    top10_rows = [_classify_top10_trade(trade, snapshot_index) for trade in top10_trades]
    common = [item for item in top10_rows if item["layer"] == "common"]
    incremental = [item for item in top10_rows if item["layer"] == "incremental"]
    unresolved = [item for item in top10_rows if item["layer"] == "unresolved"]

    top5_keys = [_trade_key(item) for item in top5_rows]
    common_keys = [_trade_key(item) for item in common]
    top5_duplicates = _duplicates(top5_keys)
    top10_duplicates = _duplicates([_trade_key(item) for item in top10_rows])
    top5_only = sorted(set(top5_keys) - set(common_keys))
    top10_common_only = sorted(set(common_keys) - set(top5_keys))

    top5_summary = _layer_summary(top5_rows, top5_capital)
    common_summary = _layer_summary(common, top10_capital)
    incremental_summary = _layer_summary(incremental, top10_capital)
    oos_window = _mapping(_mapping(payload.get("top_10_temporal_validation")).get("out_of_sample"))
    oos_start = str(oos_window.get("start_date") or "")
    oos_end = str(oos_window.get("end_date") or "")
    oos_incremental = [
        row for row in incremental if oos_start and oos_end and oos_start <= row["signal_date"] <= oos_end
    ]
    oos_incremental_summary = (
        _layer_summary(oos_incremental, top10_capital) if oos_start and oos_end else None
    )
    if oos_incremental_summary is not None:
        full_net = _decimal(incremental_summary.get("net_pnl"))
        oos_net = _decimal(oos_incremental_summary.get("net_pnl"))
        oos_incremental_summary["share_of_full_incremental_net_loss"] = (
            round(float(oos_net / full_net), 4)
            if full_net is not None and oos_net is not None and full_net < 0 and oos_net < 0
            else None
        )
    unresolved_summary = _layer_summary(unresolved, top10_capital)
    top10_total_summary = _layer_summary(top10_rows, top10_capital)
    common_delta = _subtract(common_summary["contribution_pct"], top5_summary["contribution_pct"])
    residual = _subtract(observed_gap, _add(incremental_summary["contribution_pct"], common_delta))
    top5_portfolio_residual = _subtract(top5_return, top5_summary["contribution_pct"])
    top10_portfolio_residual = _subtract(top10_return, top10_total_summary["contribution_pct"])
    gross_return_gap = _subtract(
        top10_total_summary["gross_contribution_pct"],
        top5_summary["gross_contribution_pct"],
    )
    additional_cost_pct = _subtract(top10_total_summary["cost_pct"], top5_summary["cost_pct"])
    extra_cost_drag = -additional_cost_pct if additional_cost_pct is not None else None
    reconciliation_closed = all(
        value is not None and abs(value) <= _RECONCILIATION_TOLERANCE_PCT
        for value in (residual, top5_portfolio_residual, top10_portfolio_residual)
    )
    dimensions, drags = _dimension_attribution(incremental, top10_capital)
    strict_identity = not any((snapshot_mismatches, unresolved, top5_duplicates, top10_duplicates))
    status = "ready" if strict_identity and reconciliation_closed else "partial"
    return {
        **base,
        "status": status,
        "headline": _headline(observed_gap, incremental_summary["contribution_pct"], common_delta),
        "observed_return_gap_pct": observed_gap,
        "return_gap_pct": observed_gap,
        "common_layer": common_summary,
        "top5_independent_path": top5_summary,
        "incremental_layer": incremental_summary,
        "rank_buckets": {
            "top_1_5": common_summary,
            "rank_6_10": incremental_summary,
        },
        "incremental_layer_out_of_sample": oos_incremental_summary,
        "unresolved_layer": unresolved_summary,
        "reconciliation": {
            "formula": "observed_gap = incremental_layer_contribution + common_execution_configuration_delta + residual",
            "incremental_layer_contribution_pct": incremental_summary["contribution_pct"],
            "common_execution_configuration_delta_pct": common_delta,
            "residual_pct": residual,
            "tolerance_pct": _RECONCILIATION_TOLERANCE_PCT,
            "closed": reconciliation_closed,
            "top5_portfolio_residual_pct": top5_portfolio_residual,
            "top10_portfolio_residual_pct": top10_portfolio_residual,
            "gross_return_gap_pct": gross_return_gap,
            "additional_cost_pct": additional_cost_pct,
            "extra_cost_drag_pct": extra_cost_drag,
            "gross_cost_formula": "observed_gap = gross_return_gap + extra_cost_drag + residual",
        },
        "dimensions": dimensions,
        "cost_and_execution": _cost_and_execution_attribution(
            incremental,
            incremental_summary,
        ),
        "primary_drags": drags[:8],
        "data_health": {
            "classification": status,
            "classified_trade_count": len(common) + len(incremental),
            "unresolved_trade_count": len(unresolved),
            "snapshot_prefix_match": not snapshot_mismatches,
            "snapshot_prefix_mismatch_dates": snapshot_mismatches,
            "top5_duplicate_trade_keys": top5_duplicates,
            "top10_duplicate_trade_keys": top10_duplicates,
            "top5_only_trade_keys": top5_only,
            "top10_common_only_trade_keys": top10_common_only,
            "missing_fields": [],
            "unknown_values_are_zero": False,
            "win_basis": "net_pnl_gt_zero",
            "contribution_basis": "portfolio_trade_net_pnl_divided_by_its_own_initial_capital",
            "cost_basis": "realized_trade_costs_divided_by_portfolio_initial_capital",
            "dimension_semantics": "overlapping_descriptive_groups_not_additive_across_dimensions",
            "holding_period_semantics": "realized_outcome_dimension_not_causal_signal",
            "sample_independence": "trade_rows_may_share_signal_dates; no independence claim or confidence interval",
        },
    }


def _unsupported(base, observed_gap, missing):
    return {
        **base,
        "status": "unsupported",
        "headline": "Saved result lacks fields required for strict Top 10 versus Top 5 reconciliation.",
        "observed_return_gap_pct": observed_gap,
        "return_gap_pct": observed_gap,
        "common_layer": None,
        "top5_independent_path": None,
        "incremental_layer": None,
        "rank_buckets": {"top_1_5": None, "rank_6_10": None},
        "incremental_layer_out_of_sample": None,
        "unresolved_layer": None,
        "reconciliation": {
            "formula": "observed_gap = incremental_layer_contribution + common_execution_configuration_delta + residual",
            "incremental_layer_contribution_pct": None,
            "common_execution_configuration_delta_pct": None,
            "residual_pct": None,
            "tolerance_pct": _RECONCILIATION_TOLERANCE_PCT,
            "closed": False,
            "top5_portfolio_residual_pct": None,
            "top10_portfolio_residual_pct": None,
            "gross_return_gap_pct": None,
            "additional_cost_pct": None,
            "extra_cost_drag_pct": None,
            "gross_cost_formula": "observed_gap = gross_return_gap + extra_cost_drag + residual",
        },
        "dimensions": [],
        "cost_and_execution": {
            "transaction_cost": _unavailable_metric(
                "Required portfolio trades and capital are unavailable."
            ),
            "entry_timing": _unavailable_metric(
                "Required portfolio trades and snapshots are unavailable."
            ),
            "execution_constraints": _unavailable_metric(
                "Required selection snapshots are unavailable."
            ),
        },
        "primary_drags": [],
        "data_health": {
            "classification": "unsupported",
            "missing_fields": missing,
            "unknown_values_are_zero": False,
            "dimension_semantics": "overlapping_descriptive_groups_not_additive_across_dimensions",
            "sample_independence": "unsupported",
        },
    }


def _dimension_attribution(rows, initial_capital):
    dimensions, drags = [], []
    for dimension in _DIMENSIONS:
        groups = defaultdict(list)
        for row in rows:
            values = row[dimension]
            if not isinstance(values, list):
                values = [values]
            for value in values:
                groups[str(value)].append(row)
        known_count = sum(_dimension_value_known(row[dimension]) for row in rows)
        if dimension == "factor_signal":
            known_count = sum(bool(row[dimension]) for row in rows)
        summaries = [
            {"dimension": dimension, "key": key, **_layer_summary(group, initial_capital)}
            for key, group in groups.items()
        ]
        summaries.sort(key=lambda item: (_sort_number(item["contribution_pct"]), item["key"]))
        status = _coverage_status(known_count, len(rows))
        dimensions.append(
            {
                "dimension": dimension,
                "status": status,
                "aggregation_semantics": (
                    "multi_label_overlapping_groups_not_additive"
                    if dimension == "factor_signal"
                    else "single_label_partition_within_dimension"
                ),
                "known_trade_count": known_count,
                "total_trade_count": len(rows),
                "missing_trade_count": len(rows) - known_count,
                "unavailable_reason": (
                    None
                    if status == "ready"
                    else (
                        f"No incremental trade has recorded {dimension} evidence."
                        if status == "unavailable"
                        else f"{len(rows) - known_count} incremental trades lack "
                        f"{dimension} evidence."
                    )
                ),
                "groups": summaries,
            }
        )
        if dimension != "factor_signal":
            drags.extend(
                item
                for item in summaries
                if item["contribution_pct"] is not None and item["contribution_pct"] < 0
            )
    drags.sort(key=lambda item: (_sort_number(item["contribution_pct"]), item["dimension"], item["key"]))
    return dimensions, drags


def _cost_and_execution_attribution(rows, summary):
    count = len(rows)
    cost_known = summary["field_completeness"]["costs"]["known"]
    timing_known = sum(row["entry_delay_calendar_days"] is not None for row in rows)
    constraint_known = sum(
        row["execution_constraint_evidence"] != "unknown" for row in rows
    )
    delay_values = [
        row["entry_delay_calendar_days"]
        for row in rows
        if row["entry_delay_calendar_days"] is not None
    ]
    return {
        "transaction_cost": {
            "status": _coverage_status(cost_known, count),
            "known_trade_count": cost_known,
            "total_trade_count": count,
            "missing_trade_count": count - cost_known,
            "total_costs": summary["total_costs"],
            "cost_pct": summary["cost_pct"],
            "unavailable_reason": (
                None
                if cost_known == count and count
                else (
                    "No realized transaction-cost field is recorded for ranks 6-10."
                    if not cost_known
                    else f"{count - cost_known} ranks 6-10 trades lack realized cost data."
                )
            ),
        },
        "entry_timing": {
            "status": _coverage_status(timing_known, count),
            "known_trade_count": timing_known,
            "total_trade_count": count,
            "missing_trade_count": count - timing_known,
            "basis": "calendar_days_between_signal_date_and_realized_entry_date",
            "average_entry_delay_calendar_days": (
                round(sum(delay_values) / len(delay_values), 4) if delay_values else None
            ),
            "unavailable_reason": (
                None
                if timing_known == count and count
                else (
                    "No realized entry_date is recorded for ranks 6-10."
                    if not timing_known
                    else f"{count - timing_known} ranks 6-10 trades lack entry_date."
                )
            ),
        },
        "execution_constraints": {
            "status": _coverage_status(constraint_known, count),
            "known_trade_count": constraint_known,
            "total_trade_count": count,
            "missing_trade_count": count - constraint_known,
            "scope": "executed_rank_6_10_trades_only",
            "unavailable_reason": (
                None
                if constraint_known == count and count
                else (
                    "Selection snapshots lack market or constraint-evidence fields."
                    if not constraint_known
                    else f"{count - constraint_known} ranks 6-10 trades lack constraint evidence."
                )
            ),
            "blocked_or_unfilled_candidate_attribution": "unavailable",
            "blocked_or_unfilled_candidate_reason": (
                "Portfolio trade rows contain realized trades only; rejected, untriggered, "
                "and unfilled candidates are not present in this payload."
            ),
        },
    }


def _unavailable_metric(reason):
    return {
        "status": "unavailable",
        "known_trade_count": 0,
        "total_trade_count": 0,
        "missing_trade_count": 0,
        "unavailable_reason": reason,
    }


def _coverage_status(known, total):
    if total <= 0 or known <= 0:
        return "unavailable"
    return "ready" if known == total else "partial"


def _dimension_value_known(value):
    if isinstance(value, list):
        return bool(value)
    return value != "unknown"


def _trade_row(trade, snapshots, layer):
    signal_date = str(trade.get("signal_date") or "")
    instrument_id = str(trade.get("instrument_id") or "")
    snapshot = snapshots.get(signal_date)
    selection = _selection(snapshot, "top_10", instrument_id) or _selection(snapshot, "top_5", instrument_id)
    holding_days = _integer(trade.get("holding_days"))
    entry_delay_days = _date_gap_days(signal_date, trade.get("entry_date"))
    factor_signals = [
        str(item)
        for item in (selection or {}).get("factor_signals", [])
        if str(item).strip()
    ]
    return {
        "layer": layer,
        "instrument_id": instrument_id or "unknown",
        "signal_date": signal_date or "unknown",
        "return_pct": _number(trade.get("return_pct")),
        "net_pnl": _decimal(trade.get("net_pnl")),
        "gross_pnl": _decimal(trade.get("gross_pnl")),
        "costs": _decimal(trade.get("costs")),
        "strategy": str(trade.get("strategy_id") or (selection or {}).get("primary_strategy_id") or "unknown"),
        "factor_signal": factor_signals,
        "market_regime": str((snapshot or {}).get("benchmark_trend_state") or "unknown"),
        "industry": str((selection or {}).get("industry") or "unknown"),
        "entry_timing": (
            _entry_timing_bucket(entry_delay_days)
            if entry_delay_days is not None
            else "unknown"
        ),
        "entry_delay_calendar_days": entry_delay_days,
        "exit_reason": str(trade.get("exit_reason") or "unknown"),
        "execution_constraint_evidence": _execution_constraint_evidence(
            snapshot,
            selection,
        ),
        "holding_period": _holding_period_bucket(holding_days) if holding_days is not None else "unknown",
    }


def _classify_top10_trade(trade, snapshots):
    row = _trade_row(trade, snapshots, "unresolved")
    snapshot = snapshots.get(str(trade.get("signal_date") or ""))
    instrument_id = str(trade.get("instrument_id") or "")
    if snapshot and instrument_id in set(_ordered_selection_ids(snapshot, "top_5")):
        row["layer"] = "common"
    elif snapshot and instrument_id in set(_ordered_selection_ids(snapshot, "top_10")):
        row["layer"] = "incremental"
    return row


def _layer_summary(rows, initial_capital):
    returns = [value for row in rows if (value := row.get("return_pct")) is not None]
    pnls = [value for row in rows if (value := row.get("net_pnl")) is not None]
    gross = [value for row in rows if (value := row.get("gross_pnl")) is not None]
    costs = [value for row in rows if (value := row.get("costs")) is not None]
    signal_dates = {row["signal_date"] for row in rows if row["signal_date"] != "unknown"}
    count = len(rows)
    return {
        "trade_count": count,
        "independent_signal_date_count": len(signal_dates),
        "win_rate": round(sum(value > 0 for value in pnls) / len(pnls), 4) if len(pnls) == count and rows else None,
        "average_return_pct": round(sum(returns) / len(returns), 4) if len(returns) == count and rows else None,
        "gross_pnl": str(sum(gross, Decimal("0"))) if len(gross) == count and rows else None,
        "gross_contribution_pct": round(float(sum(gross, Decimal("0")) / initial_capital * 100), 4) if len(gross) == count and rows else None,
        "net_pnl": str(sum(pnls, Decimal("0"))) if len(pnls) == count and rows else None,
        "contribution_pct": round(float(sum(pnls, Decimal("0")) / initial_capital * 100), 4) if len(pnls) == count and rows else None,
        "total_costs": str(sum(costs, Decimal("0"))) if len(costs) == count and rows else None,
        "cost_pct": round(float(sum(costs, Decimal("0")) / initial_capital * 100), 4) if len(costs) == count and rows else None,
        "field_completeness": {
            "return_pct": {"known": len(returns), "total": count},
            "gross_pnl": {"known": len(gross), "total": count},
            "net_pnl": {"known": len(pnls), "total": count},
            "costs": {"known": len(costs), "total": count},
        },
    }


def _headline(gap, incremental, common_delta):
    if any(value is None for value in (gap, incremental, common_delta)):
        return "The return gap is known, but its full reconciliation remains incomplete."
    return (
        f"Observed Top 10 minus Top 5 return gap is {gap:+.4f}%; ranks 6-10 "
        f"contributed {incremental:+.4f}% and the shared rank-1-5 execution/configuration "
        f"path difference contributed {common_delta:+.4f}%."
    )


def _trade_key(row):
    return f"{row['signal_date']}|{row['instrument_id']}"


def _duplicates(keys):
    return sorted(key for key, count in Counter(keys).items() if count > 1)


def _selection(snapshot, key, instrument_id):
    if snapshot is None:
        return None
    return next((item for item in _list_of_mappings(snapshot.get(key)) if str(item.get("instrument_id") or "") == instrument_id), None)


def _ordered_selection_ids(snapshot, key):
    return [str(item.get("instrument_id")) for item in _list_of_mappings(snapshot.get(key)) if item.get("instrument_id") is not None]


def _holding_period_bucket(days):
    if days <= 5:
        return "0-5d"
    if days <= 10:
        return "6-10d"
    if days <= 20:
        return "11-20d"
    return "20d+"


def _entry_timing_bucket(days):
    if days == 0:
        return "same_calendar_day"
    if days == 1:
        return "next_calendar_day"
    if days <= 3:
        return "2-3_calendar_days"
    return "4+_calendar_days"


def _date_gap_days(signal_date, entry_date):
    try:
        signal = date.fromisoformat(str(signal_date))
        entry = date.fromisoformat(str(entry_date))
    except (TypeError, ValueError):
        return None
    gap = (entry - signal).days
    return gap if gap >= 0 else None


def _execution_constraint_evidence(snapshot, selection):
    if snapshot is None or selection is None:
        return "unknown"
    if snapshot.get("market_entry_allowed") is False:
        return "market_entry_blocked"
    if selection.get("ranking_v4_combined_constraint_evidence_complete") is True:
        return "constraint_evidence_complete"
    if "ranking_v4_combined_constraint_evidence_complete" in selection:
        return "constraint_evidence_incomplete"
    return "unknown"


def _mapping(value):
    return value if isinstance(value, Mapping) else {}


def _list_of_mappings(value):
    return [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def _decimal(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _number(value):
    parsed = _decimal(value)
    return float(parsed) if parsed is not None else None


def _integer(value):
    parsed = _decimal(value)
    return int(parsed) if parsed is not None and parsed == parsed.to_integral_value() else None


def _subtract(left, right):
    return round(float(left) - float(right), 4) if left is not None and right is not None else None


def _add(left, right):
    return round(float(left) + float(right), 4) if left is not None and right is not None else None


def _sort_number(value):
    return float(value) if isinstance(value, (int, float)) else float("inf")


__all__ = ["build_top10_lag_attribution"]
