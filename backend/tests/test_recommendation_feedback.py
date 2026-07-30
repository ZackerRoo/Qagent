from datetime import date, timedelta
from decimal import Decimal

import pandas as pd

from qagent.cards.factor_watch import build_factor_watch_card
from qagent.domain.models import MarketContext
from qagent.factors.models import FactorRanking
from qagent.monitoring.outcomes import OpportunityOutcome
from qagent.monitoring.recommendation_calibration import (
    RecommendationCalibrationBand,
    RecommendationCalibrationCenter,
    RecommendationSignalEffect,
    build_recommendation_calibration_center,
)
from qagent.paper_trading.engine import (
    PaperDailyBenchmark,
    PaperDailyReport,
    PaperDailyReportSummary,
    PaperFailureAttributionItem,
    PaperRiskGateStatus,
)
from qagent.recommendations.feedback import (
    apply_walk_forward_validation_feedback,
    apply_recommendation_feedback_calibration,
    apply_recommendation_feedback_quality_gate,
    apply_paper_trading_feedback,
    paper_trading_feedback_data_health,
    recommendation_feedback_data_health,
    walk_forward_feedback_data_health,
)
from qagent.recommendations.quality_gate import apply_recommendation_quality_gate
from qagent.storage.repository import OpportunitySnapshotRecord


OFFICIAL_CALIBRATION_HEALTH = {
    "recommendation_calibration_scope": "ranking_v3_production",
    "recommendation_calibration_fail_closed": "true",
    "recommendation_calibration_source_official": "10",
}


def test_recommendation_feedback_promotes_working_signals_and_demotes_failed_signals():
    winner = _card("CN:688981", "中芯国际 688981.SH", 0.72, ["fund_flow_positive"])
    loser = _card("CN:000063", "中兴通讯 000063.SZ", 0.72, ["overextended"])
    center = RecommendationCalibrationCenter(
        as_of=date(2026, 7, 1),
        headline="推荐校准：可信度提升",
        verdict="可信度提升",
        reliability_score=0.68,
        baseline_win_rate_10d=0.48,
        baseline_avg_return_10d=0.2,
        score_bands=[
            RecommendationCalibrationBand(
                band="70-80",
                label="70-80 分",
                min_score=0.7,
                max_score=0.8,
                sample_count=16,
                completed_count=12,
                win_rate_10d=0.58,
                avg_return_10d=1.6,
                reliability_score=0.64,
                verdict="有效",
            )
        ],
        signal_effects=[
            RecommendationSignalEffect(
                signal_key="fund_flow_positive",
                label="资金净流入",
                sample_count=14,
                completed_count=11,
                win_rate_10d=0.64,
                avg_return_10d=2.1,
                baseline_avg_return_10d=0.2,
                lift_vs_baseline_10d=1.9,
                reliability_score=0.72,
                weight_action="提高",
                suggested_weight_delta=0.04,
                reason="资金净流入样本明显跑赢基准。",
            ),
            RecommendationSignalEffect(
                signal_key="overextended",
                label="短线过热",
                sample_count=13,
                completed_count=10,
                win_rate_10d=0.3,
                avg_return_10d=-2.4,
                baseline_avg_return_10d=0.2,
                lift_vs_baseline_10d=-2.6,
                reliability_score=0.24,
                weight_action="降低",
                suggested_weight_delta=-0.05,
                reason="短线过热样本显著跑输基准。",
            ),
        ],
        data_health=OFFICIAL_CALIBRATION_HEALTH,
    )

    before_winner = winner.rank_score
    before_loser = loser.rank_score

    apply_recommendation_feedback_calibration([winner, loser], center)

    assert winner.rank_score > before_winner
    assert loser.rank_score < before_loser
    assert winner.rank_score > loser.rank_score
    assert any("推荐反馈校准" in reason for reason in winner.rank_reasons)
    health = recommendation_feedback_data_health([winner, loser])
    assert health["recommendation_feedback_cards"] == "2"
    assert health["recommendation_feedback_adjusted"] == "2"


