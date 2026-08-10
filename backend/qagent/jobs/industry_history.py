from __future__ import annotations

import argparse
import json
from datetime import date
from typing import Any

from qagent.db import create_session_factory, initialize_database
from qagent.historical_evidence.models import HistoricalEvidenceBundle
from qagent.historical_evidence.providers import build_historical_evidence_provider
from qagent.storage.replay_evidence import ReplayEvidenceRepository


def sync_point_in_time_industries(
    *,
    provider_mode: str = "free",
    start_date: date = date(2021, 11, 1),
    end_date: date,
    database_url: str | None = None,
) -> dict[str, Any]:
    initialize_database(database_url)
    session_factory = create_session_factory(database_url)
    replay = ReplayEvidenceRepository(session_factory, provider_mode)
    base_revision = replay.current_revision()
    inventory = replay.lifecycle_inventory(base_revision, end_date)
    stock_ids = sorted(
        item.instrument_id
        for item in inventory
        if item.security_type in {"stock", "1"}
        and item.listing_date is not None
        and item.listing_date <= end_date
        and (item.delisting_date is None or item.delisting_date > start_date)
    )
    if not stock_ids:
        raise ValueError("industry sync requires a frozen stock lifecycle inventory")
    provider = build_historical_evidence_provider(provider_mode)
    if provider is None:
        raise ValueError(f"industry history provider is unavailable for {provider_mode!r}")
    if hasattr(provider, "request_timeout_seconds"):
        provider.request_timeout_seconds = max(30, provider.request_timeout_seconds)
    bundle = provider.get_industry_evidence(stock_ids, start_date, end_date)
    if bundle.errors:
        raise RuntimeError(" | ".join(bundle.errors[:5]))
    covered = {item.instrument_id for item in bundle.industries}
    if len(covered) < max(50, int(len(stock_ids) * 0.80)):
        raise RuntimeError(
            f"industry history coverage is fail-closed: {len(covered)}/{len(stock_ids)} instruments"
        )
    counts = replay.upsert_point_in_time_evidence(
        HistoricalEvidenceBundle(
            industries=bundle.industries,
            data_health=bundle.data_health,
        )
    )
    return {
        "status": "succeeded",
        "provider_mode": provider_mode,
        "base_dataset_revision": base_revision,
        "dataset_revision": replay.current_revision(),
        "inventory_stock_count": len(stock_ids),
        "industry_snapshot_rows": counts["industries"],
        "industry_snapshot_instruments": len(covered),
        "industry_coverage_ratio": round(len(covered) / len(stock_ids), 6),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "source": "baostock_point_in_time_query",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync point-in-time A-share industries")
    parser.add_argument("--provider", default="free")
    parser.add_argument("--start", type=date.fromisoformat, default=date(2021, 11, 1))
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args()
    print(
        json.dumps(
            sync_point_in_time_industries(
                provider_mode=args.provider,
                start_date=args.start,
                end_date=args.end,
                database_url=args.database_url,
            ),
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
