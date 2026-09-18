"""Same-provider current industry evidence for Financial Challenger matching only.

This module does not query providers, alter the candidate universe, or claim a
historical classification. The daily collector supplies its bounded raw queries.
"""
from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo

from research_financial_enrichment import SYMBOL, digest

SHANGHAI = ZoneInfo("Asia/Shanghai")
VERSION = 1
UNKNOWN_INDUSTRIES = frozenset({
    "unknown", "null", "none", "nan", "n/a", "na", "-", "--",
    "未知", "未知行业", "未分类", "暂无", "暂无数据", "无", "其他", "其它",
})


def industry_request(symbol, source="datahubco"):
    if source not in {"datahubco", "promax"} or not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol):
        raise ValueError("industry_request_invalid")
    return {"source": source, "api": "stock_basic", "params": {"ts_code": symbol},
            "limit": 2, "offset": 0, "fields": "ts_code,industry"}


def _timestamp(value, trade_date):
    try:
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError
        if stamp.astimezone(SHANGHAI).strftime("%Y%m%d") != trade_date:
            raise ValueError
        return stamp
    except (ValueError, TypeError, AttributeError):
        raise ValueError("industry_timestamp_invalid") from None


def _industry(symbol, entry, trade_date, source):
    request = industry_request(symbol, source)
    if not isinstance(entry, dict) or entry.get("request") != request:
        raise ValueError("industry_request_mismatch")
    received = _timestamp(entry.get("received_at"), trade_date)
    response = entry.get("response")
    if not isinstance(response, dict):
        raise ValueError("industry_response_invalid")
    if response.get("status") != "observed":
        raise ValueError("industry_not_observed")
    signed = {key: value for key, value in response.items() if key != "result_digest"}
    if response.get("result_digest") != digest(signed):
        raise ValueError("industry_response_digest_mismatch")
    data_source = "datahubco" if source == "datahubco" else "tushare_relay_promax"
    if (response.get("source") != source or response.get("data_source") != data_source
            or response.get("request") != {key: value for key, value in request.items() if key != "source"}
            or response.get("research_only") is not True
            or response.get("decision_weight") is not False
            or response.get("activation_allowed") is not False):
        raise ValueError("industry_response_identity_mismatch")
    if _timestamp(response.get("fetched_at"), trade_date) > received:
        raise ValueError("industry_timestamp_invalid")
    coverage = response.get("coverage")
    if (not isinstance(coverage, dict) or type(coverage.get("rows")) is not int
            or coverage.get("rows") != 1 or type(coverage.get("row_limit")) is not int
            or coverage.get("row_limit") != 2 or coverage.get("page_limit_reached") is not False):
        raise ValueError("industry_coverage_invalid")
    rows, fields = response.get("rows"), response.get("fields")
    if (not isinstance(fields, list) or len(fields) != len(set(fields))
            or not {"ts_code", "industry"}.issubset(fields)
            or not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict)):
        raise ValueError("industry_rows_invalid")
    if rows[0].get("ts_code") != symbol:
        raise ValueError("industry_symbol_mismatch")
    value = rows[0].get("industry")
    if (not isinstance(value, str) or not value.strip()
            or value.strip().casefold() in UNKNOWN_INDUSTRIES):
        raise ValueError("industry_missing")
    return value.strip()


def build_industry_evidence(symbols, raw_evidence, trade_date, *, source="datahubco"):
    """Replay all selected symbols; any incomplete evidence disables all matching."""
    if (not isinstance(symbols, list) or not 1 <= len(symbols) <= 20
            or any(not isinstance(symbol, str) for symbol in symbols)
            or len(set(symbols)) != len(symbols) or not isinstance(raw_evidence, dict)
            or set(raw_evidence) - set(symbols)):
        raise ValueError("industry_universe_invalid")
    try:
        if datetime.strptime(trade_date, "%Y%m%d").strftime("%Y%m%d") != trade_date:
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError("industry_trade_date_invalid") from None
    rows, failures = [], []
    for symbol in symbols:
        industry_request(symbol, source)
        try:
            industry = _industry(symbol, raw_evidence.get(symbol), trade_date, source)
            rows.append({"symbol": symbol, "instrument_id": "CN:" + symbol[:6],
                         "industry": industry, "exposure_group": industry})
        except (ValueError, TypeError) as exc:
            reason = str(exc) if isinstance(exc, ValueError) else "industry_response_invalid"
            failures.append({"symbol": symbol, "reason": reason})
    result = {"version": VERSION, "provider": source,
              "taxonomy": f"{source}.stock_basic.industry",
              "semantics": "current_observation_not_historical_pit",
              "trade_date": trade_date, "symbols": list(symbols),
              "status": "unavailable" if failures else "available",
              "rows": [] if failures else rows, "failures": failures,
              "raw_evidence": deepcopy(raw_evidence),
              "decision_weight": False, "activation_allowed": False}
    result["result_digest"] = digest(result)
    return result


def validate_industry_evidence(evidence, symbols, trade_date, *, source="datahubco"):
    if not isinstance(evidence, dict):
        raise ValueError("industry_evidence_invalid")
    replay = build_industry_evidence(symbols, evidence.get("raw_evidence"), trade_date, source=source)
    if replay != evidence:
        raise ValueError("industry_evidence_replay_mismatch")
    return replay