def test_recommendation_feedback_blocks_signals_with_bad_followthrough():
    card = _card("CN:002747", "埃斯顿 002747.SZ", 0.74, ["trend_momentum"])
    apply_recommendation_quality_gate([card])
    center = RecommendationCalibrationCenter(
        as_of=date(2026, 7, 6),
        headline="推荐校准：部分信号转弱",
        verdict="谨慎",
        reliability_score=0.72,
        baseline_win_rate_10d=0.46,
        baseline_avg_return_10d=0.3,
        score_bands=[],
        signal_effects=[
            RecommendationSignalEffect(
                signal_key="trend_momentum",
                label="二阶段趋势动量",
                sample_count=9,
                completed_count=6,
                win_rate_10d=0.25,
                avg_return_10d=-2.8,
                baseline_avg_return_10d=0.3,
                lift_vs_baseline_10d=-3.1,
                reliability_score=0.7,
                weight_action="降低",
                suggested_weight_delta=-0.08,
                reason="最近推荐后 10 日收益显著弱于基准。",
            )
        ],
        data_health=OFFICIAL_CALIBRATION_HEALTH,
    )

    apply_recommendation_feedback_quality_gate([card], center)

    assert card.decision is not None
    assert card.decision.action == "avoid"
    assert card.decision.risk_status == "blocked"
    assert card.recommendation_quality is not None
    assert card.recommendation_quality.tier == "risk_filtered"
    assert any(
        check.code == "feedback_quality_gate" for check in card.recommendation_quality.checks
    )
    assert card.pre_trade_risk is not None
    assert card.pre_trade_risk.can_buy is False
    health = recommendation_feedback_data_health([card])
    assert health["recommendation_feedback_blocked"] == "1"


def test_paper_trading_feedback_demotes_failed_strategy_and_etf_cluster():
    failed_etf = _card("CN:588200", "科创芯片ETF嘉实 588200.SH", 0.78, ["trend_momentum_stage2"])
    failed_etf.primary_strategy_id = "trend_momentum_stage2"
    unrelated = _card("CN:600519", "贵州茅台 600519.SH", 0.78, ["quality_compounder"])
    unrelated.primary_strategy_id = "quality_compounder"
    report = _paper_report(
        [
            PaperFailureAttributionItem(
                dimension="strategy",
                key="trend_momentum_stage2",
                label="二阶段趋势动量",
                total_trades=8,
                evaluated_trades=6,
                closed_trades=5,
                stopped_trades=4,
                target_hit_trades=0,
                win_rate=0.17,
                average_return_pct=-2.8,
                total_pnl=-Decimal("820"),
                total_return_pct=-4.4,
                worst_return_pct=-6.8,
                verdict="drag",
                note="模拟盘中该策略连续止损。",
            ),
            PaperFailureAttributionItem(
                dimension="asset",
                key="etf",
                label="ETF",
                total_trades=5,
                evaluated_trades=5,
                closed_trades=4,
                stopped_trades=4,
                target_hit_trades=0,
                win_rate=0.0,
                average_return_pct=-3.2,
                total_pnl=-Decimal("650"),
                total_return_pct=-5.3,
                worst_return_pct=-7.2,
                verdict="drag",
                note="ETF 组合近期模拟盘表现偏弱。",
            ),
        ]
    )

    before_failed = failed_etf.rank_score
    before_unrelated = unrelated.rank_score

    apply_paper_trading_feedback([failed_etf, unrelated], report)

    assert failed_etf.rank_score < before_failed
    assert unrelated.rank_score == before_unrelated
    assert any("模拟盘反馈" in reason for reason in failed_etf.rank_reasons)
    assert any("二阶段趋势动量" in note for note in failed_etf.calibration_notes)
    health = paper_trading_feedback_data_health([failed_etf, unrelated])
    assert health["paper_feedback_adjusted"] == "1"
    assert health["paper_feedback_blocked"] == "0"


