"""Descriptive grouping of saved matched replay fills; no provider or DB access."""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from decimal import Decimal


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, default=str).encode()).hexdigest()


def _label(value):
    return value.strip() if isinstance(value, str) and value.strip() else "unknown"


def _key(row):
    return row["signal_date"], row["instrument_id"]


def _metrics(rows, capital):
    net = sum((Decimal(str(r["net_pnl"])) for r in rows), Decimal(0))
    costs = sum((Decimal(str(r["costs"])) for r in rows), Decimal(0))
    dates = defaultdict(lambda: Decimal(0))
    for row in rows:
        dates[row["signal_date"]] += Decimal(str(row["net_pnl"]))
    return {"net_pnl": str(net), "costs": str(costs),
            "net_contribution_pp": str(net / capital * 100),
            "trade_count": len(rows), "distinct_signal_dates": len(dates),
            "win_rate": sum(Decimal(str(r["net_pnl"])) > 0 for r in rows) / len(rows) if rows else None,
            "positive_signal_date_fraction": sum(v > 0 for v in dates.values()) / len(dates) if dates else None}


def _group(rows, dimension, capital):
    groups = defaultdict(list)
    for row in rows:
        # A repeated factor label within one trade counts once. Different labels
        # overlap and are explicitly not an additive partition.
        values = row[dimension] if dimension == "factor_tags" else [row[dimension]]
        for value in sorted(set(values)):
            groups[value].append(row)
    result = {key: _metrics(value, capital) for key, value in sorted(groups.items())}
    additive = dimension != "factor_tags"
    return {"additive": additive, "groups": result,
            "cost_residual": str(Decimal(_metrics(rows, capital)["costs"]) - sum(
                (Decimal(value["costs"]) for value in result.values()), Decimal(0))) if additive else None,
            "net_residual": str(Decimal(_metrics(rows, capital)["net_pnl"]) - sum(
                (Decimal(value["net_pnl"]) for value in result.values()), Decimal(0))) if additive else None}


