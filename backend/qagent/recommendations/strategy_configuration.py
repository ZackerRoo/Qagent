from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from qagent.storage.paper import PaperAccountSettings


PAPER_STRATEGY_CONFIGURATION_SCHEMA_VERSION = "paper-strategy-configuration-v1"


def build_paper_strategy_configuration(
    *,
    provider: str,
    signal_date: date,
    symbols: Sequence[str],
    include_etfs: bool,
    feature_set_version: str,
    recommendation_policy: str,
    calibration_merge_policy: str,
    quality_weights: Mapping[str, float],
    governance_source: str,
    governance_strategies: Mapping[str, object],
    account: PaperAccountSettings,
) -> tuple[dict[str, object], str]:
    """Build a canonical, scan-time strategy recipe for research paper trades."""

    normalized_symbols = sorted({str(symbol).strip() for symbol in symbols if str(symbol).strip()})
    strategies = [
        _strategy_payload(strategy_id, value)
        for strategy_id, value in sorted(governance_strategies.items())
    ]
    configuration: dict[str, object] = {
        "schema_version": PAPER_STRATEGY_CONFIGURATION_SCHEMA_VERSION,
        "provider": provider.strip().lower(),
        "signal_date": signal_date.isoformat(),
        "universe": {
            "symbol_count": len(normalized_symbols),
            "symbols_digest": _canonical_digest(normalized_symbols),
            "include_etfs": bool(include_etfs),
            "ranking_scope": "full_card_universe",
            "ranking_normalization": "global_second_pass",
            "tie_breaker": "instrument_id_asc",
        },
        "ranking": {
            "feature_set_version": feature_set_version,
            "recommendation_policy": recommendation_policy,
            "calibration_merge_policy": calibration_merge_policy,
            "quality_score_version": "quality_v2",
            "quality_weights": {key: float(value) for key, value in sorted(quality_weights.items())},
        },
        "selection": {
            "portfolio_head_limit": 10,
            "max_per_strategy": 2,
            "candidate_admission": "strategy_governance_paper_candidate_eligible",
        },
        "governance": {
            "source": governance_source,
            "strategies": strategies,
        },
        "execution": {
            "market": "a_share",
            "entry_wait_sessions": 10,
            "max_holding_sessions": 20,
            "account_session_id": account.session_id,
            "allocation_per_trade_pct": _decimal_text(account.allocation_per_trade_pct),
            "max_positions": account.max_positions,
            "transaction_cost_bps": _decimal_text(account.transaction_cost_bps),
            "slippage_bps": _decimal_text(account.slippage_bps),
            "take_profit_pct": _decimal_text(account.take_profit_pct),
            "execution_rules": "a_share_t_plus_one_lot_tick_limit_aware",
        },
    }
    return configuration, _canonical_digest(configuration)


def parse_paper_strategy_configuration(
    raw_configuration: object,
    raw_digest: object,
) -> tuple[dict[str, object], str] | None:
    """Accept only the canonical configuration persisted by a completed scan."""

    if not isinstance(raw_configuration, str) or not isinstance(raw_digest, str):
        return None
    expected_digest = raw_digest.strip()
    if len(expected_digest) != 64:
        return None
    try:
        configuration = json.loads(raw_configuration)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(configuration, dict):
        return None
    if configuration.get("schema_version") != PAPER_STRATEGY_CONFIGURATION_SCHEMA_VERSION:
        return None
    if _canonical_digest(configuration) != expected_digest:
        return None
    return configuration, expected_digest


def _strategy_payload(strategy_id: str, value: object) -> dict[str, object]:
    return {
        "strategy_id": strategy_id,
        "strategy_version": str(getattr(value, "strategy_version", "legacy")),
        "state": str(getattr(value, "state", "unmanaged")),
        "policy_version": str(getattr(value, "policy_version", "legacy")),
        "effective_weight": float(getattr(value, "effective_weight", 0.0)),
        "policy": _json_mapping(getattr(value, "policy", {})),
    }


def _json_mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
