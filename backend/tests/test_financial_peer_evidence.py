from copy import deepcopy

import pytest

from test_financial_prospective_integration import prospective
from test_financial_challenger_forward import signed
import financial_peer_evidence as peer


def evidence_entry(document, request, rows):
    return {"request": request, "received_at": document["finished_at"], "response": signed({
        "source": "datahubco", "data_source": "datahubco", "status": "observed" if rows else "no_rows",
        "request": {k: v for k, v in request.items() if k != "source"},
        "fetched_at": document["started_at"], "fields": list(rows[0]) if rows else request["fields"].split(","),
        "rows": rows, "research_only": True, "decision_weight": False, "activation_allowed": False,
        "coverage": {"rows": len(rows), "row_limit": request["limit"],
                     "page_limit_reached": len(rows) == request["limit"]}})}


def inputs(monkeypatch):
    from collect_daily_documented_research import BATCH_APIS, batch_params
    document, _ = prospective(monkeypatch)
    extras = [f"601{i:03}.SH" for i in range(12)]
    caps = {s: 100 + i for i, s in enumerate(document["symbols"])}
    caps.update({s: 100 + i for i, s in enumerate(extras)})
    catalogue = [{"ts_code": s, "industry": "vendor", "list_status": "L"} for s in caps]
    market = [{"ts_code": s, "trade_date": document["trade_date"], "total_mv": v} for s, v in caps.items()]
    raw = {"catalogue": [evidence_entry(document, peer._request(document, "stock_basic"), catalogue)],
           "market_caps": [evidence_entry(document, peer._request(document, "daily_basic"), market)],
           "financial": {}}
    _, selected, _, _ = peer._selection(document, raw)
    for symbol in selected:
        raw["financial"][symbol] = {}
        row = {"ts_code": symbol, "end_date": "20260630", "ann_date": "20260801",
               "report_type": "1", "comp_type": "1", "n_cashflow_act": 30,
               "n_income": 10, "total_revenue": 100, "trade_date": "20260911", "pe": 10,
               "total_mv": caps[symbol], "total_assets": 1000, "total_liab": 300, "roe": 10,
               "netprofit_margin": 20, "type": "预增", "p_change_min": 1, "p_change_max": 2}
        for api in BATCH_APIS:
            params = batch_params(api, symbol, document["period"], document["trade_date"])
            request = {"source": "datahubco", "api": api, "limit": params.pop("limit"),
                       "params": params, "offset": 0, "fields": None}
            raw["financial"][symbol][api] = evidence_entry(document, request, [deepcopy(row)])
    return document, raw


def test_replay_qualified_controls_without_original_rerank(monkeypatch):
    document, raw = inputs(monkeypatch)
    before = deepcopy(document)
    section = peer._build(document, raw)
    assert section["status"] == "available", section["reasons"]
    assert len(section["rows"]) == 10
    assert not set(section["selected_symbols"]) & set(document["symbols"])
    assert document == before
    document["peer_control_evidence"] = section
    assert peer.validate_peer_evidence(document) == section


def test_missing_bulk_anchors_use_verified_original_individual_evidence(monkeypatch):
    document, raw = inputs(monkeypatch)
    expected = peer._build(document, raw)
    originals = set(document["symbols"])
    for key in ("catalogue", "market_caps"):
        response = raw[key][0]["response"]
        response["rows"] = [row for row in response["rows"] if row["ts_code"] not in originals]
        response["coverage"]["rows"] = len(response["rows"])
        signed(response)
    section = peer._build(document, raw)
    assert section["status"] == "available"
    assert section["selected_symbols"] == expected["selected_symbols"]
    assert section["rows"] == expected["rows"]
    assert section["policy"]["anchor_source"].startswith("verified_original_individual")
    document["peer_control_evidence"] = section
    assert peer.validate_peer_evidence(document) == section


