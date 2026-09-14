#!/usr/bin/env python3
"""Archive one query or catalogue from the local Qagent research API."""
import argparse
import ipaddress
import json
from pathlib import Path
import urllib.parse
import urllib.request

from rank_g2_consensus import publish


def local_origin(value):
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or parsed.username is not None
            or parsed.password is not None or parsed.path not in {"", "/"}
            or parsed.query or parsed.fragment):
        raise ValueError("invalid_local_origin")
    host = parsed.hostname
    if host == "localhost":
        host = "127.0.0.1"
    address = ipaddress.ip_address(host)
    if not address.is_loopback:
        raise ValueError("invalid_local_origin")
    authority = f"[{address}]" if address.version == 6 else str(address)
    if parsed.port is not None:
        authority += f":{parsed.port}"
    return f"{parsed.scheme}://{authority}"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("redirect_blocked")


def collect(base_url, *, catalogue=False, source=None, api=None, params=None,
            limit=100, offset=0, fields=None, opener=None):
    origin = local_origin(base_url)
    if source not in {None, "datahubco", "promax"}:
        raise ValueError("invalid_source")
    if not catalogue and (source is None or not isinstance(api, str) or not api):
        raise ValueError("query_required")
    if not isinstance(params if params is not None else {}, dict):
        raise ValueError("invalid_params")
    if (type(limit) is not int or not 1 <= limit <= 5000
            or type(offset) is not int or not 0 <= offset <= 1000000
            or set(params or {}).intersection({"limit", "offset", "fields"})):
        raise ValueError("invalid_pagination")
    if catalogue:
        url = origin + "/api/documented-research/catalogue"
        request = urllib.request.Request(url, method="GET")
    else:
        encoded = json.dumps({"source": source, "api": api, "params": params or {},
                              "limit": limit, "offset": offset, "fields": fields}, allow_nan=False).encode()
        request = urllib.request.Request(origin + "/api/documented-research/query", data=encoded,
                                         headers={"Content-Type": "application/json"}, method="POST")
    # No environment proxy, redirect, DNS hostname, or direct provider credential.
    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=70) as response:
        if response.status != 200:
            raise ValueError("system_request_failed")
        raw = response.read(16 * 1024 * 1024 + 1)
    if len(raw) > 16 * 1024 * 1024:
        raise ValueError("response_limit")
    report = json.loads(raw)
    if not isinstance(report, dict):
        raise ValueError("invalid_system_response")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--catalogue", action="store_true")
    parser.add_argument("--source", choices=("datahubco", "promax"))
    parser.add_argument("--api")
    parser.add_argument("--params", default="{}", help="JSON query parameters, excluding pagination and fields")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--fields")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = collect(args.base_url, catalogue=args.catalogue, source=args.source,
                         api=args.api, params=json.loads(args.params), limit=args.limit,
                         offset=args.offset, fields=args.fields)
        encoded = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.output:
            publish(args.output, encoded)
        else:
            print(encoded, end="")
        return 1 if report.get("status") in {"error", "incomplete"} else 0
    except Exception:
        print(json.dumps({"status": "error", "error": "documented_research_collection_failed",
                          "decision_weight": False, "activation_allowed": False}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