def build_selection_segments(source: dict, replay: dict) -> dict:
    payload = source["payload"]
    if not replay["configs_identical"]:
        raise ValueError("matched execution configurations required")
    if replay["arms"]["top_5"].get("config_digest") != replay["arms"]["top_10"].get("config_digest"):
        raise ValueError("arm configuration digests differ")
    if replay["source_snapshots_digest"] != _digest(payload["snapshots"]):
        raise ValueError("snapshot digest mismatch")
    if replay["source_run_id"] != source["run_id"]:
        raise ValueError("source run mismatch")
    windows = {}
    for arm in ("top_5", "top_10"):
        windows[arm] = [{k: w[k] for k in ("key", "start_date", "end_date")}
                        for w in payload[f"{arm}_temporal_validation"]["windows"]]
        ordered = sorted(windows[arm], key=lambda w: w["start_date"])
        if any(a["end_date"] >= b["start_date"] for a, b in zip(ordered, ordered[1:])):
            raise ValueError("overlapping historical windows")
    index = {}
    snapshot_dates = set()
    for snapshot in payload["snapshots"]:
        day = snapshot["decision_date"]
        if day in snapshot_dates:
            raise ValueError("duplicate snapshot date")
        snapshot_dates.add(day)
        if snapshot["top_5"] != snapshot["top_10"][:5]:
            raise ValueError("Top5 is not saved Top10 prefix")
        for rank, selection in enumerate(snapshot["top_10"], 1):
            key = day, selection["instrument_id"]
            if key in index:
                raise ValueError("duplicate selection identity")
            index[key] = {"rank": rank, "strategy": _label(selection.get("primary_strategy_id")),
                          "industry": _label(selection.get("industry")),
                          "regime": _label(snapshot.get("benchmark_trend_state")),
                          "factor_tags": sorted({_label(v) for v in selection.get("factor_signals") or []}) or ["unknown"]}
    enriched = {}
    for arm in ("top_5", "top_10"):
        rows = []
        seen = set()
        for trade in replay["arms"][arm]["portfolio"]["trades"]:
            key = _key(trade)
            if key in seen:
                raise ValueError("duplicate executed identity")
            seen.add(key)
            values = {field: Decimal(str(trade[field])) for field in ("gross_pnl", "net_pnl", "costs")}
            if not all(v.is_finite() for v in values.values()) or values["costs"] < 0:
                raise ValueError("invalid accounting amount")
            if values["gross_pnl"] - values["costs"] != values["net_pnl"]:
                raise ValueError("gross-cost-net mismatch")
            metadata = index.get(key, {"rank": None, "strategy": "unknown", "industry": "unknown",
                                       "regime": "unknown", "factor_tags": ["unknown"]})
            if arm == "top_5" and metadata["rank"] is not None and metadata["rank"] > 5:
                raise ValueError("Top5 execution outside saved Top5")
            row = {**trade, **metadata, "metadata_missing": key not in index}
            for origin, splits in windows.items():
                row[f"window_{origin}"] = next((w["key"] for w in splits
                    if w["start_date"] <= trade["signal_date"] <= w["end_date"]), "unknown")
            rows.append(row)
        enriched[arm] = rows
    shared = {_key(r) for r in enriched["top_5"]} & {_key(r) for r in enriched["top_10"]}
    cohorts = {"top_5": ("top_5", enriched["top_5"]), "top_10": ("top_10", enriched["top_10"]),
               "rank_6_10": ("top_10", [r for r in enriched["top_10"] if r["rank"] is not None and r["rank"] > 5]),
               "top10_common_selection": ("top_10", [r for r in enriched["top_10"] if r["rank"] is not None and r["rank"] <= 5]),
               "top10_unknown_rank": ("top_10", [r for r in enriched["top_10"] if r["rank"] is None]),
               "shared_top5": ("top_5", [r for r in enriched["top_5"] if _key(r) in shared]),
               "shared_top10": ("top_10", [r for r in enriched["top_10"] if _key(r) in shared])}
    result = {}
    for name, (arm, rows) in cohorts.items():
        capital = Decimal(str(replay["arms"][arm]["summary"]["initial_capital"]))
        if not capital.is_finite() or capital <= 0:
            raise ValueError("invalid initial capital")
        result[name] = {"execution_arm": arm, "total": _metrics(rows, capital),
                        "missing_metadata_trade_count": sum(r["metadata_missing"] for r in rows),
                        "dimensions": {d: _group(rows, d, capital) for d in
                            ("strategy", "industry", "regime", "factor_tags", "window_top_5", "window_top_10")},
                        "strategy_by_historical_window": {origin: {
                            label: _group([r for r in rows if r[f"window_{origin}"] == label], "strategy", capital)
                            for label in [w["key"] for w in windows[origin]] + ["unknown"]}
                            for origin in windows}}
    residual = Decimal(result["top_10"]["total"]["net_pnl"]) - sum(
        (Decimal(result[k]["total"]["net_pnl"]) for k in
         ("rank_6_10", "top10_common_selection", "top10_unknown_rank")), Decimal(0))
    return {"schema": "matched-selection-segments-v1", "source_run_id": source["run_id"],
            "source_snapshots_digest": replay["source_snapshots_digest"], "historical_windows": windows,
            "cohorts": result, "top10_partition_net_residual": str(residual),
            "limitations": ["Descriptive retrospective slicing, not new OOS or forward evidence; no fitted weights.",
                "Only closed executed trades: contribution is net pnl / initial arm capital, not standalone segment return or alpha.",
                "distinct_signal_dates counts unique dates, not statistically independent observations; holdings and dates remain correlated.",
                "primary_strategy is exclusive; factor_tags overlap, are deduplicated within a trade and must never be summed across tags.",
                "Dimensions and shared cohorts overlap and cannot be added; Top10 partitions into common selection, rank6-10, unknown rank.",
                "shared means same signal_date/instrument_id closed in both arms; amounts and exits may differ.",
                "Historical window unknown includes embargo gaps/outside intervals; missing metadata is retained as unknown.",
                "Industry/regime are saved snapshot labels; unknown is not reconstructed using later data."]}
