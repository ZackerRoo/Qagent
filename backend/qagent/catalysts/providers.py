from datetime import datetime, timezone
from hashlib import sha1
from typing import Any

import akshare as ak
import pandas as pd
import yfinance as yf

from qagent.catalysts.models import NewsItem


class FreeCatalystProvider:
    def __init__(self):
        self.last_errors: list[str] = []

    def get_news(self, instrument_ids: list[str], limit: int = 5) -> list[NewsItem]:
        self.last_errors = []
        items: list[NewsItem] = []
        for instrument_id in instrument_ids:
            try:
                if instrument_id.startswith("US:"):
                    items.extend(self._get_us_news(instrument_id, limit))
                elif instrument_id.startswith("CN:"):
                    items.extend(self._get_cn_news(instrument_id, limit))
                else:
                    self.last_errors.append(f"{instrument_id}: unsupported market")
            except Exception as exc:
                self.last_errors.append(f"{instrument_id}: {exc}")
        return items

    @staticmethod
    def _get_us_news(instrument_id: str, limit: int) -> list[NewsItem]:
        symbol = instrument_id.split(":", 1)[1]
        search = yf.Search(symbol, max_results=limit, news_count=limit, raise_errors=False)
        raw_news = search.news or []
        return [_normalize_yfinance_news(instrument_id, item) for item in raw_news[:limit]]

    @staticmethod
    def _get_cn_news(instrument_id: str, limit: int) -> list[NewsItem]:
        symbol = instrument_id.split(":", 1)[1]
        raw = ak.stock_news_em(symbol=symbol)
        if raw.empty:
            return []
        return [
            _normalize_akshare_news(instrument_id, row)
            for _, row in raw.head(limit).iterrows()
        ]


def _normalize_yfinance_news(instrument_id: str, item: dict[str, Any]) -> NewsItem:
    content = item.get("content") if isinstance(item.get("content"), dict) else {}
    title = item.get("title") or content.get("title") or ""
    publisher = item.get("publisher") or _nested(content, "provider", "displayName")
    url = item.get("link") or _nested(content, "canonicalUrl", "url")
    published_at = _parse_yfinance_time(item.get("providerPublishTime") or content.get("pubDate"))
    return NewsItem(
        news_id=_news_id(instrument_id, title, url),
        instrument_id=instrument_id,
        title=title,
        publisher=publisher,
        published_at=published_at,
        url=url,
        source="yfinance",
    )


def _normalize_akshare_news(instrument_id: str, row: pd.Series) -> NewsItem:
    title = _first_present(row, ["新闻标题", "标题", "title"])
    url = _first_present(row, ["新闻链接", "链接", "url"])
    published_at = _parse_datetime(_first_present(row, ["发布时间", "时间", "datetime"]))
    return NewsItem(
        news_id=_news_id(instrument_id, title, url),
        instrument_id=instrument_id,
        title=title,
        publisher=_first_present(row, ["文章来源", "来源", "publisher"]),
        published_at=published_at,
        url=url,
        source="akshare",
    )


def _news_id(instrument_id: str, title: str, url: str | None) -> str:
    digest = sha1(f"{instrument_id}|{title}|{url or ''}".encode()).hexdigest()[:12]
    return f"news_{digest}"


def _nested(value: dict[str, Any], *keys: str) -> Any:
    current: Any = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _first_present(row: pd.Series, columns: list[str]) -> str | None:
    for column in columns:
        if column in row and pd.notna(row[column]):
            return str(row[column])
    return None


def _parse_yfinance_time(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, timezone.utc)
    return _parse_datetime(str(value))


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed
