from datetime import date, datetime, timezone
from decimal import Decimal
import math

import pandas as pd
from pydantic import BaseModel, Field
from sqlalchemy import delete
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, sessionmaker

from qagent.storage.tables import MarketBarCacheRow, MarketDataCacheSpanRow


BAR_COLUMNS = ["instrument_id", "trade_date", "open", "high", "low", "close", "volume", "provider"]


class MarketDataCacheSummary(BaseModel):
    provider_mode: str
    instrument_id: str
    rows: int
    first_trade_date: date | None
    last_trade_date: date | None
    last_cached_at: datetime | None
    source_providers: list[str] = Field(default_factory=list)


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
                    "cached_at": cached_at,
                    "updated_at": cached_at,
                }
            )
        if not records:
            return 0
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
                    "source_provider": excluded.source_provider,
                    "open": excluded.open,
                    "high": excluded.high,
                    "low": excluded.low,
                    "close": excluded.close,
                    "volume": excluded.volume,
                    "cached_at": excluded.cached_at,
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
                    "provider": row.source_provider,
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
            rows = (
                session.query(MarketBarCacheRow)
                .filter(
                    MarketBarCacheRow.provider_mode == provider_mode,
                    MarketBarCacheRow.instrument_id.in_(unique_ids),
                )
                .order_by(
                    MarketBarCacheRow.instrument_id,
                    MarketBarCacheRow.trade_date.desc(),
                )
                .all()
            )
        if not rows:
            return pd.DataFrame(columns=BAR_COLUMNS)

        latest_by_instrument: dict[str, MarketBarCacheRow] = {}
        for row in rows:
            latest_by_instrument.setdefault(row.instrument_id, row)

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
                    "provider": row.source_provider,
                }
                for row in latest_by_instrument.values()
            ],
            columns=BAR_COLUMNS,
        )
        return _normalize_bars(frame)

    def list_summaries(
        self,
        provider_mode: str | None = None,
        instrument_id: str | None = None,
    ) -> list[MarketDataCacheSummary]:
        with self.session_factory() as session:
            query = session.query(MarketBarCacheRow)
            if provider_mode:
                query = query.filter(MarketBarCacheRow.provider_mode == provider_mode)
            if instrument_id:
                query = query.filter(MarketBarCacheRow.instrument_id == instrument_id)
            rows = query.order_by(MarketBarCacheRow.provider_mode, MarketBarCacheRow.instrument_id).all()
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
                spans_query = spans_query.where(MarketDataCacheSpanRow.provider_mode == provider_mode)
            if instrument_id:
                rows_query = rows_query.where(MarketBarCacheRow.instrument_id == instrument_id)
                spans_query = spans_query.where(MarketDataCacheSpanRow.instrument_id == instrument_id)
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
    if "volume" not in normalized.columns:
        normalized["volume"] = 0
    volume = _finite_numeric(normalized["volume"]).fillna(0)
    if not volume.isna().any() and volume.mod(1).eq(0).all():
        normalized["volume"] = volume.astype("int64")
    else:
        normalized["volume"] = volume
    if "provider" not in normalized.columns:
        normalized["provider"] = ""
    normalized = normalized.dropna(subset=["open", "high", "low", "close"])
    return normalized[BAR_COLUMNS].sort_values(["instrument_id", "trade_date"]).reset_index(drop=True)


def _finite_numeric(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    finite_mask = numeric.map(lambda value: pd.notna(value) and math.isfinite(float(value)))
    return numeric.where(finite_mask)
