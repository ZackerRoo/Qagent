from datetime import datetime, timezone

import pytest

from qagent.cards.scoring import aggregate_score, calculate_signal_consensus
from qagent.domain.enums import Direction, SignalType
from qagent.domain.models import Signal


def _signal(
    direction: Direction,
    score: float = 0.8,
    signal_type: SignalType = SignalType.TREND_STRENGTH,
) -> Signal:
    return Signal(
        instrument_id="CN:000001",
        signal_type=signal_type,
        direction=direction,
        observed_at=datetime(2026, 7, 16, tzinfo=timezone.utc),
        horizon="20d",
        score=score,
    )


def test_empty_signal_stack_has_no_long_opportunity_score():
    consensus = calculate_signal_consensus([])

    assert aggregate_score([]) == 0
    assert consensus.net_score == 0
    assert consensus.dominant_direction == Direction.NEUTRAL
    assert consensus.signal_count == 0


def test_equal_strength_orders_bearish_below_neutral_below_bullish():
    bearish = aggregate_score([_signal(Direction.BEARISH)])
    neutral = aggregate_score([_signal(Direction.NEUTRAL)])
    bullish = aggregate_score([_signal(Direction.BULLISH)])

    assert bearish < neutral < bullish
    assert bearish == 0
    assert neutral == pytest.approx(0.4)
    assert bullish == pytest.approx(0.8)


def test_strong_bearish_signal_cannot_raise_bullish_score():
    bullish = _signal(Direction.BULLISH, 0.8, SignalType.BREAKOUT)
    bearish = _signal(Direction.BEARISH, 0.9, SignalType.LIMIT_STATUS)

    bullish_only = aggregate_score([bullish])
    mixed = calculate_signal_consensus([bullish, bearish])

    assert mixed.net_score < bullish_only
    assert mixed.bearish_pressure > 0
    assert mixed.conflict > 0


def test_consensus_is_order_independent_and_reports_agreement():
    signals = [
        _signal(Direction.BULLISH, 0.7, SignalType.TREND_STRENGTH),
        _signal(Direction.BULLISH, 0.6, SignalType.PULLBACK),
        _signal(Direction.NEUTRAL, 0.4, SignalType.VOLUME_ANOMALY),
    ]

    forward = calculate_signal_consensus(signals)
    reverse = calculate_signal_consensus(list(reversed(signals)))

    assert forward == reverse
    assert forward.dominant_direction == Direction.BULLISH
    assert forward.agreement > 0.7
    assert forward.conflict == 0
    assert forward.signal_count == 3