def test_paper_trading_feedback_promotes_contributing_strategy():
    winner = _card("CN:688981", "中芯国际 688981.SH", 0.70, ["quality_compounder"])
    winner.primary_strategy_id = "quality_compounder"
    unrelated = _card("CN:002747", "埃斯顿 002747.SZ", 0.70, ["trend_momentum_stage2"])
    unrelated.primary_strategy_id = "trend_momentum_stage2"
    report = _paper_report(
        [
            PaperFailureAttributionItem(
                dimension="strategy",
                key="quality_compounder",
                label="质量因子",
                total_trades=7,
                evaluated_trades=5,
                closed_trades=4,
                stopped_trades=1,
                target_hit_trades=3,
                win_rate=0.75,
                average_return_pct=4.6,
                total_pnl=Decimal("1380"),
                total_return_pct=7.1,
                worst_return_pct=-1.2,
                verdict="contributor",
                note="质量因子近期贡献正收益。",
            )
        ]
    )

    before_winner = winner.rank_score
    before_unrelated = unrelated.rank_score

    apply_paper_trading_feedback([winner, unrelated], report)

    assert winner.rank_score > before_winner
    assert unrelated.rank_score == before_unrelated
    assert any("模拟盘反馈加权" in reason for reason in winner.rank_reasons)
    health = paper_trading_feedback_data_health([winner, unrelated])
    assert health["paper_feedback_adjusted"] == "1"


def test_recommendation_calibration_tracks_strategy_factor_industry_and_regime():
    pairs = []
    for index, return_10d in enumerate((2.4, -1.2), start=1):
        snapshot = OpportunitySnapshotRecord(
            snapshot_id=f"pit-calibration-{index}",
            run_id="pit-calibration",
            card_id=f"card-{index}",
            instrument_id=f"CN:68898{index}",
            market="CN",
            status="setup_ready",
            signal_date=date(2026, 6, index),
            latest_close=Decimal("100"),
            primary_strategy_id="trend_momentum_stage2",
            score=Decimal("0.80"),
            strategy_score=Decimal("0.80"),
            rank_score=Decimal("0.80"),
            trigger_price=Decimal("100"),
            initial_stop=Decimal("95"),
            target_1=Decimal("110"),
            card={
                "instrument_label": f"样本 {index}",
                "market_context": {
                    "industry": "半导体",
                    "themes": ["国产替代"],
                },
                "market_regime": {"regime": "risk_off"},
                "factor_flags": ["overextended"],
                "factor_exposures": [
                    {"factor_id": "quality", "score": 0.82},
                    {"factor_id": "trend_quality", "score": 0.76},
                ],
            },
        )
        outcome = OpportunityOutcome(
            snapshot_id=snapshot.snapshot_id,
            run_id=snapshot.run_id,
            instrument_id=snapshot.instrument_id,
            instrument_label=f"样本 {index}",
            primary_strategy_id=snapshot.primary_strategy_id,
            signal_date=snapshot.signal_date,
            outcome_status="resolved",
            return_5d=return_10d / 2,
            return_10d=return_10d,
            return_20d=return_10d * 1.2,
            max_drawdown_pct=-3.0,
            max_runup_pct=4.0,
        )
        pairs.append((snapshot, outcome))

    center = build_recommendation_calibration_center(
        pairs,
        authenticated_admission_sources={
            snapshot.snapshot_id: "ranking_v3_production" for snapshot, _ in pairs
        },
    )
    dimensions = {(effect.dimension, effect.signal_key) for effect in center.signal_effects}

    assert ("strategy", "trend_momentum_stage2") in dimensions
    assert ("factor", "quality") in dimensions
    assert ("industry", "半导体") in dimensions
    assert ("theme", "国产替代") in dimensions
    assert ("market_regime", "risk_off") in dimensions
    assert center.recent_samples[0].industry == "半导体"
    assert center.recent_samples[0].market_regime == "risk_off"
    assert "quality" in center.recent_samples[0].factor_ids
    assert center.data_health["recommendation_calibration_market_regime_effects"] == "1"


