from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
from fastapi.testclient import TestClient

from qagent.api import routes
from qagent.app import create_app
from qagent.market.etf_exposure import (
    EtfExposureResponse,
    EtfExposureService,
    build_etf_overlaps,
)


class FakeResponse:
    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self) -> None:
        return None


def _basic_html(name: str, tracking_index: str) -> str:
    return f"""
    <table>
      <tr><th>基金全称</th><td>{name}基金</td><th>基金简称</th><td>{name}</td></tr>
      <tr><th>基金代码</th><td>563080（主代码）</td><th>基金类型</th><td>指数型-股票</td></tr>
      <tr><th>业绩比较基准</th><td>{tracking_index}收益率</td><th>跟踪标的</th><td>{tracking_index}</td></tr>
    </table>
    """


def _holdings_payload(rows: list[tuple[str, str, str]]) -> str:
    body = "".join(
        f"<tr><td>{index}</td><td>{symbol}</td><td>{name}</td><td>{weight}</td></tr>"
        for index, (symbol, name, weight) in enumerate(rows, start=1)
    )
    content = (
        "<h4>2026年2季度股票投资明细 截止至：<font>2026-06-30</font></h4>"
        "<table><thead><tr><th>序号</th><th>股票代码</th><th>股票名称</th>"
        f"<th>占净值 比例</th></tr></thead><tbody>{body}</tbody></table>"
    )
    escaped = content.replace('"', '\\"')
    return f'var apidata={{content:"{escaped}",arryear:[2026],curyear:2026}};'


def _industry_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"行业类别": "制造业", "占净值比例": 61.0, "截止时间": "2026-06-30"},
            {"行业类别": "金融业", "占净值比例": 12.0, "截止时间": "2026-06-30"},
            {"行业类别": "制造业", "占净值比例": 58.0, "截止时间": "2026-03-31"},
        ]
    )


def test_etf_exposure_loads_disclosures_and_uses_local_cache(tmp_path: Path):
    calls = []

    def http_get(url, **kwargs):
        calls.append((url, kwargs))
        if "jbgk_" in url:
            return FakeResponse(_basic_html("中证A50ETF易方达", "中证A50指数"))
        return FakeResponse(
            _holdings_payload(
                [
                    ("300308", "中际旭创", "10.57%"),
                    ("300750", "宁德时代", "9.61%"),
                ]
            )
        )

    service = EtfExposureService(
        http_get=http_get,
        industry_loader=lambda **_: _industry_frame(),
        cache_dir=tmp_path,
        clock=lambda: datetime(2026, 8, 2, tzinfo=timezone.utc),
    )

    profile = service.load_profile("CN:563080", "中证A50ETF易方达")
    cached = service.load_profile("CN:563080", "中证A50ETF易方达")

    assert profile.tracking_index == "中证A50指数"
    assert profile.exposure_group == "宽基ETF:中证A50"
    assert profile.holdings_as_of.isoformat() == "2026-06-30"
    assert profile.holdings_coverage_pct == 20.18
    assert [item.instrument_id for item in profile.holdings] == ["CN:300308", "CN:300750"]
    assert profile.industries_as_of.isoformat() == "2026-06-30"
    assert [item.name for item in profile.industries] == ["制造业", "金融业"]
    assert profile.data_status == "complete"
    assert cached == profile
    assert len(calls) == 2


