from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import math

import pandas as pd
from pydantic import BaseModel, Field
from sqlalchemy import case, delete, func
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, sessionmaker

from qagent.storage.tables import MarketBarCacheRow, MarketDataCacheSpanRow
from qagent.market.calendars import trading_sessions_in_range


BAR_COLUMNS = [
    "instrument_id",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "turnover",
    "provider",
    "adjusted_open",
    "adjusted_high",
    "adjusted_low",
    "adjusted_close",
    "adjustment_factor",
    "adjustment_type",
]

# SQLite builds may keep the historical 999-variable limit. Leave headroom for
# dialect-generated parameters so bulk upserts behave consistently everywhere.
SQLITE_SAFE_VARIABLE_LIMIT = 900


class MarketDataCacheSummary(BaseModel):
    provider_mode: str
    instrument_id: str
    rows: int
    first_trade_date: date | None
    last_trade_date: date | None
    last_cached_at: datetime | None
    source_providers: list[str] = Field(default_factory=list)
    adjusted_rows: int = 0
    adjustment_types: list[str] = Field(default_factory=list)


class MarketDataCacheRepository:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    def save_daily_bars(self, provider_mode: str, bars: pd.DataFrame) -> int:
        if bars.empty:
            return 0
        normalized = _normalize_bars(bars)
        cached_at = datetime.now(timezone.utc)
        records = []
        for _, row in normalized.iterrows():
            records.append(
                {
                    "provider_mode": provider_mode,
                    "instrument_id": row["instrument_id"],
                    "trade_date": row["trade_date"],
                    "source_provider": str(row.get("provider") or provider_mode),
                    "open": Decimal(str(row["open"])),
                    "high": Decimal(str(row["high"])),
                    "low": Decimal(str(row["low"])),
                    "close": Decimal(str(row["close"])),
                    "volume": Decimal(str(row["volume"])),
                    "turnover": _decimal_or_none(row.get("turnover")),
                    "adjusted_open": _decimal_or_none(row.get("adjusted_open")),
                    "adjusted_high": _decimal_or_none(row.get("adjusted_high")),
                    "adjusted_low": _decimal_or_none(row.get("adjusted_low")),
                    "adjusted_close": _decimal_or_none(row.get("adjusted_close")),
                    "adjustment_factor": _decimal_or_none(row.get("adjustment_factor")),
                    "adjustment_type": _text_or_none(row.get("adjustment_type")),
                    "cached_at": cached_at,
                    "updated_at": cached_at,
                }
            )
        if not records:
            return 0
        with self.session_factory() as session:
            parameters_per_record = len(records[0])
            chunk_size = max(1, SQLITE_SAFE_VARIABLE_LIMIT // parameters_per_record)
            for offset in range(0, len(records), chunk_size):
                statement = sqlite_insert(MarketBarCacheRow).values(
                    records[offset : offset + chunk_size]
                )
                excluded = statement.excluded
                statement = statement.on_conflict_do_update(
                    index_elements=[
                        MarketBarCacheRow.provider_mode,
                        MarketBarCacheRow.instrument_id,
                        MarketBarCacheRow.trade_date,
                    ],
                    set_={
                        "source_provider": excluded.source_provider,
                        "open": excluded.open,
                        "high": excluded.high,
                        "low": excluded.low,
                        "close": excluded.close,
                        "volume": excluded.volume,
                        "turnover": excluded.turnover,
                        "adjusted_open": excluded.adjusted_open,
                        "adjusted_high": excluded.adjusted_high,
                        "adjusted_low": excluded.adjusted_low,
                        "adjusted_close": excluded.adjusted_close,
                        "adjustment_factor": excluded.adjustment_factor,
                        "adjustment_type": excluded.adjustment_type,
                        "cached_at": excluded.cached_at,
                        "updated_at": excluded.updated_at,
                    },
                )
                session.execute(statement)
            session.commit()
        return len(records)

    def merge_missing_daily_bars(
        self,
        provider_mode: str,
        bars: pd.DataFrame,
        *,
        allowed_keys: set[tuple[str, date]],
    ) -> int:
        """Insert exact repair rows while preserving every valid cached field."""

        if bars.empty or not allowed_keys:
            return 0
        required = {"instrument_id", "trade_date", "open", "high", "low", "close"}
        missing_columns = required - set(bars.columns)
        if missing_columns:
            raise ValueError(
                "exact repair rows are missing required fields: "
                + ", ".join(sorted(missing_columns))
            )
        relevant = bars.copy()
        relevant["trade_date"] = pd.to_datetime(relevant["trade_date"], errors="coerce").dt.date
        relevant = relevant.loc[
            relevant.apply(
                lambda row: (str(row["instrument_id"]), row["trade_date"]) in allowed_keys,
                axis=1,
            )
        ].copy()
        if relevant.empty:
            return 0
        duplicate_keys = relevant.duplicated(["instrument_id", "trade_date"], keep=False)
        if duplicate_keys.any():
            raise ValueError("exact repair provider returned duplicate instrument/date rows")
        normalized = _normalize_bars(relevant)
        if len(normalized) != len(relevant):
            raise ValueError("exact repair provider returned invalid OHLC fields")
        for column in ["open", "high", "low", "close"]:
            values = pd.to_numeric(normalized[column], errors="coerce")
            if values.isna().any() or (values <= 0).any():
                raise ValueError(f"exact repair provider returned invalid {column}")
        volume = pd.to_numeric(normalized["volume"], errors="coerce")
        if volume.isna().any() or (volume < 0).any():
            raise ValueError("exact repair provider returned invalid volume")
        adjustment_factors = pd.to_numeric(normalized["adjustment_factor"], errors="coerce")
        invalid_factors = normalized["adjustment_factor"].notna() & (
            adjustment_factors.isna() | (adjustment_factors <= 0)
        )
        if invalid_factors.any():
            raise ValueError("exact repair provider returned invalid adjustment_factor")

        with self.session_factory() as session:
            existing_by_key = {
                (row.instrument_id, row.trade_date): row
                for row in session.query(MarketBarCacheRow)
                .filter(
                    MarketBarCacheRow.provider_mode == provider_mode,
                    MarketBarCacheRow.instrument_id.in_(normalized["instrument_id"].tolist()),
                    MarketBarCacheRow.trade_date.in_(normalized["trade_date"].tolist()),
                )
                .all()
            }
        for _, row in normalized.iterrows():
            existing = existing_by_key.get((row["instrument_id"], row["trade_date"]))
            if existing is None:
                continue
            merged_adjusted = {
                column: _preserved_positive_value(
                    getattr(existing, column),
                    row.get(column),
                )
                for column in [
                    "adjusted_open",
                    "adjusted_high",
                    "adjusted_low",
                    "adjusted_close",
                ]
            }
            if not _valid_partial_adjusted_ohlc(merged_adjusted):
                raise ValueError("exact repair would create invalid adjusted OHLC fields")

        cached_at = datetime.now(timezone.utc)
        records = []
        for _, row in normalized.iterrows():
            records.append(
                {
                    "provider_mode": provider_mode,
                    "instrument_id": row["instrument_id"],
                    "trade_date": row["trade_date"],
                    "source_provider": str(row.get("provider") or provider_mode),
                    "open": Decimal(str(row["open"])),
                    "high": Decimal(str(row["high"])),
                    "low": Decimal(str(row["low"])),
                    "close": Decimal(str(row["close"])),
                    "volume": Decimal(str(row["volume"])),
                    "turnover": _decimal_or_none(row.get("turnover")),
                    "adjusted_open": _decimal_or_none(row.get("adjusted_open")),
                    "adjusted_high": _decimal_or_none(row.get("adjusted_high")),
                    "adjusted_low": _decimal_or_none(row.get("adjusted_low")),
                    "adjusted_close": _decimal_or_none(row.get("adjusted_close")),
                    "adjustment_factor": _decimal_or_none(row.get("adjustment_factor")),
                    "adjustment_type": _text_or_none(row.get("adjustment_type")),
                    "cached_at": cached_at,
                    "updated_at": cached_at,
                }
            )
        with self.session_factory() as session:
            statement = sqlite_insert(MarketBarCacheRow).values(records)
            excluded = statement.excluded
            statement = statement.on_conflict_do_update(
                index_elements=[
                    MarketBarCacheRow.provider_mode,
                    MarketBarCacheRow.instrument_id,
                    MarketBarCacheRow.trade_date,
                ],
                set_={
                    "turnover": func.coalesce(MarketBarCacheRow.turnover, excluded.turnover),
                    "adjusted_open": _keep_valid_positive(
                        MarketBarCacheRow.adjusted_open, excluded.adjusted_open
                    ),
                    "adjusted_high": _keep_valid_positive(
                        MarketBarCacheRow.adjusted_high, excluded.adjusted_high
                    ),
                    "adjusted_low": _keep_valid_positive(
                        MarketBarCacheRow.adjusted_low, excluded.adjusted_low
                    ),
                    "adjusted_close": _keep_valid_positive(
                        MarketBarCacheRow.adjusted_close, excluded.adjusted_close
                    ),
                    "adjustment_factor": _keep_valid_positive(
                        MarketBarCacheRow.adjustment_factor,
                        excluded.adjustment_factor,
                    ),
                    "adjustment_type": func.coalesce(
                        MarketBarCacheRow.adjustment_type,
                        excluded.adjustment_type,
                    ),
                    "updated_at": excluded.updated_at,
                },
            )
            session.execute(statement)
            session.commit()
        return len(records)

    def record_coverage(
        self,
        provider_mode: str,
        instrument_id: str,
        start: date,
        end: date,
        row_count: int,
    ) -> None:
        cached_at = datetime.now(timezone.utc)
        with self.session_factory() as session:
            statement = sqlite_insert(MarketDataCacheSpanRow).values(
                provider_mode=provider_mode,
                instrument_id=instrument_id,
                start_date=start,
                end_date=end,
                row_count=row_count,
                cached_at=cached_at,
                updated_at=cached_at,
            )
            statement = statement.on_conflict_do_update(
                index_elements=[
                    MarketDataCacheSpanRow.provider_mode,
                    MarketDataCacheSpanRow.instrument_id,
                    MarketDataCacheSpanRow.start_date,
                    MarketDataCacheSpanRow.end_date,
                ],
                set_={
                    "row_count": row_count,
                    "cached_at": cached_at,
                    "updated_at": cached_at,
                },
            )
            session.execute(statement)
            session.commit()

    def has_coverage(self, provider_mode: str, instrument_id: str, start: date, end: date) -> bool:
        with self.session_factory() as session:
            span = (
                session.query(MarketDataCacheSpanRow)
                .filter(
                    MarketDataCacheSpanRow.provider_mode == provider_mode,
                    MarketDataCacheSpanRow.instrument_id == instrument_id,
                    MarketDataCacheSpanRow.start_date <= start,
                    MarketDataCacheSpanRow.end_date >= end,
                )
                .order_by(MarketDataCacheSpanRow.cached_at.desc())
                .first()
            )
            return span is not None

    def has_usable_coverage(
        self,
        provider_mode: str,
        instrument_id: str,
        start: date,
        end: date,
        *,
        require_adjusted: bool = False,
        minimum_session_coverage: float | None = None,
        maximum_trailing_session_gap: int | None = None,
    ) -> bool:
        with self.session_factory() as session:
            span = (
                session.query(MarketDataCacheSpanRow)
                .filter(
                    MarketDataCacheSpanRow.provider_mode == provider_mode,
                    MarketDataCacheSpanRow.instrument_id == instrument_id,
                    MarketDataCacheSpanRow.start_date <= start,
                    MarketDataCacheSpanRow.end_date >= end,
                    MarketDataCacheSpanRow.row_count > 0,
                )
                .order_by(MarketDataCacheSpanRow.cached_at.desc())
                .first()
            )
            if span is None:
                return False
            total_rows, latest_trade_date = (
                session.query(
                    func.count(MarketBarCacheRow.trade_date),
                    func.max(MarketBarCacheRow.trade_date),
                )
                .filter(
                    MarketBarCacheRow.provider_mode == provider_mode,
                    MarketBarCacheRow.instrument_id == instrument_id,
                    MarketBarCacheRow.trade_date >= start,
                    MarketBarCacheRow.trade_date <= end,
                    *_valid_cached_ohlc_filters(),
                )
                .one()
            )
            if total_rows <= 0:
                return False
            if (
                maximum_trailing_session_gap is not None
                and provider_mode == "free"
                and instrument_id.startswith("CN:")
                and latest_trade_date is not None
            ):
                effective_end = min(end, date.today())
                try:
                    missing_tail = len(
                        trading_sessions_in_range(
                            latest_trade_date + timedelta(days=1),
                            effective_end,
                        )
                    )
                except ValueError:
                    missing_tail = 0
                if missing_tail > maximum_trailing_session_gap:
                    return False
            expected_rows = total_rows
            if (
                minimum_session_coverage is not None
                and provider_mode == "free"
                and instrument_id.startswith("CN:")
            ):
                try:
                    expected_rows = len(trading_sessions_in_range(start, end))
                except ValueError:
                    # Snapshot lookups intentionally use an open-ended historical range.
                    expected_rows = total_rows
                if expected_rows > 0 and total_rows / expected_rows < minimum_session_coverage:
                    return False
            if not require_adjusted:
                return True
            adjusted_rows = (
                session.query(MarketBarCacheRow)
                .filter(
                    MarketBarCacheRow.provider_mode == provider_mode,
                    MarketBarCacheRow.instrument_id == instrument_id,
                    MarketBarCacheRow.trade_date >= start,
                    MarketBarCacheRow.trade_date <= end,
                    MarketBarCacheRow.adjusted_close.is_not(None),
                    MarketBarCacheRow.adjusted_close > 0,
                    MarketBarCacheRow.adjustment_factor.is_not(None),
                    MarketBarCacheRow.adjustment_factor > 0,
                    MarketBarCacheRow.adjustment_type.is_not(None),
                    *_valid_cached_ohlc_filters(),
                )
                .count()
            )
            denominator = expected_rows if expected_rows > 0 else total_rows
            return adjusted_rows / denominator >= 0.95

    def load_daily_bars(
        self,
        provider_mode: str,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> pd.DataFrame:
        with self.session_factory() as session:
            rows = (
                session.query(MarketBarCacheRow)
                .filter(
                    MarketBarCacheRow.provider_mode == provider_mode,
                    MarketBarCacheRow.instrument_id.in_(instrument_ids),
                    MarketBarCacheRow.trade_date >= start,
                    MarketBarCacheRow.trade_date <= end,
                )
                .order_by(MarketBarCacheRow.instrument_id, MarketBarCacheRow.trade_date)
                .all()
            )
        if not rows:
            return pd.DataFrame(columns=BAR_COLUMNS)
        frame = pd.DataFrame(
            [
                {
                    "instrument_id": row.instrument_id,
                    "trade_date": row.trade_date,
                    "open": row.open,
                    "high": row.high,
                    "low": row.low,
                    "close": row.close,
                    "volume": row.volume,
                    "turnover": row.turnover,
                    "provider": row.source_provider,
                    "adjusted_open": row.adjusted_open,
                    "adjusted_high": row.adjusted_high,
                    "adjusted_low": row.adjusted_low,
                    "adjusted_close": row.adjusted_close,
                    "adjustment_factor": row.adjustment_factor,
                    "adjustment_type": row.adjustment_type,
                }
                for row in rows
            ],
            columns=BAR_COLUMNS,
        )
        return _normalize_bars(frame)

    def load_latest_daily_bars(
        self,
        provider_mode: str,
        instrument_ids: list[str],
    ) -> pd.DataFrame:
        if not instrument_ids:
            return pd.DataFrame(columns=BAR_COLUMNS)
        unique_ids = sorted(set(instrument_ids))
        with self.session_factory() as session:
            # The composite primary key starts with provider and instrument, so
            # one bounded index seek per requested instrument is substantially
            # cheaper on the multi-gigabyte local cache than materializing a
            # grouped MAX subquery across the full table.
            rows = [
                row
                for instrument_id in unique_ids
                if (
                    row := session.query(MarketBarCacheRow)
                    .filter(
                        MarketBarCacheRow.provider_mode == provider_mode,
                        MarketBarCacheRow.instrument_id == instrument_id,
                        *_valid_cached_ohlc_filters(),
                    )
                    .order_by(MarketBarCacheRow.trade_date.desc())
                    .first()
                )
                is not None
            ]
        if not rows:
            return pd.DataFrame(columns=BAR_COLUMNS)

        frame = pd.DataFrame(
            [
                {
                    "instrument_id": row.instrument_id,
                    "trade_date": row.trade_date,
                    "open": row.open,
                    "high": row.high,
                    "low": row.low,
                    "close": row.close,
                    "volume": row.volume,
                    "turnover": row.turnover,
                    "provider": row.source_provider,
                    "adjusted_open": row.adjusted_open,
                    "adjusted_high": row.adjusted_high,
                    "adjusted_low": row.adjusted_low,
                    "adjusted_close": row.adjusted_close,
                    "adjustment_factor": row.adjustment_factor,
                    "adjustment_type": row.adjustment_type,
                }
                for row in rows
            ],
            columns=BAR_COLUMNS,
        )
        return _normalize_bars(frame)

    def latest_trade_dates(
        self,
        provider_mode: str,
        instrument_ids: list[str],
        *,
        not_after: date | None = None,
    ) -> dict[str, date]:
        """Return the latest valid cached session without materializing price history."""

        result: dict[str, date] = {}
        unique_ids = sorted(set(instrument_ids))
        with self.session_factory() as session:
            for offset in range(0, len(unique_ids), SQLITE_SAFE_VARIABLE_LIMIT):
                chunk = unique_ids[offset : offset + SQLITE_SAFE_VARIABLE_LIMIT]
                query = session.query(
                    MarketBarCacheRow.instrument_id,
                    func.max(MarketBarCacheRow.trade_date),
                ).filter(
                    MarketBarCacheRow.provider_mode == provider_mode,
                    MarketBarCacheRow.instrument_id.in_(chunk),
                    *_valid_cached_ohlc_filters(),
                )
                if not_after is not None:
                    query = query.filter(MarketBarCacheRow.trade_date <= not_after)
                for instrument_id, latest_trade_date in query.group_by(
                    MarketBarCacheRow.instrument_id
                ):
                    if latest_trade_date is not None:
                        result[str(instrument_id)] = latest_trade_date
        return result

    def count_daily_bars_by_instrument(
        self,
        provider_mode: str,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> dict[str, int]:
        """Count valid cached rows for coverage bookkeeping in bounded batches."""

        result = {instrument_id: 0 for instrument_id in set(instrument_ids)}
        unique_ids = sorted(result)
        with self.session_factory() as session:
            for offset in range(0, len(unique_ids), SQLITE_SAFE_VARIABLE_LIMIT):
                chunk = unique_ids[offset : offset + SQLITE_SAFE_VARIABLE_LIMIT]
                rows = (
                    session.query(
                        MarketBarCacheRow.instrument_id,
                        func.count(MarketBarCacheRow.trade_date),
                    )
                    .filter(
                        MarketBarCacheRow.provider_mode == provider_mode,
                        MarketBarCacheRow.instrument_id.in_(chunk),
                        MarketBarCacheRow.trade_date >= start,
                        MarketBarCacheRow.trade_date <= end,
                        *_valid_cached_ohlc_filters(),
                    )
                    .group_by(MarketBarCacheRow.instrument_id)
                    .all()
                )
                for instrument_id, row_count in rows:
                    result[str(instrument_id)] = int(row_count)
        return result

    def list_summaries(
        self,
        provider_mode: str | None = None,
        instrument_id: str | None = None,
    ) -> list[MarketDataCacheSummary]:
        with self.session_factory() as session:
            query = session.query(MarketBarCacheRow)
            query = query.filter(*_valid_cached_ohlc_filters())
            if provider_mode:
                query = query.filter(MarketBarCacheRow.provider_mode == provider_mode)
            if instrument_id:
                query = query.filter(MarketBarCacheRow.instrument_id == instrument_id)
            rows = query.order_by(
                MarketBarCacheRow.provider_mode, MarketBarCacheRow.instrument_id
            ).all()
        grouped: dict[tuple[str, str], list[MarketBarCacheRow]] = {}
        for row in rows:
            grouped.setdefault((row.provider_mode, row.instrument_id), []).append(row)
        summaries: list[MarketDataCacheSummary] = []
        for (mode, symbol), items in grouped.items():
            summaries.append(
                MarketDataCacheSummary(
                    provider_mode=mode,
                    instrument_id=symbol,
                    rows=len(items),
                    first_trade_date=min(item.trade_date for item in items),
                    last_trade_date=max(item.trade_date for item in items),
                    last_cached_at=max(item.cached_at for item in items),
                    source_providers=sorted(
                        {item.source_provider for item in items if item.source_provider}
                    ),
                    adjusted_rows=sum(1 for item in items if item.adjusted_close is not None),
                    adjustment_types=sorted(
                        {item.adjustment_type for item in items if item.adjustment_type}
                    ),
                )
            )
        return summaries

    def delete(
        self,
        provider_mode: str | None = None,
        instrument_id: str | None = None,
    ) -> int:
        with self.session_factory() as session:
            rows_query = delete(MarketBarCacheRow)
            spans_query = delete(MarketDataCacheSpanRow)
            if provider_mode:
                rows_query = rows_query.where(MarketBarCacheRow.provider_mode == provider_mode)
                spans_query = spans_query.where(
                    MarketDataCacheSpanRow.provider_mode == provider_mode
                )
            if instrument_id:
                rows_query = rows_query.where(MarketBarCacheRow.instrument_id == instrument_id)
                spans_query = spans_query.where(
                    MarketDataCacheSpanRow.instrument_id == instrument_id
                )
            deleted_rows = session.execute(rows_query).rowcount or 0
            session.execute(spans_query)
            session.commit()
            return deleted_rows


def _normalize_bars(bars: pd.DataFrame) -> pd.DataFrame:
    if bars.empty:
        return pd.DataFrame(columns=BAR_COLUMNS)
    normalized = bars.copy()
    normalized["trade_date"] = pd.to_datetime(normalized["trade_date"]).dt.date
    for column in ["open", "high", "low", "close"]:
        normalized[column] = _finite_numeric(normalized[column])
    for column in [
        "turnover",
        "adjusted_open",
        "adjusted_high",
        "adjusted_low",
        "adjusted_close",
        "adjustment_factor",
    ]:
        if column not in normalized.columns:
            normalized[column] = None
        normalized[column] = _finite_numeric(normalized[column])
    if "volume" not in normalized.columns:
        normalized["volume"] = 0
    volume = _finite_numeric(normalized["volume"]).fillna(0)
    if not volume.isna().any() and volume.mod(1).eq(0).all():
        normalized["volume"] = volume.astype("int64")
    else:
        normalized["volume"] = volume
    if "provider" not in normalized.columns:
        normalized["provider"] = ""
    if "adjustment_type" not in normalized.columns:
        normalized["adjustment_type"] = None
    normalized = normalized.dropna(subset=["open", "high", "low", "close"])
    normalized = _clear_invalid_adjusted_ohlc(normalized)
    normalized = normalized[_valid_ohlc_mask(normalized)]
    return (
        normalized[BAR_COLUMNS].sort_values(["instrument_id", "trade_date"]).reset_index(drop=True)
    )


def _keep_valid_positive(current, replacement):
    return case(
        (current.is_not(None) & (current > 0), current),
        else_=replacement,
    )


def _preserved_positive_value(current: object, replacement: object) -> float | None:
    for value in (current, replacement):
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(numeric) and numeric > 0:
            return numeric
    return None


def _valid_partial_adjusted_ohlc(values: dict[str, float | None]) -> bool:
    present = [value for value in values.values() if value is not None]
    if not present:
        return True
    if values["adjusted_close"] is None:
        return False
    if len(present) < 4:
        return True
    return bool(
        values["adjusted_high"] >= values["adjusted_open"]
        and values["adjusted_high"] >= values["adjusted_close"]
        and values["adjusted_high"] >= values["adjusted_low"]
        and values["adjusted_low"] <= values["adjusted_open"]
        and values["adjusted_low"] <= values["adjusted_close"]
    )


def _valid_ohlc_mask(frame: pd.DataFrame) -> pd.Series:
    return (
        frame["open"].gt(0)
        & frame["high"].gt(0)
        & frame["low"].gt(0)
        & frame["close"].gt(0)
        & frame["high"].ge(frame["open"])
        & frame["high"].ge(frame["close"])
        & frame["high"].ge(frame["low"])
        & frame["low"].le(frame["open"])
        & frame["low"].le(frame["close"])
    )


def _clear_invalid_adjusted_ohlc(frame: pd.DataFrame) -> pd.DataFrame:
    adjusted_columns = [
        "adjusted_open",
        "adjusted_high",
        "adjusted_low",
        "adjusted_close",
    ]
    adjusted = frame[adjusted_columns].rename(
        columns={column: column.removeprefix("adjusted_") for column in adjusted_columns}
    )
    has_adjusted_value = adjusted.notna().any(axis=1)
    complete = adjusted.notna().all(axis=1)
    valid = adjusted["close"].gt(0) & (~complete | _valid_ohlc_mask(adjusted))
    invalid = has_adjusted_value & ~valid
    if not invalid.any():
        return frame
    normalized = frame.copy()
    normalized.loc[invalid, adjusted_columns] = None
    normalized.loc[invalid, "adjustment_factor"] = None
    normalized.loc[invalid, "adjustment_type"] = None
    return normalized


def _valid_cached_ohlc_filters() -> tuple[object, ...]:
    return (
        MarketBarCacheRow.open > 0,
        MarketBarCacheRow.high > 0,
        MarketBarCacheRow.low > 0,
        MarketBarCacheRow.close > 0,
        MarketBarCacheRow.high >= MarketBarCacheRow.open,
        MarketBarCacheRow.high >= MarketBarCacheRow.close,
        MarketBarCacheRow.high >= MarketBarCacheRow.low,
        MarketBarCacheRow.low <= MarketBarCacheRow.open,
        MarketBarCacheRow.low <= MarketBarCacheRow.close,
    )


def _finite_numeric(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    finite_mask = numeric.map(lambda value: pd.notna(value) and math.isfinite(float(value)))
    return numeric.where(finite_mask)


def _decimal_or_none(value: object) -> Decimal | None:
    try:
        if value is None or pd.isna(value):
            return None
    except (TypeError, ValueError):
        return None
    return Decimal(str(value))


def _text_or_none(value: object) -> str | None:
    try:
        if value is None or pd.isna(value):
            return None
    except (TypeError, ValueError):
        return None
    text = str(value).strip()
    return text or None
