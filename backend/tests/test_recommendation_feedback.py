from datetime import date, timedelta
from decimal import Decimal

import pandas as pd

from qagent.cards.factor_watch import build_factor_watch_card
from qagent.factors.models import FactorRanking
from qagent.monitoring.recommendation_calibration import (
    RecommendationCalibrationBand,
    RecommendationCalibrationCenter,
    RecommendationSignalEffect,
)
from qagent.paper_trading.engine import (
    PaperDailyBenchmark,
    PaperDailyReport,
    PaperDailyReportSummary,
    PaperFailureAttributionItem,
    PaperRiskGateStatus,
)
from qagent.recommendations.feedback import (
    apply_recommendation_feedback_calibration,
    apply_recommendation_feedback_quality_gate,
    apply_paper_trading_feedback,
    paper_trading_feedback_data_health,
    recommendation_feedback_data_health,
)
from qagent.recommendations.quality_gate import apply_recommendation_quality_gate


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
    )

    apply_recommendation_feedback_quality_gate([card], center)

    assert card.decision is not None
    assert card.decision.action == "avoid"
    assert card.decision.risk_status == "blocked"
    assert card.recommendation_quality is not None
    assert card.recommendation_quality.tier == "risk_filtered"
    assert any(check.code == "feedback_quality_gate" for check in card.recommendation_quality.checks)
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
        data_health={},
    )
