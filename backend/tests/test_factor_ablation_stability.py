import copy

import numpy as np
import pandas as pd
import pytest

from qagent.research.factor_ablation_stability import _paired_dates, date_outcomes
from qagent.research.factor_ablation import run_factor_ablation
from test_factor_ablation import payload


def test_paired_halves_leaveout_and_missing_ic_are_descriptive():
    base = [{"signal_date": f"2025-01-0{i + 1}", "sample_rows": 10, "top_count": 1,
             "gross_top_bucket_excess_return_pct": 0., "rank_ic": None} for i in range(4)]
    challenger = copy.deepcopy(base)
    for row, delta in zip(challenger, [2., 2., -1., -1.]):
        row["gross_top_bucket_excess_return_pct"] = delta
    result = _paired_dates(base, challenger)
    gross = result["gross_top_bucket_excess_return_pct"]
    assert gross["all_dates"] == {"observed_pairs": 4, "mean_delta": .5, "positive_fraction": .5}
    assert [half["mean_delta"] for half in gross["chronological_halves"]] == [2., -1.]
    assert gross["leave_one_date_out_mean_range"] == [0., 1.]
    assert result["rank_ic"]["all_dates"]["mean_delta"] is None
    with pytest.raises(ValueError, match="date identities"):
        _paired_dates(base, challenger[::-1])
    challenger[0]["top_count"] = 2
    with pytest.raises(ValueError, match="bucket sizes"):
        _paired_dates(base, challenger)


def test_top_bucket_ties_and_turnover_match_comparator_semantics():
    frame = pd.DataFrame({"signal_date": ["2025-01-01"] * 5 + ["2025-01-02"] * 5,
                          "instrument_id": list("abcde") * 2,
                          "target_excess_return_pct": list(range(5)) * 2})
    results = date_outcomes(frame, np.array([5, 5, 0, 0, 0, 0, 0, 0, 0, 1]), .2)
    assert results[0]["gross_top_bucket_excess_return_pct"] == 0
    assert results[0]["turnover_from_previous"] is None
    assert results[1]["gross_top_bucket_excess_return_pct"] == 4
    assert results[1]["turnover_from_previous"] == 1


def test_real_stability_preserves_metrics_and_discards_models():
    pytest.importorskip("lightgbm")
    sample = payload()
    sample["config"]["seeds"] = [7, 19]
    result = run_factor_ablation(sample, collect_stability=True)
    assert len(result["paired_stability"]["comparisons"]) == 4
    assert result["model_persisted"] is False
    for row in result["results"]:
        assert [seed["seed"] for seed in row["stability"]["seeds"]] == [7, 19]
        assert len(row["stability"]["ensemble_dates"]) == row["lightgbm"]["cross_sections"]
        for seed in row["stability"]["seeds"]:
            dates = seed["dates"]
            gross = np.mean([date["gross_top_bucket_excess_return_pct"] for date in dates])
            turnover = np.mean([date["turnover_from_previous"] for date in dates[1:]])
            assert seed["metrics"]["net_top_bucket_excess_return_pct"] == pytest.approx(gross - turnover * .1, abs=1e-6)
    assert "model_text" not in str(result)
