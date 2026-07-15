from datetime import date, datetime, timezone

from qagent.cards.generator import OpportunityCardGenerator
from qagent.domain.enums import Direction, OpportunityStatus, SignalType
from qagent.domain.models import Signal
from qagent.market.indicators import wilder_atr
from qagent.providers.fixtures import FixtureMarketDataProvider
from qagent.signals.engine import SignalEngine
from qagent.strategy_data.providers import FixtureStrategyDataProvider
from qagent.strategies.evaluator import StrategyEvaluator
from qagent.strategies.registry import default_strategy_registry


def test_card_generator_creates_setup_ready_card():
    provider = FixtureMarketDataProvider()
    bars = provider.get_daily_bars(["US:TEST"], date(2026, 1, 1), date(2026, 3, 31))
    signals = SignalEngine().generate("US:TEST", bars)
    card = OpportunityCardGenerator().generate("US:TEST", signals, bars)
    assert card is not None
    assert card.entry_plan.confirmation
    assert card.exit_plan.invalidation
    assert card.data_caveats == ["fixture data"]
    assert card.signals
    assert card.signals[0].signal_type
    assert card.signals[0].evidence
    assert card.signal_consensus is not None
    assert card.signal_consensus.dominant_direction == Direction.BULLISH
    assert card.signal_consensus.bearish_pressure == 0
    assert card.strategy_evaluations
    assert card.primary_strategy_id in {
        "breakout_volume_confirmation",
        "trend_momentum_stage2",
        "gf_dma_health",
    }
    assert card.strategy_score >= card.score
    assert any(strategy.status == "missing_data" for strategy in card.strategy_evaluations)
    assert card.decision.action in {"candidate_entry", "watch_trigger", "wait_pullback"}
    assert card.decision.conviction_score >= 0.5
    assert card.decision.suggested_risk_pct > 0
    assert card.decision.trigger_price == card.entry_plan.trigger_price
    assert card.decision.initial_stop == card.exit_plan.initial_stop
    assert card.decision.target_1 == card.exit_plan.target_1
    assert card.decision.failure_conditions
    assert card.decision.verification_checks
    assert "guarantee" not in card.decision.model_dump_json().lower()
    expected_atr = float(wilder_atr(bars.sort_values("trade_date"), period=14).iloc[-1])
    actual_risk = float(card.entry_plan.trigger_price - card.exit_plan.initial_stop)
    assert abs(actual_risk - expected_atr) <= 0.02


def test_card_generator_reports_market_data_provider():
    provider = FixtureMarketDataProvider()
    bars = provider.get_daily_bars(["US:TEST"], date(2026, 1, 1), date(2026, 3, 31))
    bars["provider"] = "yfinance"
    signals = SignalEngine().generate("US:TEST", bars)

    card = OpportunityCardGenerator().generate("US:TEST", signals, bars)

    assert card is not None
    assert card.data_caveats == ["provider: yfinance"]


def test_card_generator_uses_pead_trade_plan_when_pead_is_primary():
    provider = FixtureMarketDataProvider()
    bars = provider.get_daily_bars(["US:TEST"], date(2026, 1, 1), date(2026, 3, 31))
    signals = SignalEngine().generate("US:TEST", bars)
    earnings_events = FixtureStrategyDataProvider().get_earnings_events(
        ["US:TEST"], start=date(2026, 3, 1), end=date(2026, 3, 31)
    )
    evaluations = StrategyEvaluator(default_strategy_registry()).evaluate(
        "US:TEST",
        signals,
        bars,
        context={
            "earnings_events": earnings_events,
            "available_data": [
                "earnings_actuals",
                "earnings_estimates",
                "announcement_timestamp",
            ],
        },
    )
    card = OpportunityCardGenerator().generate("US:TEST", signals, bars, evaluations)

    assert card is not None
    assert card.primary_strategy_id == "pead_earnings_drift"
    assert card.entry_plan.entry_type == "pead"
    assert "earnings-day low" in card.exit_plan.invalidation
    assert card.rank_score >= card.strategy_score
    assert any("PEAD" in reason for reason in card.rank_reasons)
    assert card.decision.action in {"candidate_entry", "watch_trigger"}
    assert card.decision.horizon == "swing"