def test_recommendation_calibration_excludes_legacy_when_official_samples_exist():
    pairs = []
    definitions = (
        ("official", "ranking_v3_production", 3.0),
        ("manual", "legacy_manual", -20.0),
        ("shadow", "ranking_v4_shadow", -30.0),
    )
    for index, (name, admission_source, return_10d) in enumerate(
        definitions,
        start=1,
    ):
        card: dict[str, object] = {"instrument_label": name}
        if admission_source is not None:
            card["paper_admission"] = {"admission_source": admission_source}
        snapshot = OpportunitySnapshotRecord(
            snapshot_id=f"calibration-{name}",
            run_id="calibration-source-scope",
            card_id=f"card-{name}",
            instrument_id=f"CN:60000{index}",
            market="CN",
            status="setup_ready",
            signal_date=date(2026, 7, index),
            latest_close=Decimal("10"),
            primary_strategy_id="trend_momentum_stage2",
            score=Decimal("0.80"),
            strategy_score=Decimal("0.80"),
            rank_score=Decimal("0.80"),
            trigger_price=Decimal("10"),
            initial_stop=Decimal("9"),
            target_1=Decimal("12"),
            card=card,
        )
        pairs.append(
            (
                snapshot,
                OpportunityOutcome(
                    snapshot_id=snapshot.snapshot_id,
                    run_id=snapshot.run_id,
                    instrument_id=snapshot.instrument_id,
                    instrument_label=name,
                    primary_strategy_id=snapshot.primary_strategy_id,
                    signal_date=snapshot.signal_date,
                    outcome_status="resolved",
                    return_5d=return_10d / 2,
                    return_10d=return_10d,
                    return_20d=return_10d,
                    max_drawdown_pct=-2,
                    max_runup_pct=4,
                ),
            )
        )

    center = build_recommendation_calibration_center(
        pairs,
        authenticated_admission_sources={
            "calibration-official": "ranking_v3_production",
            "calibration-manual": "legacy_manual",
            "calibration-shadow": "ranking_v4_shadow",
        },
    )

    assert center.baseline_win_rate_10d == 1.0
    assert center.baseline_avg_return_10d == 3.0
    assert [sample.snapshot_id for sample in center.recent_samples] == ["calibration-official"]
    assert center.recent_samples[0].admission_source == "ranking_v3_production"
    assert center.data_health["recommendation_calibration_scope"] == ("ranking_v3_production")
    assert center.data_health["recommendation_calibration_source_total"] == "3"
    assert center.data_health["recommendation_calibration_source_official"] == "1"
    assert center.data_health["recommendation_calibration_source_research_shadow"] == "1"
    assert center.data_health["recommendation_calibration_source_legacy_manual"] == "1"
    assert center.data_health["recommendation_calibration_source_legacy_unknown"] == "0"
    assert center.data_health["recommendation_calibration_source_excluded"] == "2"
    assert center.data_health["recommendation_calibration_samples"] == "1"


