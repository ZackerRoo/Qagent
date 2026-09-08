#!/usr/bin/env python3
"""Inspect four missing raw prices in Fuyao's recent 10-day dump, in memory.

Run with the backend environment (and optional pyarrow installed). No database,
cache, scheduler, or provider factory is accessed. Signed URLs never enter output.
The /api prefix and API-key access were verified live on 2026-09-08; published
market-dumps docs currently describe /dump and browser-cookie access instead.
"""
from __future__ import annotations

import io
import json
import argparse
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit

MAX_BYTES = 25 * 1024 * 1024
ENDPOINT = "/api/dump/market-dumps/daily-k-10d/download-url"
TARGETS = (("002731.SZ", "2026-09-02"), ("002731.SZ", "2026-09-07"),
           ("600929.SH", "2026-09-04"), ("688432.SH", "2026-09-07"))
COLUMNS = ["thscode", "date_ms", "adjusted", "interval", "currency",
           "open_price", "high_price", "low_price", "close_price"]


class ProbeError(RuntimeError):
    """Only fixed, credential-free messages may be passed to this exception."""


def read_file(path: Path) -> bytes:
    """Read a bounded local artifact, checking its size before reading bytes."""
    with path.open("rb") as source:
        if os.fstat(source.fileno()).st_size > MAX_BYTES:
            raise ProbeError("file_size_limit")
        blob = source.read(MAX_BYTES + 1)
        if len(blob) > MAX_BYTES:
            raise ProbeError("file_size_limit")
        return blob


def download(session, url: str) -> bytes:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ProbeError("invalid_download_url")
    deadline = time.monotonic() + 45
    # Fresh session: no API key or Fuyao authentication headers/cookies forwarded.
    with session.get(url, stream=True, timeout=(5, 10), allow_redirects=False) as response:
        if response.status_code != 200:
            raise ProbeError("download_http_error")
        length = response.headers.get("Content-Length")
        if length is not None and (not length.isdigit() or int(length) > MAX_BYTES):
            raise ProbeError("download_size_limit")
        output = io.BytesIO()
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if time.monotonic() > deadline:
                raise ProbeError("download_deadline")
            if output.tell() + len(chunk) > MAX_BYTES:
                raise ProbeError("download_size_limit")
            output.write(chunk)
        return output.getvalue()


def inspect_dump(blob: bytes, parquet) -> dict:
    import pandas as pd

    source = parquet.ParquetFile(io.BytesIO(blob))
    if source.metadata.num_rows > 150_000:
        raise ProbeError("unexpected_recent_dump_row_count")
    if not set(COLUMNS).issubset(source.schema_arrow.names):
        raise ProbeError("unexpected_dump_schema")
    matches = {pair: [] for pair in TARGETS}
    min_date = max_date = None
    for batch in source.iter_batches(batch_size=8192, columns=COLUMNS):
        frame = batch.to_pandas()
        if not (frame["adjusted"].eq("none").all() and frame["interval"].eq("1d").all()
                and frame["currency"].eq("CNY").all()):
            raise ProbeError("unexpected_dump_price_basis")
        dates = pd.to_datetime(frame["date_ms"], unit="ms", utc=True).dt.tz_convert(
            "Asia/Shanghai").dt.strftime("%Y-%m-%d")
        if not dates.empty:
            min_date = min(min_date or dates.min(), dates.min())
            max_date = max(max_date or dates.max(), dates.max())
        for symbol, day in TARGETS:
            for _, row in frame.loc[frame["thscode"].eq(symbol) & dates.eq(day)].iterrows():
                prices = [float(row[field]) for field in COLUMNS[-4:]]
                import math
                valid = (all(math.isfinite(value) and value > 0 for value in prices)
                         and prices[1] >= max(prices[0], prices[3])
                         and prices[2] <= min(prices[0], prices[3]))
                matches[(symbol, day)].append({"valid_ohlc": valid,
                    **{field: value if math.isfinite(value) else None
                       for field, value in zip(COLUMNS[-4:], prices)}})
    return {"read_only": True, "price_basis": "unadjusted_only",
            "download_bytes": len(blob), "total_rows": source.metadata.num_rows,
            "min_date": min_date, "max_date": max_date,
            "targets": [{"thscode": symbol, "trade_date": day,
                         "count": len(rows), "duplicate_key": len(rows) > 1,
                         "rows": rows} for (symbol, day), rows in matches.items()]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, help="Inspect an existing parquet offline (maximum 25 MiB)")
    args = parser.parse_args(argv)
    try:
        blob = read_file(args.file) if args.file is not None else None
        try:
            import pyarrow.parquet as parquet
        except ImportError:
            raise ProbeError("optional_dependency_pyarrow_missing") from None
        if args.file is None:
            import requests
            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
            from qagent.config import get_settings
            from qagent.providers.fuyao import FuyaoClient

            settings = get_settings()
            if not settings.fuyao_api_key:
                raise ProbeError("fuyao_api_key_missing")
            client = FuyaoClient(settings.fuyao_api_key, base_url=settings.fuyao_base_url,
                                 max_attempts=1, request_timeout_seconds=10)
            data = client.request_data(ENDPOINT)
            url = data.get("presigned_url")
            if not isinstance(url, str):
                raise ProbeError("download_link_missing")
            with requests.Session() as session:
                blob = download(session, url)
        print(json.dumps(inspect_dump(blob, parquet), allow_nan=False))
        return 0
    except Exception as exc:
        # Never serialize upstream exception text: it may contain a signed URL.
        error = str(exc) if isinstance(exc, ProbeError) else "probe_failed"
        print(json.dumps({"read_only": True, "error": error}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