def test_card_generator_does_not_treat_bearish_limit_signal_as_long_support():
    provider = FixtureMarketDataProvider()
    bars = provider.get_daily_bars(["CN:000001"], date(2026, 1, 1), date(2026, 3, 31))
    bearish = Signal(
        instrument_id="CN:000001",
        signal_type=SignalType.LIMIT_STATUS,
        direction=Direction.BEARISH,
        observed_at=datetime(2026, 3, 31, tzinfo=timezone.utc),
        horizon="1d",
        score=0.9,
    )

    card = OpportunityCardGenerator().generate("CN:000001", [bearish], bars)

    assert card is not None
    assert card.score == 0
    assert card.strategy_score == 0
    assert card.signal_consensus is not None
    assert card.signal_consensus.bearish_pressure == 0.9
    assert card.signal_consensus.dominant_direction == Direction.BEARISH


def test_card_generator_downgrades_nearly_balanced_conflicting_signals():
    provider = FixtureMarketDataProvider()
    bars = provider.get_daily_bars(["CN:000001"], date(2026, 1, 1), date(2026, 3, 31))
    observed_at = datetime(2026, 3, 31, tzinfo=timezone.utc)
    signals = [
        Signal(
            instrument_id="CN:000001",
            signal_type=SignalType.TREND_STRENGTH,
            direction=Direction.BULLISH,
            observed_at=observed_at,
            horizon="20d",
            score=0.61,
            evidence={"close": 10, "ma_20": 9.8, "ma_50": 9.5},
        ),
        Signal(
            instrument_id="CN:000001",
            signal_type=SignalType.LIMIT_STATUS,
            direction=Direction.BEARISH,
            observed_at=observed_at,
            horizon="1d",
            score=1.0,
        ),
    ]

    card = OpportunityCardGenerator().generate("CN:000001", signals, bars)

    assert card is not None
    assert card.signal_consensus is not None
    assert card.signal_consensus.conflict > 0.95
    assert card.strategy_score < 0.5
    assert card.status == OpportunityStatus.WATCH


def test_card_generator_discloses_atr_fallback_after_recent_missing_bar():
    provider = FixtureMarketDataProvider()
    bars = provider.get_daily_bars(["CN:000001"], date(2026, 1, 1), date(2026, 3, 31))
    bars.loc[bars.index[-1], "high"] = None
    signal = Signal(
        instrument_id="CN:000001",
        signal_type=SignalType.TREND_STRENGTH,
        direction=Direction.BULLISH,
        observed_at=datetime(2026, 3, 31, tzinfo=timezone.utc),
        horizon="20d",
        score=0.7,
        evidence={"close": 10, "ma_20": 9.8, "ma_50": 9.5},
    )

    card = OpportunityCardGenerator().generate("CN:000001", [signal], bars)

    assert card is not None
    assert "atr: fallback_4pct_insufficient_history" in card.data_caveats


def test_card_generator_uses_adjusted_volatility_without_changing_trade_price_basis():
    provider = FixtureMarketDataProvider()
    adjusted = provider.get_daily_bars(["CN:000001"], date(2026, 1, 1), date(2026, 3, 31))
    raw = adjusted.copy()
    split_index = raw.index[-5]
    raw.loc[raw.index < split_index, ["open", "high", "low", "close"]] *= 2
    signal = Signal(
        instrument_id="CN:000001",
        signal_type=SignalType.TREND_STRENGTH,
        direction=Direction.BULLISH,
        observed_at=datetime(2026, 3, 31, tzinfo=timezone.utc),
        horizon="20d",
        score=0.7,
        evidence={"close": 10, "ma_20": 9.8, "ma_50": 9.5},
    )

    adjusted_card = OpportunityCardGenerator().generate(
        "CN:000001",
        [signal],
        raw,
        volatility_bars=adjusted,
    )
    raw_card = OpportunityCardGenerator().generate("CN:000001", [signal], raw)

    assert adjusted_card is not None and raw_card is not None
    assert adjusted_card.entry_plan.trigger_price == raw_card.entry_plan.trigger_price
    adjusted_risk = adjusted_card.entry_plan.trigger_price - adjusted_card.exit_plan.initial_stop
    raw_risk = raw_card.entry_plan.trigger_price - raw_card.exit_plan.initial_stop
    assert adjusted_risk < raw_risk
