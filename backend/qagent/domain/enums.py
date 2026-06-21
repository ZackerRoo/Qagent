from enum import StrEnum


class Market(StrEnum):
    US = "US"
    CN = "CN"


class OpportunityStatus(StrEnum):
    NEW_IDEA = "new_idea"
    WATCH = "watch"
    SETUP_READY = "setup_ready"
    TRIGGERED = "triggered"
    EXTENDED = "extended"
    ACTIVE = "active"
    RISK_ELEVATED = "risk_elevated"
    INVALIDATED = "invalidated"
    CLOSED = "closed"
    POSTMORTEM_DONE = "postmortem_done"


class SignalType(StrEnum):
    TREND_STRENGTH = "trend_strength"
    PULLBACK = "pullback"
    BREAKOUT = "breakout"
    VOLUME_ANOMALY = "volume_anomaly"
    LIMIT_STATUS = "limit_status"
    EVENT_CATALYST = "event_catalyst"


class Direction(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class AlertStatus(StrEnum):
    PENDING = "pending"
    TRIGGERED = "triggered"
    ACKNOWLEDGED = "acknowledged"
    CLOSED = "closed"
    EXPIRED = "expired"
    INVALIDATED = "invalidated"
