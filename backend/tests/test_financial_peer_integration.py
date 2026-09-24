from copy import deepcopy
from datetime import datetime
import json

import pytest

from test_financial_challenger_forward import NOW, batch, database, forward, signed
from test_financial_prospective_integration import prospective
from test_run_financial_forward_research import paths, runner, RUN  # noqa: F401


def peer_document(monkeypatch):
    document, baseline = prospective(monkeypatch)
    import financial_daily_baseline as daily
    import financial_peer_evidence as peer
    monkeypatch.setattr(daily, "validate_archive", lambda value, **kwargs: value["predictions"])
    document["prospective_contract"] = forward.PEER_PROSPECTIVE_CONTRACT
    document["finished_at"] = "2026-09-11T16:45:00+08:00"
    originals = document["symbols"]
    extras = [f"601{i:03}.SH" for i in range(1, 11)]
    caps = {symbol: 100 + i for i, symbol in enumerate(originals)}
    caps.update({symbol: 100 + i for i, symbol in enumerate(extras)})

    def entry(request, rows):
        fields = request["fields"].split(",") if request["fields"] else list(rows[0])
        return {"request": request, "received_at": "2026-09-11T16:44:00+08:00",
                "response": signed({"source": "datahubco", "data_source": "datahubco",
                    "status": "observed", "request": {k: v for k, v in request.items() if k != "source"},
                    "fetched_at": "2026-09-11T16:43:00+08:00", "rows": rows, "fields": fields,
                    "research_only": True, "decision_weight": False, "activation_allowed": False,
                    "coverage": {"rows": len(rows), "row_limit": request["limit"],
                                 "page_limit_reached": len(rows) == request["limit"]}})}

    raw = {"catalogue": [entry(peer._request(document, "stock_basic"),
            [{"ts_code": s, "industry": "vendor", "list_status": "L"} for s in caps])],
           "market_caps": [entry(peer._request(document, "daily_basic"),
            [{"ts_code": s, "trade_date": document["trade_date"], "total_mv": mv}
             for s, mv in caps.items()])], "financial": {}}
    for symbol in extras:
        raw["financial"][symbol] = {}
        for api in batch.BATCH_APIS:
            request = deepcopy(document["system_evidence"][originals[0]][api]["request"])
            request["params"]["ts_code"] = symbol
            rows = deepcopy(document["system_evidence"][originals[0]][api]["response"]["rows"])
            for row in rows:
                row.update(ts_code=symbol, total_mv=caps[symbol])
            raw["financial"][symbol][api] = entry(request, rows)
    document["peer_control_evidence"] = peer._build(document, raw)
    assert document["peer_control_evidence"]["status"] == "available"
    return signed(document), baseline


def test_v5_preserves_original_ranking_baseline_and_replays(monkeypatch):
    original, _ = prospective(monkeypatch)
    document, baseline = peer_document(monkeypatch)
    original_ids = {"CN:" + symbol[:6] for symbol in document["symbols"]}
    import financial_daily_baseline as daily
    calls = []
    def validate(value, **kwargs):
        calls.append(kwargs["eligible_ids"])
        return value["predictions"]
    monkeypatch.setattr(daily, "validate_archive", validate)
    signal = forward.seal(document, baseline, now=NOW)
    assert document["financial_candidate"] == original["financial_candidate"]
    assert document["enrichment_reports"] == original["enrichment_reports"]
    assert document["universe"] == original["universe"]
    assert signal["protocol"] == "financial-rule-forward-v5"
    assert {row["instrument_id"] for row in signal["rankings"]} == original_ids
    assert calls == [original_ids]
    assert signal["control_status"] == "available"
    assert all(key not in original_ids for key in signal["control_order"])
    assert all(p["control_g2_rank"] is None for p in signal["matched_control_pairs"])
    forward.validate_archive(signal)


def test_v5_price_labels_include_selected_controls_only(monkeypatch, tmp_path):
    document, baseline = peer_document(monkeypatch)
    signal = forward.seal(document, baseline, now=NOW)
    symbols = document["symbols"] + document["peer_control_evidence"]["selected_symbols"]
    db = database(tmp_path, symbols=symbols)
    before = db.read_bytes()
    result = forward.evaluate(signal, db, as_of=datetime.fromisoformat("2026-09-18T16:00:00+08:00"))
    five = result["horizons"][0]
    expected = list(dict.fromkeys([r["instrument_id"] for r in signal["rankings"]]
                                  + signal["control_order"]))
    assert [label["instrument_id"] for label in five["labels"]] == expected
    assert five["expected"] == five["completed"] == 10
    assert five["external_control_expected"] == five["external_control_completed"] == 5
    assert five["label_expected"] == five["label_completed"] == 15
    assert five["control_completed"] == 5 and five["paired_complete"] is True
    assert db.read_bytes() == before


def test_missing_external_prices_do_not_inflate_original_coverage_or_lift(monkeypatch, tmp_path):
    document, baseline = peer_document(monkeypatch)
    signal = forward.seal(document, baseline, now=NOW)
    result = forward.evaluate(signal, database(tmp_path, symbols=document["symbols"]),
        as_of=datetime.fromisoformat("2026-09-18T16:00:00+08:00"))["horizons"][0]
    assert result["expected"] == result["completed"] == 10
    assert result["external_control_expected"] == 5 and result["external_control_completed"] == 0
    assert result["label_expected"] == 15 and result["label_completed"] == 10
    assert result["status"] == "partial" and result["paired_complete"] is False
    assert result["candidate_completed"] == 5 and result["lift_pct"] is None


@pytest.mark.parametrize("field,value", [("rows", []), ("selected_symbols", []),
                                          ("status", "unavailable")])