def test_recommendation_calibration_fails_closed_when_card_forges_official_source():
    snapshot = OpportunitySnapshotRecord(
        snapshot_id="forged-official-card",
        run_id="forged-official-run",
        card_id="forged-official-card",
        instrument_id="CN:600001",
        market="CN",
        status="setup_ready",
        signal_date=date(2026, 7, 1),
        latest_close=Decimal("10"),
        primary_strategy_id="trend_momentum_stage2",
        score=Decimal("0.90"),
        strategy_score=Decimal("0.90"),
        rank_score=Decimal("0.90"),
        trigger_price=Decimal("10"),
        initial_stop=Decimal("9"),
        target_1=Decimal("12"),
        card={
            "paper_admission": {"admission_source": "ranking_v3_production"},
            "recommendation_provenance": {
                "admission_source": "ranking_v3_production"
            },
        },
    )
    outcome = OpportunityOutcome(
        snapshot_id=snapshot.snapshot_id,
        run_id=snapshot.run_id,
        instrument_id=snapshot.instrument_id,
        primary_strategy_id=snapshot.primary_strategy_id,
        signal_date=snapshot.signal_date,
        outcome_status="resolved",
        return_10d=99.0,
    )

    center = build_recommendation_calibration_center([(snapshot, outcome)])

    assert center.recent_samples == []
    assert center.signal_effects == []
    assert all(suggestion.delta == 0 for suggestion in center.weight_suggestions)
    assert center.baseline_avg_return_10d is None
    assert center.data_health["recommendation_calibration_scope"] == (
        "ranking_v3_production"
    )
    assert center.data_health["recommendation_calibration_fail_closed"] == "true"
    assert center.data_health["recommendation_calibration_source_official"] == "0"
    assert center.data_health["recommendation_calibration_source_legacy_unknown"] == "1"


def test_legacy_calibration_requires_explicit_legacy_scope():
    snapshot = OpportunitySnapshotRecord(
        snapshot_id="legacy-report-sample",
        run_id="legacy-report-run",
        card_id="legacy-report-card",
        instrument_id="CN:600002",
        market="CN",
        status="setup_ready",
        signal_date=date(2026, 7, 1),
        latest_close=Decimal("10"),
        primary_strategy_id="legacy_strategy",
        score=Decimal("0.70"),
        strategy_score=Decimal("0.70"),
        rank_score=Decimal("0.70"),
        trigger_price=Decimal("10"),
        initial_stop=Decimal("9"),
        target_1=Decimal("12"),
        card={},
    )
    outcome = OpportunityOutcome(
        snapshot_id=snapshot.snapshot_id,
        run_id=snapshot.run_id,
        instrument_id=snapshot.instrument_id,
        primary_strategy_id=snapshot.primary_strategy_id,
        signal_date=snapshot.signal_date,
        outcome_status="resolved",
        return_10d=2.0,
    )

    official = build_recommendation_calibration_center([(snapshot, outcome)])
    legacy = build_recommendation_calibration_center(
        [(snapshot, outcome)],
        reporting_scope="legacy",
    )

    assert official.recent_samples == []
    assert legacy.data_health["recommendation_calibration_scope"] == "legacy_only"
    assert [sample.snapshot_id for sample in legacy.recent_samples] == [
        "legacy-report-sample"
    ]
    assert legacy.baseline_avg_return_10d == 2.0


def test_paper_feedback_matches_pit_dimensions_without_double_counting():
    card = _card("CN:688981", "中芯国际 688981.SH", 0.76, ["quality"])
    card.market_context = MarketContext(
        board="科创板",
        industry="半导体",
        themes=["国产替代"],
        summary="半导体；国产替代",
    ).model_copy(update={"market_regime": "risk_off"})
    report = _paper_report(
        [
            _paper_attribution("factor", "quality", "质量因子"),
            _paper_attribution("industry", "半导体", "半导体"),
            _paper_attribution("market_regime", "risk_off", "risk_off"),
        ]
    )
    before = card.rank_score

    apply_paper_trading_feedback([card], report)

    assert card.rank_score == before - 0.10
    assert any("模拟盘反馈降权" in reason for reason in card.rank_reasons)