def test_etf_overlap_is_a_disclosed_top_holdings_lower_bound(tmp_path: Path):
    holdings = {
        "563080": [("300308", "中际旭创", "10.00%"), ("300750", "宁德时代", "8.00%")],
        "159595": [("300308", "中际旭创", "9.00%"), ("600519", "贵州茅台", "7.00%")],
    }

    def http_get(url, **kwargs):
        symbol = "563080" if "563080" in str(url) or "563080" in str(kwargs) else "159595"
        if "jbgk_" in url:
            name = "中证A50ETF易方达" if symbol == "563080" else "中证A50ETF大成"
            return FakeResponse(_basic_html(name, "中证A50指数"))
        return FakeResponse(_holdings_payload(holdings[symbol]))

    service = EtfExposureService(
        http_get=http_get,
        industry_loader=lambda **_: _industry_frame(),
        cache_dir=tmp_path,
        clock=lambda: datetime(2026, 8, 2, tzinfo=timezone.utc),
    )
    profiles = [
        service.load_profile("CN:563080", "中证A50ETF易方达"),
        service.load_profile("CN:159595", "中证A50ETF大成"),
    ]

    overlap = build_etf_overlaps(profiles)[0]

    assert overlap.same_tracking_index is True
    assert overlap.disclosed_overlap_lower_bound_pct == 9.0
    assert [item.instrument_id for item in overlap.shared_constituents] == ["CN:300308"]
    assert overlap.status == "measured"


def test_etf_exposure_fails_closed_when_sources_are_unavailable(tmp_path: Path):
    def unavailable(*args, **kwargs):
        raise RuntimeError("source unavailable")

    service = EtfExposureService(
        http_get=unavailable,
        industry_loader=unavailable,
        cache_dir=tmp_path,
        clock=lambda: datetime(2026, 8, 2, tzinfo=timezone.utc),
    )

    profile = service.load_profile("CN:159999", "测试ETF")

    assert profile.tracking_index is None
    assert profile.exposure_group is None
    assert profile.holdings == []
    assert profile.industries == []
    assert profile.data_status == "unavailable"
    assert profile.errors == [
        "basic_metadata:RuntimeError",
        "holdings:RuntimeError",
        "industries:RuntimeError",
    ]


def test_etf_exposure_api_only_admits_catalogued_etfs(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'etf-api.db'}")
    routes._repo().replace_tradable_instruments(
        [
            SimpleNamespace(
                instrument_id="CN:563080",
                symbol="563080",
                name="中证A50ETF易方达",
                label="中证A50ETF易方达 563080.SH",
                asset_type="etf",
                exchange="SH",
                source="test",
            ),
            SimpleNamespace(
                instrument_id="CN:000001",
                symbol="000001",
                name="平安银行",
                label="平安银行 000001.SZ",
                asset_type="stock",
                exchange="SZ",
                source="test",
            ),
        ]
    )

    class StubService:
        def build_response(self, instruments):
            assert instruments == [("CN:563080", "中证A50ETF易方达")]
            return EtfExposureResponse(
                profiles=[],
                overlaps=[],
                data_health={"etf_exposure_source": "stub"},
            )

    monkeypatch.setattr(routes, "_etf_exposure_service", StubService())
    response = TestClient(create_app()).get(
        "/api/etf-exposures?instrument_ids=CN:563080,CN:000001&limit=16"
    )

    assert response.status_code == 200
    assert response.json()["data_health"] == {
        "etf_exposure_source": "stub",
        "etf_exposure_requested": "2",
        "etf_exposure_catalog_matched": "1",
        "etf_exposure_catalog_missing": "1",
        "etf_exposure_cache": "local_disk",
    }


def test_paper_candidate_asset_type_keeps_unknown_sources_explicit():
    assert routes._paper_snapshot_asset_type(
        SimpleNamespace(
            card={"asset_type": "ETF", "instrument_label": "测试ETF 159999.SZ"},
            instrument_id="CN:159999",
        )
    ) == "etf"
    assert routes._paper_snapshot_asset_type(
        SimpleNamespace(card={}, instrument_id="CN:000001")
    ) == "unknown"


def test_empty_paper_portfolio_lookthrough_endpoint(tmp_path: Path, monkeypatch):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'empty-lookthrough.db'}",
    )

    response = TestClient(create_app()).get(
        "/api/paper-trades/look-through-risk?provider=free&reporting_scope=legacy"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["status"] == "empty"
    assert payload["summary"]["position_count"] == 0
    assert payload["warnings"] == []
    assert payload["data_health"]["portfolio_lookthrough_mode"] == "advisory_only"