@pytest.mark.parametrize("key,field,value,reason", [
    ("catalogue", "industry", "different", "peer_original_industry_mismatch"),
    ("market_caps", "total_mv", 999, "peer_original_market_cap_mismatch"),
])
def test_present_bulk_anchor_conflicts_fail_closed(monkeypatch, key, field, value, reason):
    document, raw = inputs(monkeypatch)
    symbol = document["financial_candidate"]["rankings"][0]["stock_id"]
    response = raw[key][0]["response"]
    next(row for row in response["rows"] if row["ts_code"] == symbol)[field] = value
    signed(response)
    section = peer._build(document, raw)
    assert section["status"] == "unavailable" and section["rows"] == []
    assert section["reasons"] == [reason]


@pytest.mark.parametrize("change", [
    lambda r: r["market_caps"][0]["response"]["rows"][0].update(trade_date="20260910"),
    lambda r: r["catalogue"][0]["response"]["rows"].append(dict(r["catalogue"][0]["response"]["rows"][0])),
    lambda r: r["catalogue"][0]["response"].update(fetched_at="2026-09-10T16:40:00+08:00"),
    lambda r: r["catalogue"][0]["response"].update(fields=["ts_code"]),
    lambda r: r["catalogue"][0]["response"].update(activation_allowed=True),
])
def test_bad_bulk_proof_fails_closed_even_resigned(monkeypatch, change):
    document, raw = inputs(monkeypatch)
    change(raw)
    for key in ("market_caps", "catalogue"):
        for entry in raw[key]:
            response = entry["response"]
            response["coverage"]["rows"] = len(response["rows"])
            signed(response)
    section = peer._build(document, raw)
    assert section["status"] == "unavailable" and section["rows"] == []
    document["peer_control_evidence"] = section
    peer.validate_peer_evidence(document)


def test_nested_financial_wrong_date_rejected(monkeypatch):
    document, raw = inputs(monkeypatch)
    first = next(iter(raw["financial"].values()))
    first["daily_basic"]["response"]["rows"][0]["trade_date"] = "20260910"
    signed(first["daily_basic"]["response"])
    assert peer._build(document, raw)["reasons"] == ["peer_financial_trade_date_mismatch"]


def test_qualification_failure_excluded_not_replaced(monkeypatch):
    document, raw = inputs(monkeypatch)
    symbol = next(iter(raw["financial"]))
    response = raw["financial"][symbol]["daily_basic"]["response"]
    response["rows"][0]["pe"] = -1
    signed(response)
    section = peer._build(document, raw)
    assert section["status"] == "available"
    assert len(section["selected_symbols"]) == 10 and len(section["rows"]) == 9
    assert section["excluded"][0]["symbol"] == symbol


def test_tampered_derived_rows_not_trusted(monkeypatch):
    document, raw = inputs(monkeypatch)
    section = peer._build(document, raw)
    section["rows"][0]["total_mv"] = "999"
    document["peer_control_evidence"] = signed(section)
    with pytest.raises(ValueError, match="replay_mismatch"):
        peer.validate_peer_evidence(document)


def test_budget_stops_before_first_request(monkeypatch):
    document, _ = inputs(monkeypatch)
    calls = []
    section = peer.collect_peer_evidence(document, base_url="http://127.0.0.1:8000",
                                        query=lambda *a, **kw: calls.append(kw), deadline_monotonic=0)
    assert not calls and section["status"] == "unavailable"


def test_first_page_cap_is_unknown_completeness_and_offset_pages_rejected(monkeypatch):
    document, _ = inputs(monkeypatch)
    rows = [{"ts_code": str(i), "industry": "vendor", "list_status": "L"} for i in range(5000)]
    first = evidence_entry(document, peer._request(document, "stock_basic"), rows)
    second = evidence_entry(document, peer._request(document, "stock_basic", 5000), deepcopy(rows))
    assert len(peer._pages([first], "stock_basic", document)) == 5000
    with pytest.raises(ValueError, match="first_page_required"):
        peer._pages([first, second], "stock_basic", document)
    first["response"]["rows"][1]["ts_code"] = first["response"]["rows"][0]["ts_code"]
    signed(first["response"])
    with pytest.raises(ValueError, match="duplicate"):
        peer._pages([first], "stock_basic", document)
