"""Prospective, cache-only observation of persisted recommendation order.

This cohort is not the walk-forward baseline model or a trading account.
"""
from __future__ import annotations

import json
import hashlib
import inspect
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from qagent.monitoring.outcomes import compute_forward_returns
from qagent.recommendations.selection import (
    EXCLUDED_STATUSES, baseline_eligible_cards, paper_eligible_card_ids, select_strategy_diversified,
)
from qagent.domain.enums import Market
from qagent.market.calendars import trading_sessions_in_range
from qagent.storage.market_cache import MarketDataCacheRepository
from qagent.storage.tables import ScanRunRow, WalkForwardRunRow


KEY = "recommendation_forward_alignment_v1"
PROTOCOL = "persisted_cards_order_strategy_cap2_top10_v1"
SOURCE = "live_scan_cards_order"


def selection_digest():
    return hashlib.sha256((inspect.getsource(baseline_eligible_cards) +
                           inspect.getsource(select_strategy_diversified) +
                           inspect.getsource(paper_eligible_card_ids) +
                           repr(sorted(EXCLUDED_STATUSES))).encode()).hexdigest()


def capture_recommendation_order(cards, item_by_instrument, *, recorded_at, data_health,
                                 governance=(), source_complete=False, benchmark_entry_allowed=None):
    """Called only when a new scan is saved; never reconstruct old selections."""
    from qagent.recommendations.rotation import sort_recommendation_cards
    from qagent.jobs.full_market import run_full_market_batch_scan_job
    ranking_digest = hashlib.sha256((inspect.getsource(sort_recommendation_cards) +
                                    inspect.getsource(run_full_market_batch_scan_job)).encode()).hexdigest()
    def entry(card, rank):
        item = item_by_instrument.get(card.instrument_id)
        signal_date = getattr(item, "latest_trade_date", None)
        return {
            "instrument_id": card.instrument_id,
            "card_id": card.card_id,
            "primary_strategy_id": card.primary_strategy_id,
            "original_rank": rank,
            "signal_date": signal_date.isoformat() if signal_date else None,
            "status": card.status.value, "rank_score": card.rank_score,
            "trigger_price": str(card.entry_plan.trigger_price) if card.entry_plan.trigger_price is not None else None,
            "initial_stop": str(card.exit_plan.initial_stop) if card.exit_plan.initial_stop is not None else None,
            "target_1": str(card.exit_plan.target_1) if card.exit_plan.target_1 is not None else None,
            "no_chase_above": str(card.entry_plan.no_chase_above) if card.entry_plan.no_chase_above is not None else None,
        }

    ordered = [entry(card, rank) for rank, card in enumerate(cards, 1)]
    local_day = recorded_at.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
    current_ids = {row["card_id"] for row in ordered if row["signal_date"] == local_day}
    eligible = baseline_eligible_cards([card for card in cards if card.card_id in current_ids], governance)
    selected = select_strategy_diversified(eligible, limit=10, max_per_strategy=2)
    if benchmark_entry_allowed is False:
        selected = []
    by_card = {row["card_id"]: row for row in ordered}
    top10 = [by_card[card.card_id] for card in selected]
    blockers = []
    if not source_complete:
        blockers.append("complete_source_unavailable")
    if any(not card.primary_strategy_id for card in eligible):
        blockers.append("strategy_identity_missing")
    if len({row["instrument_id"] for row in ordered}) != len(ordered):
        blockers.append("duplicate_instrument")
    if ordered and not current_ids:
        blockers.append("not_recorded_on_signal_date")
    return {
        "version": 1, "source": SOURCE, "protocol": PROTOCOL,
        "recorded_at": recorded_at.isoformat(), "decision_date": local_day,
        "selection_implementation_digest": selection_digest(),
        "ranking_implementation_digest": ranking_digest,
        "source_identity": {"entrypoint": "qagent.jobs.full_market.run_full_market_batch_scan_job",
                            "implementation_digest": ranking_digest,
                            "scope": "live_scan_pipeline_code_not_historical_model_authentication"},
        "source_complete": source_complete, "eligible_count": len(eligible),
        "benchmark_entry_allowed": benchmark_entry_allowed,
        "benchmark_gate_status": "captured" if isinstance(benchmark_entry_allowed, bool) else "unknown",
        "excluded_stale_cards": [row for row in ordered if row["card_id"] not in current_ids],
        "model_identity": {
            key: data_health.get(key) for key in
            ("feature_set_version", "recommendation_policy_entrypoint", "ranking_model_version")
        },
        "observed_version_fields": {key: value for key, value in data_health.items()
                                    if any(word in key for word in ("version", "digest", "entrypoint")) and key != KEY},
        "original_order": ordered, "top10": top10,
        "blockers": blockers,
    }