def test_tampered_peer_normalization_rejected(monkeypatch, field, value):
    document, baseline = peer_document(monkeypatch)
    document["peer_control_evidence"][field] = value
    signed(document["peer_control_evidence"])
    with pytest.raises(ValueError, match="peer_evidence_replay_mismatch"):
        forward.seal(signed(document), baseline, now=NOW)


def test_unavailable_peers_seal_without_waiting_or_fabricated_pairs(monkeypatch):
    document, baseline = peer_document(monkeypatch)
    import financial_peer_evidence as peer
    document["peer_control_evidence"] = peer._build(document, {
        "catalogue": [], "market_caps": [], "financial": {}})
    signal = forward.seal(signed(document), baseline, now=NOW)
    assert signal["status"] == "sealed"
    assert signal["control_status"] == "control_unavailable"
    assert signal["control_reasons"] == ["peer_control_evidence_unavailable"]
    assert not signal["matched_control_pairs"]
    forward.validate_archive(signal)


def test_v5_objective_uses_ordered_ids_without_external_or_original_rank_tiebreak():
    candidates = ["CN:600001", "CN:600002"]
    controls = ["CN:601001", "CN:601002", "CN:601003"]
    ids = candidates + controls
    metadata = {key: {"industry": "same", "total_mv": "100"} for key in ids}
    # Even a supplied reverse rank ordering has no role in v5's objective.
    ranks = {key: len(ids) - i for i, key in enumerate(ids)}
    assignments = forward._global_control_assignment(candidates, ids, metadata, ranks, peer_controls=True)
    assert [row[0] for row in assignments] == controls[:2]
    without_external_ranks = forward._global_control_assignment(
        candidates, ids, metadata, {key: ranks[key] for key in candidates}, peer_controls=True)
    assert assignments == without_external_ranks


def test_resigned_v5_pair_tampering_rejected(monkeypatch):
    document, baseline = peer_document(monkeypatch)
    signal = forward.seal(document, baseline, now=NOW)
    signal["matched_control_pairs"][0]["control_g2_rank"] = 1
    signed(signal)
    with pytest.raises(ValueError, match="signal_replay_mismatch"):
        forward.validate_archive(signal)


def test_peer_stage_keeps_original_artifacts_and_shared_deadline(monkeypatch):
    document, _ = prospective(monkeypatch)
    import financial_peer_evidence as peer
    calls = []
    def query(base_url, **request):
        symbol = request["params"]["ts_code"]
        return (document["industry_evidence"]["raw_evidence"][symbol]["response"]
                if request["api"] == "stock_basic" else document["system_evidence"][symbol][request["api"]]["response"])
    def collect(value, **kwargs):
        calls.append((deepcopy(value), kwargs))
        return {"status": "unavailable"}
    monkeypatch.setattr(peer, "collect_peer_evidence", collect)
    monkeypatch.setattr(batch.time, "monotonic", lambda: 100.0)
    options = dict(query=query, universe=document["universe"], daily_frozen_industry=True)
    original = batch.run_batch(document["symbols"], "20260630", "20260911", **options)
    result = batch.run_batch(document["symbols"], "20260630", "20260911",
                             peer_controls=True, bounded_same_day=True, **options)
    assert len(calls) == 1 and calls[0][1]["deadline_monotonic"] == 700
    assert calls[0][0]["financial_candidate"]["status"] == "ranked"
    assert result["financial_candidate"] == original["financial_candidate"]
    assert result["enrichment_reports"] == original["enrichment_reports"]
    assert result["status"] == "observed"


@pytest.mark.parametrize("options", [{}, {"daily_frozen_industry": True}, {"bounded_same_day": True}])
def test_peer_option_requires_both_existing_guards(options):
    with pytest.raises(ValueError, match="peer_controls_require"):
        batch.run_batch(["600001.SH"], "20260630", "20260911", peer_controls=True, **options)


def test_v5_original_report_chronology_and_old_strict_equality(monkeypatch):
    document, baseline = peer_document(monkeypatch)
    forward.seal(document, baseline, now=NOW)
    old = deepcopy(document)
    old["prospective_contract"] = forward.PROSPECTIVE_CONTRACT
    with pytest.raises(ValueError, match="enrichment_capture_mismatch"):
        forward.seal(signed(old), baseline, now=NOW)
    document["system_evidence"][document["symbols"][0]]["cashflow"]["received_at"] = document["finished_at"]
    with pytest.raises(ValueError, match="section_capture_time_order"):
        forward.seal(signed(document), baseline, now=NOW)


def test_runner_recognizes_new_contract_and_seals_unavailable_peers(paths, monkeypatch):  # noqa: F811
    document, baseline = peer_document(monkeypatch)
    import financial_peer_evidence as peer
    import financial_daily_baseline as daily
    document["peer_control_evidence"] = peer._build(document, {
        "catalogue": [], "market_caps": [], "financial": {}})
    signed(document)
    daily_dir, signal_dir, evaluations, runs, db = paths
    (daily_dir / "daily.json").write_text(json.dumps(document))
    monkeypatch.setattr(daily, "collect", lambda *a, **kw: baseline)
    monkeypatch.setattr(runner, "datetime", type("Clock", (), {"now": staticmethod(lambda tz: RUN)}))
    code, report = runner.run(*paths, now=RUN, daily_baseline_source_dir=daily_dir,
        daily_baseline_frozen_dir=daily_dir, daily_baseline_rank_dir=runs / "ranks")
    assert code == 0 and report["seal"]["status"] == "sealed"
    assert report["seal"]["control_status"] == "control_unavailable"
    assert json.loads((signal_dir / "2026-09-11.json").read_text())["protocol"] == "financial-rule-forward-v5"
