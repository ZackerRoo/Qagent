import copy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from qagent.research.g2_forward_source import digest
from qagent.research.g2_risk_feature_freeze import VARIANTS
from test_g2_risk_feature_forward import FROZEN, make_source

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("financial_daily_baseline", ROOT / "scripts/financial_daily_baseline.py")
daily = importlib.util.module_from_spec(spec)
spec.loader.exec_module(daily)
DAY = "2026-09-18"  # Deliberately not an original G2 ten-session signal day.
NOW = datetime(2026, 9, 18, 10, tzinfo=timezone.utc)
IDS = [f"CN:{i:06d}" for i in range(5)]


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


@pytest.fixture(scope="module")
def models():
    # Test actual distributed frozen inference; never silently skip it.
    return daily.load_frozen(FROZEN)


@pytest.fixture
def setup(tmp_path, monkeypatch, models):
    monkeypatch.setattr(daily, "datetime", Clock)
    monkeypatch.setattr(daily, "load_frozen", lambda _: (copy.deepcopy(models[0]), models[1]))
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    old = make_source(source_dir)
    source = json.loads(old.read_text().replace("2026-09-11", DAY))
    old.unlink()
    write(source_dir, source)
    return source_dir, tmp_path / "ranks", source


def write(directory, source, name="a"):
    source.pop("source_digest", None)
    source["source_digest"] = digest(source)
    path = directory / f"{DAY}-{name}.json"
    path.write_text(json.dumps(source))
    return path


def collect(setup):
    return daily.collect(setup[0], FROZEN, setup[1], signal_date=DAY, eligible_ids=IDS, now=NOW)


def test_daily_full_features_matches_frozen_inference_without_g2_sampling(setup, models):
    result = collect(setup)
    assert result["protocol"] == daily.PROTOCOL and result["status"] == "available"
    rows, _ = daily.source_rows(setup[2])
    frame = daily.prepare_features(pd.DataFrame(rows), feature_stage="raw")
    expected = np.mean(np.vstack([m.predict(frame.loc[:, list(VARIANTS["full_features"])].astype("float64"))
                                 for m in models[1]["full_features"]]), axis=0)
    assert [r["full_features"]["score"] for r in result["predictions"]] == list(expected)
    assert not any("without_risk" in row for row in result["predictions"])
    assert len(daily.validate_archive(result, signal_date=DAY, eligible_ids=IDS)) == 7
    assert not (setup[1] / "signals").exists()
    path = setup[1] / f"{DAY}.json"
    raw = path.read_bytes()
    assert path.stat().st_mode & 0o222 == 0
    assert collect(setup) == result and raw == path.read_bytes()


@pytest.mark.parametrize("change", [
    {"capture_started_at_utc": f"{DAY}T06:00:00+00:00"},
    {"captured_at_utc": f"{DAY}T11:00:00+00:00"},
    {"signal_date": "2026-09-17"},
])
def test_invalid_source_time_never_reserves_archive(setup, change):
    setup[2].update(change)
    write(setup[0], setup[2])
    assert collect(setup) is None
    assert not setup[1].exists()


def test_stale_eligible_row_waits_then_first_complete_chronological_source_wins(setup):
    setup[2]["items"][0]["latest_trade_date"] = "2026-09-17"
    write(setup[0], setup[2])
    assert collect(setup) is None
    repaired = copy.deepcopy(setup[2])
    repaired["items"][0]["latest_trade_date"] = DAY
    repaired["captured_at_utc"] = f"{DAY}T08:30:00+00:00"
    write(setup[0], repaired, "z")
    later = copy.deepcopy(repaired)
    later["captured_at_utc"] = f"{DAY}T09:00:00+00:00"
    write(setup[0], later, "b")
    result = collect(setup)
    assert result["source_digest"] == repaired["source_digest"]
    # A late-arriving earlier artifact cannot rewrite the first-sealed sidecar.
    earlier = copy.deepcopy(repaired)
    earlier["captured_at_utc"] = f"{DAY}T08:00:00+00:00"
    write(setup[0], earlier, "c")
    assert collect(setup) == result


def test_source_digest_corruption_waits(setup):
    source = setup[2]
    source["scan_job_id"] = "tampered"
    (setup[0] / f"{DAY}-a.json").write_text(json.dumps(source))
    assert collect(setup) is None


@pytest.mark.parametrize("field", ["rank", "score", "models", "source", "manifest", "coverage", "eligible"])
def test_resigned_structural_tampering_rejected(setup, field):
    result = collect(setup)
    if field == "rank":
        result["predictions"][0]["full_features"]["rank"] = 999
    elif field == "score":
        result["predictions"][0]["full_features"]["score"] = True
    elif field == "models":
        result["models"] = []
    elif field == "source":
        result["source"]["scan_job_id"] = "tampered"
    elif field == "manifest":
        result["frozen_manifest"]["config_sha256"] = "0" * 64
    elif field == "coverage":
        result["coverage"]["scored_rows"] = 0
    else:
        result["eligible_ids"] = result["eligible_ids"][:-1]
    result.pop("result_digest")
    result["result_digest"] = digest(result)
    with pytest.raises(ValueError):
        daily.validate_archive(result)


def test_cross_midnight_completion_is_rejected(setup, monkeypatch):
    class Tomorrow(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 18, 16, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(daily, "datetime", Tomorrow)
    with pytest.raises(ValueError, match="same_day"):
        collect(setup)
    assert not setup[1].exists()


def test_existing_archive_rejects_changed_financial_cohort(setup):
    collect(setup)
    with pytest.raises(ValueError, match="cohort"):
        daily.collect(setup[0], FROZEN, setup[1], signal_date=DAY,
                      eligible_ids=IDS + ["CN:000005"], now=NOW)


def test_no_source_no_model_loading(setup, monkeypatch):
    monkeypatch.setattr(daily, "load_frozen", lambda _: pytest.fail("must not load without source"))
    assert daily.collect(setup[0] / "missing", FROZEN, setup[1], signal_date=DAY,
                         eligible_ids=IDS, now=NOW) is None


def test_oversized_and_symlink_source_rejected(setup, monkeypatch):
    monkeypatch.setattr(daily, "MAX_INPUT_BYTES", 1)
    assert collect(setup) is None
    monkeypatch.setattr(daily, "MAX_INPUT_BYTES", 128 * 1024 * 1024)
    path = setup[0] / f"{DAY}-a.json"
    renamed = setup[0] / "elsewhere"
    path.rename(renamed)
    path.symlink_to(renamed)
    assert collect(setup) is None


def test_changed_model_or_scorer_failure_propagates_without_archive(setup, monkeypatch):
    def fail(_):
        raise ValueError("frozen scorer implementation changed")
    monkeypatch.setattr(daily, "load_frozen", fail)
    with pytest.raises(ValueError, match="scorer"):
        collect(setup)
    assert not setup[1].exists()
