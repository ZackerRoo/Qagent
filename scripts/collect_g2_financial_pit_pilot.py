#!/usr/bin/env python3
"""One prospective G2 financial evidence pilot. No database, scheduler or ranking writes.

Register an explicit stock cohort before G2 source capture, collect at most one
Datahubco fina_indicator and one ProMax daily_basic page per stock, then bind
the evidence to the naturally captured G2 source. This is a diagnostic sidecar:
the existing frozen collector never reads it.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, time, timezone
from decimal import Decimal, InvalidOperation
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
from zoneinfo import ZoneInfo

from collect_documented_research import collect, local_origin
from collect_daily_documented_research import batch_params
from research_financial_enrichment import SYMBOL, digest

PROTOCOL = "g2-financial-pit-pilot-v1"
APIS = (("datahubco", "fina_indicator"), ("promax", "daily_basic"))
MAX_STOCKS = 20
MAX_REQUESTS = 40
BUDGET_SECONDS = 600
PAGE_LIMIT = 12
DATE = re.compile(r"\d{8}\Z")
HEX = re.compile(r"[0-9a-f]{64}\Z")
FINANCIAL_FIELDS = ("tr_yoy", "netprofit_yoy", "grossprofit_margin",
                    "netprofit_margin", "roe")
VALUATION_FIELDS = ("total_mv", "pe", "pe_ttm", "pb", "turnover_rate", "volume_ratio")
G2_FEATURE_MAP = {
    "earnings_yield": {"api": "daily_basic", "field": "pe_ttm", "transform": "1 / positive pe_ttm"},
    "return_on_equity": {"api": "fina_indicator", "field": "roe", "transform": "source percent"},
    "gross_margin": {"api": "fina_indicator", "field": "grossprofit_margin", "transform": "source percent"},
    "revenue_growth": {"api": "fina_indicator", "field": "tr_yoy", "transform": "source percent"},
    "earnings_growth": {"api": "fina_indicator", "field": "netprofit_yoy", "transform": "source percent"},
}
MARKETCAP_MAP = {"api": "daily_basic", "field": "total_mv",
                 "transform": "positive total_mv * 10000 CNY"}


def utc_now():
    return datetime.now(timezone.utc)


def timestamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("timezone_required")
    return parsed.astimezone(timezone.utc)


def ymd(value):
    if not isinstance(value, str) or not DATE.fullmatch(value):
        raise ValueError("invalid_date")
    return datetime.strptime(value, "%Y%m%d").date()


def _sealed(value):
    value["result_digest"] = digest(value)
    return value


def _check_seal(value):
    if not isinstance(value, dict) or not HEX.fullmatch(str(value.get("result_digest", ""))):
        raise ValueError("invalid_digest")
    if digest({k: v for k, v in value.items() if k != "result_digest"}) != value["result_digest"]:
        raise ValueError("invalid_digest")
    return value


def _load(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("unsafe_evidence_file")
    return json.loads(path.read_text())


def _atomic(path, value, *, replace=False):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=".g2-pit-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o400)
        if replace:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def _lock(directory):
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("unsafe_pilot_directory")
    fd = os.open(directory / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def register(directory, universe_file, signal_date, period, source_dir, *, clock=utc_now):
    """Create an immutable, dated cohort from a stock directory or scan request list."""
    day, report_period = ymd(signal_date), ymd(period)
    if report_period > day or report_period.strftime("%m%d") not in {"0331", "0630", "0930", "1231"}:
        raise ValueError("invalid_report_period")
    registered = clock()
    if (not isinstance(registered, datetime) or registered.tzinfo is None
            or registered.utcoffset() is None):
        raise ValueError("timezone_required")
    if day < registered.astimezone(ZoneInfo("Asia/Shanghai")).date():
        raise ValueError("historical_registration_forbidden")
    if source_dir.is_symlink() or not source_dir.is_dir():
        raise ValueError("invalid_g2_source_directory")
    raw = universe_file.read_bytes()
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("universe_file_too_large")
    universe = json.loads(raw)
    if not isinstance(universe, dict) or universe.get("kind") not in {
        "tradable_stock_directory", "scan_request_list"
    } or not isinstance(universe.get("symbols"), list):
        raise ValueError("invalid_universe")
    symbols = universe["symbols"]
    if not 1 <= len(symbols) <= MAX_STOCKS or len(set(symbols)) != len(symbols):
        raise ValueError("invalid_cohort_size")
    if any(not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol) for symbol in symbols):
        raise ValueError("invalid_stock_id")
    if universe.get("signal_date") != signal_date:
        raise ValueError("universe_date_mismatch")
    if directory.exists():
        raise FileExistsError("pilot_already_registered")
    directory.mkdir(mode=0o700)
    manifest = _sealed({"protocol": PROTOCOL, "signal_date": signal_date,
                        "period": period, "registered_at_utc": registered.isoformat(),
                        "symbols": symbols, "universe": universe,
                        "source_directory": str(source_dir.resolve()),
                        "universe_file_sha256": sha256(raw).hexdigest(),
                        "implementation_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
                        "research_only": True, "decision_weight": False,
                        "activation_allowed": False})
    _atomic(directory / "registration.json", manifest)
    return manifest


def _state(directory, manifest, *, clock=utc_now):
    path = directory / "state.json"
    if path.exists():
        state = _check_seal(_load(path))
        if state.get("registration_digest") != manifest["result_digest"]:
            raise ValueError("registration_mismatch")
        return state
    state = _sealed({"registration_digest": manifest["result_digest"],
                     "started_at_utc": clock().isoformat(), "entries": {}})
    _atomic(path, state)
    return state


def _save_state(directory, state):
    state.pop("result_digest", None)
    _atomic(directory / "state.json", _sealed(state), replace=True)


def _request(symbol, api, manifest):
    params = batch_params(api, symbol, manifest["period"], manifest["signal_date"])
    # A single bounded page, using the already documented research API shape.
    params.pop("limit")
    return {"source": "datahubco" if api == "fina_indicator" else "promax",
            "api": api, "params": params, "limit": PAGE_LIMIT,
            "offset": 0, "fields": None}


def _reuse(archive, manifest):
    if archive is None:
        return {}
    value = _check_seal(_load(archive))
    if (value.get("protocol") != "daily-documented-research-v2"
            or value.get("trade_date") != manifest["signal_date"]
            or value.get("period") != manifest["period"]
            or timestamp(value.get("started_at")) < timestamp(manifest["registered_at_utc"])):
        raise ValueError("financial_archive_mismatch")
    result = {}
    for symbol in manifest["symbols"]:
        for _, api in APIS:
            evidence = value.get("system_evidence", {}).get(symbol, {}).get(api)
            expected = _request(symbol, api, manifest)
            if evidence is None or evidence.get("request") != expected:
                continue
            response = evidence.get("response")
            if not isinstance(response, dict) or response.get("source") != expected["source"]:
                continue
            result[f"{symbol}:{api}"] = {
                "mode": "reused", "request": expected, "response": response,
                "fetched_at": response.get("fetched_at"),
                "archive_sha256": sha256(archive.read_bytes()).hexdigest(),
                "archive_result_digest": value["result_digest"],
            }
    return result


def collect_pilot(directory, *, base_url="http://127.0.0.1:8000", archive=None,
                  query=collect, clock=utc_now):
    local_origin(base_url)
    with _lock(directory):
        manifest = _check_seal(_load(directory / "registration.json"))
        if manifest.get("protocol") != PROTOCOL or (directory / "bound-source.json").exists():
            raise ValueError("pilot_closed_or_invalid")
        state = _state(directory, manifest, clock=clock)
        deadline = timestamp(state["started_at_utc"]).timestamp() + BUDGET_SECONDS
        reuse = _reuse(archive, manifest)
        for symbol in manifest["symbols"]:
            for _, api in APIS:
                key = f"{symbol}:{api}"
                if key in state["entries"]:
                    continue
                request = _request(symbol, api, manifest)
                if key in reuse:
                    state["entries"][key] = reuse[key]
                    _save_state(directory, state)
                    continue
                attempted = sum(item["mode"] == "requested" for item in state["entries"].values())
                if attempted >= MAX_REQUESTS or clock().timestamp() + 70 > deadline:
                    state["entries"][key] = {"mode": "excluded", "request": request,
                                             "reason": "request_or_time_budget_exhausted"}
                    _save_state(directory, state)
                    continue
                # Reserve before the network call: interruption consumes the request.
                entry = {"mode": "requested", "request": request,
                         "status": "interrupted", "reserved_at_utc": clock().isoformat()}
                state["entries"][key] = entry
                _save_state(directory, state)
                try:
                    response = query(base_url, **request)
                    if not isinstance(response, dict):
                        raise ValueError("invalid_system_response")
                    entry.update(status="complete", response=response,
                                 fetched_at=response.get("fetched_at"))
                except Exception:
                    entry.update(status="error", reason="system_request_failed",
                                 fetched_at=clock().isoformat())
                _save_state(directory, state)
            checkpoint = directory / f"checkpoint-{symbol}.json"
            if not checkpoint.exists():
                _atomic(checkpoint, _sealed({"registration_digest": manifest["result_digest"],
                    "symbol": symbol, "entries": {api: state["entries"][f"{symbol}:{api}"]
                                               for _, api in APIS}}))
        return state


def _number(value):
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _classify(entry, symbol, api, manifest, cutoff):
    if entry is None:
        return {"status": "excluded", "reason": "not_collected", "valid_fields": []}
    if entry["mode"] == "excluded":
        return {"status": "excluded", "reason": entry["reason"], "valid_fields": []}
    if entry.get("status") in {"error", "interrupted"}:
        return {"status": "excluded", "reason": entry.get("reason", entry["status"]), "valid_fields": []}
    response = entry.get("response")
    expected = _request(symbol, api, manifest)
    if not isinstance(response, dict) or entry.get("request") != expected:
        return {"status": "excluded", "reason": "request_mismatch", "valid_fields": []}
    try:
        _check_seal(response)
    except ValueError:
        return {"status": "excluded", "reason": "invalid_response_digest", "valid_fields": []}
    if entry.get("fetched_at") != response.get("fetched_at"):
        return {"status": "excluded", "reason": "fetch_time_mismatch", "valid_fields": []}
    if (response.get("source") != expected["source"] or response.get("status") not in {
            "observed", "no_rows", "error"} or response.get("decision_weight") is not False
            or response.get("activation_allowed") is not False):
        return {"status": "excluded", "reason": "response_provenance_invalid", "valid_fields": []}
    if response.get("request") != {
            key: value for key, value in expected.items() if key != "source"}:
        return {"status": "excluded", "reason": "response_request_mismatch", "valid_fields": []}
    try:
        fetched = timestamp(entry["fetched_at"])
    except (KeyError, TypeError, ValueError):
        return {"status": "excluded", "reason": "missing_fetch_time", "valid_fields": []}
    if fetched < timestamp(manifest["registered_at_utc"]):
        return {"status": "excluded", "reason": "fetched_before_registration", "valid_fields": []}
    if entry["mode"] == "requested" and fetched < timestamp(entry["reserved_at_utc"]):
        return {"status": "excluded", "reason": "fetched_before_request", "valid_fields": []}
    if fetched > cutoff:
        return {"status": "excluded", "reason": "fetched_after_capture_started", "valid_fields": []}
    if response["status"] != "observed":
        return {"status": "excluded", "reason": response.get("error", response["status"]), "valid_fields": []}
    rows = response.get("rows")
    if not isinstance(rows, list) or not rows:
        return {"status": "excluded", "reason": "empty_rows", "valid_fields": []}
    if len(rows) >= PAGE_LIMIT or response.get("coverage", {}).get("page_limit_reached") is True:
        return {"status": "excluded", "reason": "page_truncated_or_coverage_unknown", "valid_fields": []}
    fields = FINANCIAL_FIELDS if api == "fina_indicator" else VALUATION_FIELDS
    variants = []
    for row in rows:
        if not isinstance(row, dict) or row.get("ts_code") != symbol:
            return {"status": "excluded", "reason": "stock_identity_mismatch", "valid_fields": []}
        if api == "fina_indicator":
            if row.get("end_date") != manifest["period"]:
                return {"status": "excluded", "reason": "report_period_mismatch", "valid_fields": []}
            announcement = row.get("ann_date")
            if not announcement:
                return {"status": "excluded", "reason": "announcement_missing", "valid_fields": []}
            try:
                cutoff_day = cutoff.astimezone(ZoneInfo("Asia/Shanghai")).date()
                if ymd(announcement) < ymd(manifest["period"]) or ymd(announcement) >= cutoff_day:
                    raise ValueError("announcement_outside_cutoff")
                if row.get("f_ann_date") and (ymd(row["f_ann_date"]) < ymd(manifest["period"])
                                              or ymd(row["f_ann_date"]) >= cutoff_day):
                    raise ValueError("announcement_outside_cutoff")
            except ValueError:
                return {"status": "excluded", "reason": "announcement_outside_cutoff", "valid_fields": []}
            if any(row.get(tag) not in (None, "", "1", 1) for tag in ("report_type", "comp_type")):
                return {"status": "excluded", "reason": "unsupported_report_scope", "valid_fields": []}
        elif row.get("trade_date") != manifest["signal_date"]:
            return {"status": "excluded", "reason": "trade_date_mismatch", "valid_fields": []}
        variants.append(tuple(_number(row.get(field)) for field in fields))
    if len(set(variants)) != 1:
        return {"status": "excluded", "reason": "ambiguous_consumed_revision", "valid_fields": []}
    values = dict(zip(fields, variants[0]))
    valid = [field for field, value in values.items() if value is not None]
    if api == "daily_basic":
        valid = [field for field in valid if field != "total_mv" or values[field] > 0]
    return {"status": "valid" if valid else "excluded",
            "reason": None if valid else "no_consumable_fields", "valid_fields": valid,
            "values": {field: str(values[field]) for field in valid},
            "fetched_at": fetched.isoformat()}


def _g2_feature_available(sections, feature):
    mapping = G2_FEATURE_MAP[feature]
    section = sections[mapping["api"]]
    field = mapping["field"]
    if field not in section["valid_fields"]:
        return False
    return feature != "earnings_yield" or Decimal(section["values"][field]) > 0


def bind_source(directory, source_file, *, clock=utc_now):
    """Attach the prospective pilot to one natural G2 source, without changing it."""
    with _lock(directory):
        manifest = _check_seal(_load(directory / "registration.json"))
        if (directory / "bound-source.json").exists():
            raise ValueError("pilot_already_bound")
        state = _state(directory, manifest)
        if source_file.is_symlink() or source_file.resolve().parent != Path(manifest["source_directory"]):
            raise ValueError("source_path_mismatch")
        source_raw = source_file.read_bytes()
        source = json.loads(source_raw)
        if source.get("protocol") != "g2-forward-source-v2":
            raise ValueError("invalid_g2_source_protocol")
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
        from qagent.research.g2_risk_feature_forward import source_rows
        source_rows(source)
        if source.get("signal_date") != ymd(manifest["signal_date"]).isoformat():
            raise ValueError("source_signal_date_mismatch")
        scan_job_id = source.get("scan_job_id")
        if (not isinstance(scan_job_id, str) or not scan_job_id
                or source_file.name != f"{source['signal_date']}-{sha256(scan_job_id.encode()).hexdigest()}.json"):
            raise ValueError("source_filename_mismatch")
        cutoff = timestamp(source["capture_started_at_utc"])
        captured = timestamp(source["captured_at_utc"])
        observed = clock()
        if (not isinstance(observed, datetime) or observed.tzinfo is None
                or observed.utcoffset() is None):
            raise ValueError("timezone_required")
        local_day = ymd(manifest["signal_date"])
        if (not cutoff <= captured <= observed.astimezone(timezone.utc)
                or cutoff.astimezone(ZoneInfo("Asia/Shanghai")).date() != local_day
                or captured.astimezone(ZoneInfo("Asia/Shanghai")).date() != local_day
                or cutoff.astimezone(ZoneInfo("Asia/Shanghai")).time() < time(15, 30)):
            raise ValueError("source_capture_time_invalid")
        if timestamp(manifest["registered_at_utc"]) >= cutoff:
            raise ValueError("registration_after_capture_started")
        actual = source.get("research_universe")
        if (not isinstance(actual, list) or any(not isinstance(item, str) or
                not re.fullmatch(r"CN:\d{6}", item) for item in actual)
                or len(actual) != len(set(actual))):
            raise ValueError("invalid_g2_universe")
        normalized = {"CN:" + symbol.split(".")[0] for symbol in manifest["symbols"]}
        results = {}
        for symbol in manifest["symbols"]:
            if "CN:" + symbol.split(".")[0] not in actual:
                results[symbol] = {api: {"status": "excluded", "reason": "not_in_g2_source",
                                          "valid_fields": []} for _, api in APIS}
            else:
                results[symbol] = {api: _classify(state["entries"].get(f"{symbol}:{api}"), symbol, api,
                                                  manifest, cutoff) for _, api in APIS}
        total = 2 * len(manifest["symbols"])
        valid = sum(item["status"] == "valid" for per_stock in results.values() for item in per_stock.values())
        field_counts = {field: sum(field in per_stock[api]["valid_fields"]
                                   for per_stock in results.values())
                        for api, fields in (("fina_indicator", FINANCIAL_FIELDS),
                                            ("daily_basic", VALUATION_FIELDS)) for field in fields}
        g2_feature_counts = {feature: sum(_g2_feature_available(sections, feature)
                                          for sections in results.values())
                             for feature in G2_FEATURE_MAP}
        g2_feature_availability = {symbol: {feature: _g2_feature_available(sections, feature)
                                            for feature in G2_FEATURE_MAP}
                                   for symbol, sections in results.items()}
        financial_marketcap_stocks = [symbol for symbol, sections in results.items()
            if all(g2_feature_availability[symbol].values())
            and "total_mv" in sections["daily_basic"]["valid_fields"]]
        report = _sealed({"protocol": PROTOCOL, "registration_digest": manifest["result_digest"],
            "state_digest": state["result_digest"], "source_digest": source["source_digest"],
            "source_file_sha256": sha256(source_raw).hexdigest(),
            "capture_started_at_utc": cutoff.isoformat(), "signal_date": manifest["signal_date"],
            "period": manifest["period"], "pre_registered_symbols": manifest["symbols"],
            "source_universe_count": len(actual),
            "source_intersection_count": len(normalized.intersection(actual)),
            "not_in_source": [s for s in manifest["symbols"] if "CN:" + s.split(".")[0] not in actual],
            "entries": state["entries"], "classified": results,
            "g2_feature_map": G2_FEATURE_MAP, "marketcap_map": MARKETCAP_MAP,
            "g2_feature_availability": g2_feature_availability,
            "coverage": {"expected_slots": total, "closed_slots": sum(len(v) for v in results.values()),
                         "valid_slots": valid, "excluded_slots": total - valid,
                         "requested": sum(e["mode"] == "requested" for e in state["entries"].values()),
                         "reused": sum(e["mode"] == "reused" for e in state["entries"].values()),
                         "field_counts": field_counts,
                         "g2_feature_counts": g2_feature_counts,
                         "financial_and_marketcap_stocks": financial_marketcap_stocks,
                         "joint_valid_stocks": [s for s, v in results.items()
                                                if all(item["status"] == "valid" for item in v.values())]},
            "status": "diagnostic_only" if valid == total and len(normalized.intersection(actual)) == len(normalized)
                      else "partial",
            "research_only": True, "decision_weight": False, "activation_allowed": False,
            "limitations": ["No frozen G2 model or production Ranking consumes this sidecar.",
                            "Pilot coverage is not full G2 cohort coverage or a return estimate.",
                            "A pre-capture response can establish only this observed revision's availability."]})
        _atomic(directory / "bound-source.json", report)
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    command = parser.add_subparsers(dest="command", required=True)
    register_parser = command.add_parser("register")
    register_parser.add_argument("--directory", type=Path, required=True)
    register_parser.add_argument("--universe-file", type=Path, required=True)
    register_parser.add_argument("--signal-date", required=True)
    register_parser.add_argument("--period", required=True)
    register_parser.add_argument("--source-dir", type=Path, required=True)
    collect_parser = command.add_parser("collect")
    collect_parser.add_argument("--directory", type=Path, required=True)
    collect_parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    collect_parser.add_argument("--financial-archive", type=Path)
    bind_parser = command.add_parser("bind-source")
    bind_parser.add_argument("--directory", type=Path, required=True)
    bind_parser.add_argument("--source-file", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "register":
            result = register(args.directory, args.universe_file, args.signal_date, args.period,
                              args.source_dir)
        elif args.command == "collect":
            result = collect_pilot(args.directory, base_url=args.base_url, archive=args.financial_archive)
        else:
            result = bind_source(args.directory, args.source_file)
        print(json.dumps({"status": "ok", "result_digest": result["result_digest"]}))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