def test_recommendation_calibration_normalizes_correlated_dimensions():
    single = _card("CN:688981", "中芯国际 688981.SH", 0.76, ["quality"])
    multiple = _card("CN:688981", "中芯国际 688981.SH", 0.76, ["quality"])
    for card in (single, multiple):
        card.market_context = MarketContext(
            board="科创板",
            industry="半导体",
            themes=[],
            summary="半导体",
        )
    base_effect = RecommendationSignalEffect(
        dimension="factor",
        signal_key="quality",
        label="质量因子",
        sample_count=10,
        completed_count=8,
        win_rate_10d=0.25,
        avg_return_10d=-2.0,
        baseline_avg_return_10d=0.2,
        lift_vs_baseline_10d=-2.2,
        reliability_score=0.6,
        weight_action="降低",
        suggested_weight_delta=-0.04,
        reason="近期表现偏弱。",
    )
    industry_effect = base_effect.model_copy(
        update={
            "dimension": "industry",
            "signal_key": "半导体",
            "label": "半导体",
        }
    )
    single_center = RecommendationCalibrationCenter(
        as_of=date(2026, 7, 1),
        headline="校准",
        verdict="观察",
        reliability_score=1.0,
        signal_effects=[base_effect],
        data_health=OFFICIAL_CALIBRATION_HEALTH,
    )
    multiple_center = single_center.model_copy(
        update={"signal_effects": [base_effect, industry_effect]}
    )

    apply_recommendation_feedback_calibration([single], single_center)
    apply_recommendation_feedback_calibration([multiple], multiple_center)

    assert single.rank_score == multiple.rank_score == 0.72


def test_recommendation_calibration_matches_explicit_signal_date_market_regime():
    card = _card("CN:688981", "中芯国际 688981.SH", 0.76, [])
    card.market_context = MarketContext(
        board="科创板",
        industry="半导体",
        themes=[],
        summary="半导体",
    ).model_copy(update={"market_regime": "risk_off"})
    center = RecommendationCalibrationCenter(
        as_of=date(2026, 7, 1),
        headline="校准",
        verdict="观察",
        reliability_score=1.0,
        signal_effects=[
            RecommendationSignalEffect(
                dimension="market_regime",
                signal_key="risk_off",
                label="弱市",
                sample_count=10,
                completed_count=8,
                win_rate_10d=0.25,
                avg_return_10d=-2.0,
                baseline_avg_return_10d=0.2,
                lift_vs_baseline_10d=-2.2,
                reliability_score=0.6,
                weight_action="降低",
                suggested_weight_delta=-0.04,
                reason="弱市中的推荐近期表现偏弱。",
            )
        ],
        data_health=OFFICIAL_CALIBRATION_HEALTH,
    )

    apply_recommendation_feedback_calibration([card], center)

    assert card.rank_score == 0.72
    assert any("弱市" in reason for reason in card.rank_reasons)


def test_walk_forward_feedback_requires_mature_out_of_sample_evidence():
    blocked = _card("CN:002747", "埃斯顿 002747.SZ", 0.74, ["trend_momentum"])
    blocked.primary_strategy_id = "trend_momentum_stage2"
    immature = _card("CN:688981", "中芯国际 688981.SH", 0.74, ["quality_factor"])
    immature.primary_strategy_id = "quality_compounder"
    apply_recommendation_quality_gate([blocked, immature])
    validation = {
        "status": "rejected",
        "strategies": [
            {
                "dimension": "strategy",
                "key": "trend_momentum_stage2",
                "label": "二阶段趋势动量",
                "out_of_sample_count": 36,
                "action": "disable",
                "suggested_weight_delta": -0.10,
            },
            {
                "dimension": "strategy",
                "key": "quality_compounder",
                "label": "质量因子",
                "out_of_sample_count": 12,
                "action": "disable",
                "suggested_weight_delta": -0.10,
            },
        ],
        "factors": [],
    }

    before_immature = immature.rank_score
    apply_walk_forward_validation_feedback([blocked, immature], validation)

    assert blocked.decision is not None
    assert blocked.decision.action == "avoid"
    assert blocked.pre_trade_risk is not None
    assert blocked.pre_trade_risk.can_buy is False
    assert blocked.rank_score <= 0.35
    assert immature.rank_score == before_immature
    assert not any("样本外门禁" in reason for reason in immature.rank_reasons)
    health = walk_forward_feedback_data_health([blocked, immature], validation)
    assert health["walk_forward_feedback_blocked"] == "1"