def compare_historical_identity(fact, reference):
    """A comparison requires a saved historical manifest, never caller assertions."""
    differences = []
    if not reference:
        differences.append("historical_identity_manifest_missing")
    else:
        for key in ("feature_set_version", "recommendation_policy_entrypoint", "ranking_model_version"):
            actual = fact.get("model_identity", {}).get(key)
            expected = reference.get("model_identity", {}).get(key)
            if not actual or not expected or actual != expected:
                differences.append(f"model_identity:{key}")
        for key in ("selection_implementation_digest", "ranking_implementation_digest"):
            if not fact.get(key) or fact.get(key) != reference.get(key):
                differences.append(key)
    if fact.get("benchmark_gate_status") != "captured":
        differences.append("benchmark_gate_unknown")
    if fact.get("excluded_stale_cards"):
        differences.append("stale_candidate_filter_differs")
    return {"comparable": not differences, "differences": differences}


def _valid_saved_fact(fact):
    if not isinstance(fact, dict):
        return False
    if not isinstance(fact.get("blockers"), list) or any(not isinstance(item, str) for item in fact["blockers"]):
        return False
    try:
        date.fromisoformat(fact["decision_date"])
    except (TypeError, ValueError, KeyError):
        return False
    for key in ("model_identity", "observed_version_fields", "source_identity"):
        if not isinstance(fact.get(key), dict):
            return False
    if any(value is not None and not isinstance(value, str) for value in fact["model_identity"].values()):
        return False
    for key in ("selection_implementation_digest", "ranking_implementation_digest", "benchmark_gate_status"):
        if not isinstance(fact.get(key), str):
            return False
    if not isinstance(fact.get("eligible_count"), int) or type(fact.get("source_complete")) is not bool:
        return False
    top10 = fact.get("top10")
    if not isinstance(top10, list) or len(top10) > 10:
        return False
    return all(isinstance(item, dict) and isinstance(item.get("instrument_id"), str)
               and isinstance(item.get("primary_strategy_id"), str)
               and item.get("signal_date") == fact["decision_date"] for item in top10)


