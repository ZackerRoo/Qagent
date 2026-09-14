#!/usr/bin/env python3
"""Collect one explicit Datahubco research page, without database or account writes."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from qagent.providers.datahubco import DatahubcoClient, DatahubcoError  # noqa: E402
from rank_g2_consensus import publish  # noqa: E402


def collect(client, api, params):
    table = client.query(api, **params)
    report = {
        "protocol": "datahubco-research-page-v1", "source": table.source, "api": table.api,
        "query_params": dict(params),
        "observed_at": datetime.now(timezone.utc).isoformat(), "fields": list(table.fields),
        "rows": list(table.rows), "row_limit": table.row_limit,
        "status": "observed" if table.rows else "no_rows",
        "decision_weight": False, "activation_allowed": False,
        "warnings": ["insecure_http_explicitly_authorized", "coverage_not_established",
                     "raw_table_semantics_unverified", "not_historical_point_in_time_evidence"],
    }
    report["result_digest"] = sha256(json.dumps(
        report, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", required=True)
    parser.add_argument("--params", default="{}", help="JSON object of explicit documented query parameters")
    parser.add_argument("--allow-insecure-http", action="store_true",
                        help="Explicitly authorize sending the environment key over plaintext HTTP")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if not args.allow_insecure_http:
            raise DatahubcoError("insecure_http_not_authorized")
        params = json.loads(args.params)
        if not isinstance(params, dict):
            raise DatahubcoError("invalid_params")
        client = DatahubcoClient(os.environ.get("QAGENT_DATAHUBCO_KEY", ""),
                                allow_insecure_http=True)
        report = collect(client, args.api, params)
        encoded = json.dumps(report, indent=2, allow_nan=False) + "\n"
        if args.output:
            publish(args.output, encoded)
        else:
            print(encoded, end="")
        return 0
    except Exception as exc:
        kind = exc.kind if isinstance(exc, DatahubcoError) else "collection_failed"
        print(json.dumps({"source": "datahubco", "status": "error", "error": kind,
                          "decision_weight": False, "activation_allowed": False}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