def test_walk_forward_positive_weight_requires_accepted_release_gate():
    card = _card("CN:688981", "中芯国际 688981.SH", 0.70, ["quality_factor"])
    validation = {
        "status": "insufficient",
        "strategies": [],
        "factors": [
            {
                "dimension": "factor",
                "key": "quality_factor",
                "label": "质量因子",
                "out_of_sample_count": 40,
                "action": "increase",
                "suggested_weight_delta": 0.04,
            }
        ],
    }
    before = card.rank_score

    apply_walk_forward_validation_feedback([card], validation)
    assert card.rank_score == before

    validation["status"] = "accepted"
    apply_walk_forward_validation_feedback([card], validation)
    assert card.rank_score > before
    assert any("样本外校准" in reason for reason in card.rank_reasons)


def _card(instrument_id: str, label: str, score: float, flags: list[str]):
    card = build_factor_watch_card(
        instrument_id,
        _bars(instrument_id),
        FactorRanking(
            instrument_id=instrument_id,
            instrument_label=label,
            factor_score=score,
            factor_rank=1,
            percentile=score,
            momentum_score=score,
            trend_quality_score=max(0.1, score - 0.04),
            liquidity_score=0.82,
            low_risk_score=0.68,
            reversal_score=0.52,
            execution_penalty=0.0,
            data_completeness=0.9,
            factor_exposures=[],
            flags=flags,
            missing_data=[],
        ),
    )
    assert card is not None
    card.factor_flags = flags
    card.rank_score = score
    return card


def _bars(instrument_id: str) -> pd.DataFrame:
    start = date(2026, 1, 1)
    rows = []
    for index in range(100):
        close = 20 + index * 0.08
        rows.append(
            {
                "instrument_id": instrument_id,
                "trade_date": start + timedelta(days=index),
                "open": close - 0.05,
                "high": close + 0.14,
                "low": close - 0.14,
                "close": close,
                "volume": 2_100_000 + index * 3_500,
                "provider": "fixture",
            }
        )
    return pd.DataFrame(rows)


def _paper_report(failure_attribution: list[PaperFailureAttributionItem]) -> PaperDailyReport:
    return PaperDailyReport(
        report_date=date(2026, 7, 8),
        summary=PaperDailyReportSummary(
            total_trades=10,
            new_opportunities=0,
            triggered_today=0,
            open_positions=1,
            closed_today=2,
            target_hits_today=0,
            stopped_today=2,
            total_return_pct=-4.2,
            max_drawdown_pct=-6.4,
            win_rate=0.18,
        ),
        benchmark=PaperDailyBenchmark(total_return_pct=-1.0, items=[], summary="跑输基准。"),
        risk_gate=PaperRiskGateStatus(
            action="pause_new_entries",
            can_add_entries=False,
            title="暂停新增",
            reason="模拟盘连续止损。",
            reasons=["连续止损", "跑输基准"],
            recovery_conditions=["等待止损率下降", "恢复目标命中"],
        ),
        failure_attribution=failure_attribution,
        event_timeline=[],
        new_opportunities=[],
        triggered_today=[],
        holdings=[],
        closed_today=[],
        asset_groups=[],
        next_trade_day_focus=[],
        data_health={
            "paper_reporting_scope": "ranking_v3_production",
            "paper_reporting_fail_closed": "true",
            "paper_reporting_official": "10",
        },
    )


def _paper_attribution(
    dimension: str,
    key: str,
    label: str,
) -> PaperFailureAttributionItem:
    return PaperFailureAttributionItem(
        dimension=dimension,
        key=key,
        label=label,
        total_trades=5,
        evaluated_trades=4,
        closed_trades=4,
        stopped_trades=3,
        target_hit_trades=0,
        win_rate=0.0,
        average_return_pct=-3.0,
        total_pnl=-Decimal("500"),
        total_return_pct=-4.0,
        worst_return_pct=-6.0,
        verdict="drag",
        note="该信号时点维度近期拖累模拟盘。",
    )
