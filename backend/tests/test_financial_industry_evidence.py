from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import financial_industry_evidence as industry
sys.path.pop(0)


SYMBOLS = ["600001.SH", "000001.SZ"]
DAY = "20260918"


def signed(value):
    value.pop("result_digest", None)
    value["result_digest"] = industry.digest(value)
    return value


def entry(symbol, name="银行"):
    request = industry.industry_request(symbol)
    return {"request": request, "received_at": "2026-09-18T17:00:01+08:00", "response": signed({
        "source": "datahubco", "data_source": "datahubco", "status": "observed",
        "request": {k: v for k, v in request.items() if k != "source"},
        "fetched_at": "2026-09-18T09:00:00+00:00", "fields": ["ts_code", "industry"],
        "rows": [{"ts_code": symbol, "industry": name}], "research_only": True,
        "decision_weight": False, "activation_allowed": False,
        "coverage": {"rows": 1, "row_limit": 2, "page_limit_reached": False}})}


def test_all_selected_homogeneous_current_observation_without_mutation():
    raw = {s: entry(s, " 银行 ") for s in SYMBOLS}
    original = deepcopy(raw)
    result = industry.build_industry_evidence(SYMBOLS, raw, DAY)
    assert result["status"] == "available"
    assert result["taxonomy"] == "datahubco.stock_basic.industry"
    assert result["semantics"] == "current_observation_not_historical_pit"
    assert [r["industry"] for r in result["rows"]] == ["银行", "银行"]
    assert raw == original
    assert industry.validate_industry_evidence(result, SYMBOLS, DAY) == result


@pytest.mark.parametrize("change", [
    lambda r: r.update(status="no_rows"),
    lambda r: r.update(status="unknown"),
    lambda r: r.update(source="promax"),
    lambda r: r.update(data_source="unknown"),
    lambda r: r.update(activation_allowed=True),
    lambda r: r["request"].update(params={"ts_code": "600999.SH"}),
    lambda r: r.update(fetched_at="2026-09-18"),
    lambda r: r.update(fetched_at="2026-09-17T17:00:00+08:00"),
    lambda r: r.update(fetched_at="2026-09-18T17:00:02+08:00"),
    lambda r: r["coverage"].update(page_limit_reached=True),
    lambda r: r["coverage"].update(rows=True),
    lambda r: r.update(fields=["ts_code"]),
    lambda r: r["rows"][0].update(ts_code="600999.SH"),
    lambda r: r["rows"][0].update(industry="  "),
    lambda r: r["rows"][0].update(industry=None),
    lambda r: r["rows"].append(dict(r["rows"][0])),
    lambda r: r["rows"].append({"ts_code": "600001.SH", "industry": "汽车"}),
])
def test_resigned_bad_provider_evidence_is_unavailable_without_partial_fill(change):
    raw = {s: entry(s) for s in SYMBOLS}
    change(raw[SYMBOLS[0]]["response"])
    signed(raw[SYMBOLS[0]]["response"])
    result = industry.build_industry_evidence(SYMBOLS, raw, DAY)
    assert result["status"] == "unavailable" and result["rows"] == []
    assert [f["symbol"] for f in result["failures"]] == [SYMBOLS[0]]
    assert industry.validate_industry_evidence(result, SYMBOLS, DAY) == result


def test_missing_symbol_and_unsigned_response_retained_as_failures():
    raw = {SYMBOLS[0]: entry(SYMBOLS[0])}
    raw[SYMBOLS[0]]["response"].pop("result_digest")
    result = industry.build_industry_evidence(SYMBOLS, raw, DAY)
    assert len(result["failures"]) == 2
    assert result["raw_evidence"] == raw


@pytest.mark.parametrize("value", ["unknown", " Unknown ", "未知", "未分类", "NULL", "n/a", "其他", "暂无数据"])
def test_unknown_industry_tokens_never_form_common_industry(value):
    result = industry.build_industry_evidence(SYMBOLS, {s: entry(s, value) for s in SYMBOLS}, DAY)
    assert result["status"] == "unavailable" and result["rows"] == []
    assert all(item["reason"] == "industry_missing" for item in result["failures"])


@pytest.mark.parametrize("field,value", [("taxonomy", "SW"), ("version", 2),
    ("rows", []), ("failures", [{"symbol": "bad"}]), ("status", "unavailable")])
def test_resigned_normalization_tampering_rejected(field, value):
    result = industry.build_industry_evidence(SYMBOLS, {s: entry(s) for s in SYMBOLS}, DAY)
    result[field] = value
    signed(result)
    with pytest.raises(ValueError, match="replay_mismatch"):
        industry.validate_industry_evidence(result, SYMBOLS, DAY)


def test_other_universe_and_historical_replay_refused():
    raw = {s: entry(s) for s in SYMBOLS}
    with pytest.raises(ValueError, match="universe_invalid"):
        industry.build_industry_evidence(SYMBOLS[:1], raw, DAY)
    assert industry.build_industry_evidence(SYMBOLS, raw, "20260917")["status"] == "unavailable"
