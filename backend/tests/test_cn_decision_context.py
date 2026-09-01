from qagent.jobs.daily_scan import run_daily_scan
from qagent.market.cn_context import (
    UNKNOWN_ETF_EXPOSURE,
    UNKNOWN_STOCK_INDUSTRY,
    build_market_context,
    infer_etf_exposure_group,
)
from qagent.providers.fixtures import FixtureMarketDataProvider
from qagent.recommendations.cn_execution import build_trading_constraints


def test_trading_constraints_classify_a_share_boards_and_permissions():
    star = build_trading_constraints("CN:688981", instrument_label="中芯国际 688981.SH")
    bse = build_trading_constraints("CN:920580", instrument_label="科创新材 920580.BJ")
    main = build_trading_constraints("CN:600519", instrument_label="贵州茅台 600519.SH")

    assert star.board == "科创板"
    assert star.price_limit_pct == 20
    assert star.permission_required is True
    assert star.minimum_order_quantity == 200
    assert star.quantity_step == 1
    assert {item.code for item in star.constraints}.issuperset(
        {"star_market_permission", "t_plus_one", "lot_size_100"}
    )
    assert bse.board == "北交所"
    assert bse.minimum_order_quantity == 100
    assert bse.quantity_step == 1
    assert bse.price_limit_pct == 30
    assert any(item.code == "bse_permission" for item in bse.constraints)
    assert main.board == "沪市主板"
    assert main.price_limit_pct == 10
    assert main.permission_required is False
    assert main.minimum_order_quantity == 100
    assert main.quantity_step == 100


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


def test_market_context_covers_core_etfs_and_ai_compute_theme_names():
    etf = build_market_context("CN:510300", instrument_label="沪深300ETF 510300.SH")
    optical = build_market_context("CN:002281", instrument_label="光迅科技 002281.SZ")

    assert etf.industry == "宽基ETF:沪深300"
    assert "沪深300ETF" in etf.index_memberships
    assert "指数工具" in etf.themes
    assert optical.industry == "光通信"
    assert "AI算力供应链" in optical.themes
    assert "CPO" in optical.themes


def test_etf_exposure_groups_cover_broad_factor_cross_border_and_sector_products():
    assert infer_etf_exposure_group("中证A50ETF易方达") == "宽基ETF:中证A50"
    assert infer_etf_exposure_group("300自由现金流ETF摩根") == "策略ETF:自由现金流"
    assert infer_etf_exposure_group("中证红利ETF招商") == "策略ETF:红利"
    assert infer_etf_exposure_group("纳指科技ETF景顺") == "跨境ETF:美股科技"
    assert infer_etf_exposure_group("美国50ETF易方达") == "跨境ETF:美国宽基"
    assert infer_etf_exposure_group("标普生物科技ETF嘉实") == "跨境ETF:美国医药"
    assert infer_etf_exposure_group("港股央企红利ETF永赢") == "跨境ETF:港股红利"
    assert infer_etf_exposure_group("科技ETF华宝") == "人工智能/计算机"
    assert infer_etf_exposure_group("银行ETF", current_industry="指数ETF") == "银行"
    assert infer_etf_exposure_group("测试ETF", current_industry="指数ETF") is None
    assert infer_etf_exposure_group("芯片ETF", current_industry="半导体") == "半导体"


def test_unknown_etf_exposure_is_explicit_and_has_no_fake_benchmark():
    context = build_market_context("CN:159999", instrument_label="测试ETF 159999.SZ")

    assert context.industry == UNKNOWN_ETF_EXPOSURE
    assert context.index_memberships == []


def test_unknown_stock_industry_is_explicit_instead_of_generic_composite():
    context = build_market_context("CN:002612", instrument_label="测试公司 002612.SZ")

    assert context.industry == UNKNOWN_STOCK_INDUSTRY
    assert context.industry != "综合"


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
