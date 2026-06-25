from qagent.jobs.daily_scan import run_daily_scan
from qagent.market.cn_context import build_market_context
from qagent.providers.fixtures import FixtureMarketDataProvider
from qagent.recommendations.cn_execution import build_trading_constraints


def test_trading_constraints_classify_a_share_boards_and_permissions():
    star = build_trading_constraints("CN:688981", instrument_label="中芯国际 688981.SH")
    bse = build_trading_constraints("CN:920580", instrument_label="科创新材 920580.BJ")
    main = build_trading_constraints("CN:600519", instrument_label="贵州茅台 600519.SH")

    assert star.board == "科创板"
    assert star.price_limit_pct == 20
    assert star.permission_required is True
    assert {item.code for item in star.constraints}.issuperset(
        {"star_market_permission", "t_plus_one", "lot_size_100"}
    )
    assert bse.board == "北交所"
    assert bse.price_limit_pct == 30
    assert any(item.code == "bse_permission" for item in bse.constraints)
    assert main.board == "沪市主板"
    assert main.price_limit_pct == 10
    assert main.permission_required is False


def test_market_context_adds_industry_theme_and_index_labels():
    context = build_market_context("CN:688981", instrument_label="中芯国际 688981.SH")

    assert context.industry == "半导体"
    assert "AI算力供应链" in context.themes
    assert "科创50" in context.index_memberships
    assert context.summary.startswith("半导体")


def test_market_context_adds_storage_chip_theme_labels():
    context = build_market_context("CN:688525", instrument_label="佰维存储 688525.SH")

    assert context.industry == "存储芯片"
    assert "存储芯片" in context.themes
    assert "国产替代" in context.themes


def test_daily_scan_cards_include_cn_constraints_context_and_chinese_summary():
    result = run_daily_scan(
        instrument_ids=["CN:000001"],
        provider=FixtureMarketDataProvider(),
    )

    card = result.cards[0]

    assert card.trading_constraints is not None
    assert card.trading_constraints.t_plus_one is True
    assert any(item.code == "lot_size_100" for item in card.trading_constraints.constraints)
    assert card.market_context is not None
    assert card.market_context.industry == "银行"
    assert card.recommendation_summary is not None
    assert "买点" in card.recommendation_summary.buy_timing
    assert "卖出" in card.recommendation_summary.sell_timing
    assert "CN:" not in card.recommendation_summary.headline
