"""Pure accounting of recorded common trades; no execution counterfactual."""

from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation
from collections.abc import Mapping


def _decimal(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def build_common_execution_delta(top5, common, top5_capital, top10_capital, *, identity_valid=True):
    """Pair signal date/instrument keys and normalize each path by its own capital.

    All numeric aggregates are withheld if identity or required recorded facts
    are invalid. Empty, known subsets contribute zero, unlike missing evidence.
    Decimal strings retain accounting precision; numeric percentages are for UI.
    """
    errors = []
    indexes = []
    capitals = [_decimal(top5_capital), _decimal(top10_capital)]
    for label, capital in zip(("top5", "top10"), capitals):
        if capital is None or capital <= 0:
            errors.append(f"{label}.initial_capital: missing_or_invalid")
    if not identity_valid:
        errors.append("selection_identity: unresolved_or_ambiguous")
    for label, rows in (("top5", top5), ("top10_common", common)):
        parsed_rows = []
        for i, row in enumerate(rows):
            prefix = f"{label}[{i}]"
            if not isinstance(row, Mapping):
                errors.append(f"{prefix}: invalid_trade")
                continue
            parsed = dict(row)
            parsed["quantity"] = row.get("shares", row.get("quantity"))
            if "shares" in row and "quantity" in row and _decimal(row["shares"]) != _decimal(row["quantity"]):
                errors.append(f"{prefix}: conflicting_shares_and_quantity")
            if not isinstance(row.get("instrument_id"), str) or not row["instrument_id"].strip():
                errors.append(f"{prefix}.instrument_id: missing_or_invalid")
            for field in ("signal_date", "entry_date", "exit_date"):
                try:
                    parsed[field] = date.fromisoformat(str(row.get(field)))
                except ValueError:
                    errors.append(f"{prefix}.{field}: missing_or_invalid")
            for field in ("net_pnl", "gross_pnl", "costs", "quantity", "entry_price", "exit_price"):
                parsed[field] = _decimal(parsed.get(field))
                if parsed[field] is None:
                    errors.append(f"{prefix}.{field}: missing_or_non_finite")
            if all(parsed.get(f) is not None for f in ("net_pnl", "gross_pnl", "costs")):
                if parsed["gross_pnl"] - parsed["costs"] != parsed["net_pnl"]:
                    errors.append(f"{prefix}: gross_minus_cost_does_not_equal_net")
            for field in ("quantity", "entry_price", "exit_price"):
                if parsed[field] is not None and parsed[field] <= 0:
                    errors.append(f"{prefix}.{field}: non_positive")
            if parsed["costs"] is not None and parsed["costs"] < 0:
                errors.append(f"{prefix}.costs: negative")
            if all(isinstance(parsed.get(f), date) for f in ("signal_date", "entry_date", "exit_date")):
                if not parsed["signal_date"] <= parsed["entry_date"] <= parsed["exit_date"]:
                    errors.append(f"{prefix}: invalid_date_order")
            parsed["key"] = (str(row.get("signal_date")), str(row.get("instrument_id")))
            parsed_rows.append(parsed)
        counts = Counter(row["key"] for row in parsed_rows)
        errors.extend(f"{label}: duplicate_key:{key[0]}|{key[1]}" for key, n in counts.items() if n > 1)
        indexes.append({row["key"]: row for row in parsed_rows})
    result = {
        "status": "unavailable" if errors else "ready",
        "pairing_key": ["signal_date", "instrument_id"],
        "basis": "each_path_net_pnl_divided_by_its_own_initial_capital",
        "contribution_unit": "percentage_points_of_initial_capital_return",
        "semantics": "recorded_trade_accounting_only; no_cash_crowding_causality_or_counterfactual_claim",
        "formula": "common_delta = matched_net_delta + top10_common_only_contribution - top5_only_contribution",
        "matched_formula": "matched_net_delta = matched_gross_delta - matched_cost_delta",
        "errors": errors,
        "matched": None,
        "top10_common_only": None,
        "top5_only": None,
        "common_execution_configuration_delta_pct": None,
        "common_execution_configuration_delta_pct_exact": None,
        "residual_pct_exact": None,
        "closed": False,
    }
    if errors:
        return result
    left, right = indexes
    cap5, cap10 = capitals
    matched_keys = sorted(left.keys() & right.keys())
    only5 = sorted(left.keys() - right.keys())
    only10 = sorted(right.keys() - left.keys())

    def contribution(index, keys, field, capital):
        return sum((index[key][field] for key in keys), Decimal(0)) / capital * 100

    def value_fields(name, value):
        return {name: float(value), name + "_exact": str(value)}

    def facts(row):
        return {field: str(row[field]) for field in (
            "signal_date", "instrument_id", "quantity", "entry_price", "exit_price",
            "entry_date", "exit_date", "gross_pnl", "costs", "net_pnl",
        )}

    pairs = []
    same_price_gross_effect = Decimal(0)
    same_price_recorded_gross_delta = Decimal(0)
    same_price_count = 0
    for key in matched_keys:
        a, b = left[key], right[key]
        same_prices = all(a[f] == b[f] for f in ("entry_price", "exit_price"))
        if same_prices:
            same_price_count += 1
            same_price_gross_effect += (b["quantity"] / cap10 - a["quantity"] / cap5) * (a["exit_price"] - a["entry_price"]) * 100
            same_price_recorded_gross_delta += b["gross_pnl"] / cap10 * 100 - a["gross_pnl"] / cap5 * 100
        pairs.append({
            "trade_key": "|".join(key), "top5": facts(a), "top10": facts(b),
            "quantity_delta": str(b["quantity"] - a["quantity"]),
            "entry_price_delta": str(b["entry_price"] - a["entry_price"]),
            "exit_price_delta": str(b["exit_price"] - a["exit_price"]),
            "entry_date_delta_calendar_days": (b["entry_date"] - a["entry_date"]).days,
            "exit_date_delta_calendar_days": (b["exit_date"] - a["exit_date"]).days,
            **{name: str(b[field] / cap10 * 100 - a[field] / cap5 * 100)
               for name, field in (("net_contribution_delta_pct_exact", "net_pnl"),
                                   ("gross_contribution_delta_pct_exact", "gross_pnl"),
                                   ("cost_delta_pct_exact", "costs"))},
        })
    deltas = {field: contribution(right, matched_keys, field, cap10)
              - contribution(left, matched_keys, field, cap5)
              for field in ("net_pnl", "gross_pnl", "costs")}
    extra5 = contribution(left, only5, "net_pnl", cap5)
    extra10 = contribution(right, only10, "net_pnl", cap10)
    total = contribution(right, right, "net_pnl", cap10) - contribution(left, left, "net_pnl", cap5)
    residual = total - (deltas["net_pnl"] + extra10 - extra5)
    result.update({
        "matched": {"trade_count": len(pairs), "trades": pairs,
                    **value_fields("net_contribution_delta_pct", deltas["net_pnl"]),
                    **value_fields("gross_contribution_delta_pct", deltas["gross_pnl"]),
                    **value_fields("cost_delta_pct", deltas["costs"]),
                    "same_price_quantity_effect": {
                        "trade_count": same_price_count,
                        "formula": "sum((top10_shares/top10_capital - top5_shares/top5_capital) * (exit_price-entry_price) * 100)",
                        "semantics": "arithmetic_identity_for_equal_recorded_prices; not_a_sizing_counterfactual",
                        **value_fields("gross_contribution_delta_pct", same_price_gross_effect),
                        "recorded_gross_minus_quantity_effect_pct_exact": str(same_price_recorded_gross_delta - same_price_gross_effect),
                        "reconciled_to_recorded_gross": abs(same_price_recorded_gross_delta - same_price_gross_effect) <= Decimal("1e-20"),
                    }},
        "top5_only": {"trade_count": len(only5), "trades": [facts(left[k]) for k in only5],
                      **value_fields("contribution_pct", extra5)},
        "top10_common_only": {"trade_count": len(only10), "trades": [facts(right[k]) for k in only10],
                             **value_fields("contribution_pct", extra10)},
        **value_fields("common_execution_configuration_delta_pct", total),
        "residual_pct_exact": str(residual),
        "closed": abs(residual) <= Decimal("1e-20") and abs(
            deltas["net_pnl"] - deltas["gross_pnl"] + deltas["costs"]
        ) <= Decimal("1e-20"),
    })
    if not result["closed"]:
        result["status"] = "partial"
    return result