def build_forward_alignment_report(session_factory, *, provider: str, start: date, end: date,
                                   historical_run_id: str | None = None):
    """SELECT-only access to already persisted scan facts and cached daily bars."""
    with session_factory() as session:
        reference = None
        if historical_run_id:
            historical = session.get(WalkForwardRunRow, historical_run_id)
            if historical and historical.provider == provider:
                try:
                    reference = json.loads(historical.data_health).get("recommendation_alignment_identity")
                    if isinstance(reference, str):
                        reference = json.loads(reference)
                    if not isinstance(reference, dict) or not isinstance(reference.get("model_identity"), dict):
                        reference = None
                except (TypeError, ValueError, AttributeError):
                    reference = None
        rows = session.query(ScanRunRow).filter(
            ScanRunRow.provider == provider,
            ScanRunRow.created_at >= datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc) - timedelta(hours=8),
            ScanRunRow.created_at < datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc) - timedelta(hours=8),
        ).order_by(ScanRunRow.created_at, ScanRunRow.run_id).all()
        records = []
        legacy = 0
        for row in rows:
            # SQLite stores UTC naive timestamps. Use China dates for scan eligibility.
            created = row.created_at.replace(tzinfo=timezone.utc)
            if not start <= created.astimezone(ZoneInfo("Asia/Shanghai")).date() <= end:
                continue
            try:
                health = json.loads(row.data_health)
                if not isinstance(health, dict):
                    health = {}
            except (TypeError, ValueError):
                health = {}
            raw = health.get(KEY)
            if not raw:
                legacy += 1
                continue
            try:
                fact = json.loads(raw)
                if not isinstance(fact, dict):
                    raise ValueError("not an object")
            except (TypeError, ValueError):
                fact = {"blockers": ["invalid_saved_fact"]}
            if "decision_date" in fact and fact.get("decision_date") != created.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat():
                fact = {"blockers": ["recorded_date_mismatch"]}
            records.append((row.run_id, fact))

    cache = MarketDataCacheRepository(session_factory)
    canonical = {}
    rejected = []
    for run_id, fact in records:
        if not _valid_saved_fact(fact):
            rejected.append({"run_id": run_id, "blockers": ["invalid_saved_fact", *[item for item in fact.get("blockers", []) if isinstance(item, str)]] if isinstance(fact.get("blockers"), list) else ["invalid_saved_fact"]})
            continue
        blockers = list(fact.get("blockers", [])) if isinstance(fact.get("blockers"), list) else ["invalid_saved_fact"]
        if fact.get("protocol") != PROTOCOL or fact.get("source") != SOURCE:
            blockers.append("unsupported_identity")
        top10 = fact.get("top10", [])
        if not isinstance(top10, list) or any(not isinstance(item, dict) for item in top10):
            blockers.append("invalid_saved_fact")
        required = ("decision_date", "model_identity", "observed_version_fields", "selection_implementation_digest", "eligible_count", "benchmark_gate_status")
        if any(key not in fact for key in required):
            blockers.append("invalid_saved_fact")
        if isinstance(top10, list) and any(not isinstance(item, dict) or not item.get("instrument_id") or not item.get("primary_strategy_id") or item.get("signal_date") != fact.get("decision_date") for item in top10):
            blockers.append("invalid_saved_selection")
        if not fact.get("source_complete"):
            blockers.append("complete_source_unavailable")
        if blockers:
            rejected.append({"run_id": run_id, "blockers": sorted(set(blockers))})
            continue
        canonical.setdefault(fact["decision_date"], (run_id, fact))

    cohorts = []
    for decision_day, (run_id, fact) in canonical.items():
        day = date.fromisoformat(decision_day)
        ids = [item["instrument_id"] for item in fact["top10"]]
        bars = cache.load_daily_bars(provider, ids, day, end)
        if not {"instrument_id", "trade_date", "close"}.issubset(bars.columns):
            bars = bars.iloc[:0].reindex(columns=["instrument_id", "trade_date", "close"])
        selections = []
        for rank, item in enumerate(fact["top10"], 1):
            stock_bars = bars.loc[bars["instrument_id"] == item["instrument_id"]]
            try:
                returns = compute_forward_returns(stock_bars, day, horizons=(5, 10, 20))
            except (ValueError, KeyError):
                returns = {f"return_{n}d": None for n in (5, 10, 20)}
            # Missing cache sessions must not shift a 5-session target to a later bar.
            market = Market.CN if item["instrument_id"].startswith("CN:") else Market.US
            sessions = trading_sessions_in_range(day, end, market)
            cached_dates = set(stock_bars["trade_date"])
            for horizon in (5, 10, 20):
                if len(sessions) <= horizon or not set(sessions[:horizon + 1]).issubset(cached_dates):
                    returns[f"return_{horizon}d"] = None
            selections.append({**item, "rank": rank, **returns})
        metrics = {}
        for label, subset in (("top5", selections[:5]), ("top10", selections), ("rank6_10", selections[5:])):
            metrics[label] = {}
            for horizon in (5, 10, 20):
                values = [item[f"return_{horizon}d"] for item in subset if item[f"return_{horizon}d"] is not None]
                metrics[label][str(horizon)] = {
                    "mature_count": len(values), "expected_count": len(subset),
                    "mean_return_pct": sum(values) / len(values) if values and len(values) == len(subset) else None,
                }
        cohorts.append({"run_id": run_id, "decision_date": decision_day,
                        "historical_comparison": compare_historical_identity(fact, reference),
                        "model_identity": fact["model_identity"],
                        "observed_version_fields": fact["observed_version_fields"],
                        "selection_implementation_digest": fact["selection_implementation_digest"],
                        "source_identity": fact["source_identity"],
                        "source_complete": True, "eligible_count": fact["eligible_count"],
                        "benchmark_gate_status": fact["benchmark_gate_status"],
                        "selections": selections, "metrics": metrics})
    return {
        "version": 1, "provider": provider, "source": SOURCE, "protocol": PROTOCOL,
        "read_only": True, "cache_only": True, "decision_weight": False,
        "canonical_rule": "earliest_valid_same_signal_day_scan_then_run_id; never_merge_runs",
        "historical_reference": "walk_forward._signals: baseline snapshot.top_5/top_10, strategy cap 2",
        "historical_comparison": {
            "comparable": bool(cohorts) and all(item["historical_comparison"]["comparable"] for item in cohorts),
            "reason": "see_per_cohort_identity_differences; returns_remain_descriptive_not_execution_pnl",
            "historical_run_id": historical_run_id,
        },
        "expected_historical_protocol": {
            "selection_implementation_digest": selection_digest(),
            "statuses": "shared_baseline_eligible_cards", "strategy_limit": 2,
            "market_gate": "benchmark_trend.entry_allowed",
            "required_identity_fields": ["feature_set_version", "recommendation_policy_entrypoint", "ranking_model_version"],
            "comparison_requirements": "authenticated matching historical model identity, selection digest and benchmark gate; equal selection rules alone are insufficient",
        },
        "return_basis": "signal_close_to_cached_session_close_pct; descriptive_not_executable_portfolio_pnl",
        "prospective_start": min(canonical, default=None),
        "legacy_runs_excluded": legacy, "rejected_runs": rejected, "cohorts": cohorts,
    }


def frozen_selection_signals(fact, *, run_id: str, size: int):
    """Convert frozen plans for explicit offline replay; never run an evaluator."""
    from qagent.backtesting.engine import BacktestSignal
    if size not in (5, 10) or fact.get("blockers") or not fact.get("source_complete"):
        raise ValueError("a complete captured fact and size 5 or 10 are required")
    return [BacktestSignal(
        snapshot_id=f"forward-alignment-{run_id}:{item['instrument_id']}",
        instrument_id=item["instrument_id"], signal_date=item["signal_date"],
        primary_strategy_id=item["primary_strategy_id"], status=item["status"],
        rank_score=item["rank_score"], trigger_price=item["trigger_price"],
        initial_stop=item["initial_stop"], target_1=item["target_1"],
        no_chase_above=item["no_chase_above"], outcome_status="pending",
    ) for item in fact["top10"][:size]]
