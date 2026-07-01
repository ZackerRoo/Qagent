from collections.abc import Iterable

from qagent.domain.models import (
    DataQualityAudit,
    DataQualityIssue,
    MarketContext,
    TradabilityAssessment,
    TradingStatus,
)


def assess_instrument_data_quality(
    instrument_id: str,
    instrument_label: str | None,
    bars,
    *,
    trading_status: TradingStatus | None = None,
    tradability: TradabilityAssessment | None = None,
    market_context: MarketContext | None = None,
    adjusted_price_ready: bool = False,
) -> DataQualityAudit:
    if not instrument_id.startswith("CN:"):
        return DataQualityAudit(
            status="ready",
            score=1.0,
            can_recommend=True,
            issues=[],
            summary="非A股标的暂不使用A股专项数据质量闸门。",
        )

    issues: list[DataQualityIssue] = []
    if bars.empty:
        issues.append(
            _issue(
                "no_daily_bars",
                "block",
                "缺少日线行情",
                "数据源没有返回日线 OHLCV，无法判断趋势、成交额和交易状态。",
                "补齐日线行情后再进入推荐池。",
            )
        )
        return _audit(issues)

    ordered = bars.sort_values("trade_date").reset_index(drop=True)
    latest = ordered.iloc[-1]
    latest_close = _numeric(latest.get("close"))
    latest_volume = _numeric(latest.get("volume"))

    if latest_close is None or latest_close <= 0:
        issues.append(
            _issue(
                "invalid_latest_price",
                "block",
                "最新价格异常",
                "最新收盘价缺失或小于等于 0，不能用于买点和止损测算。",
                "换用可用行情源或等待下一次有效行情。",
            )
        )
    if latest_volume is not None and latest_volume <= 0:
        issues.append(
            _issue(
                "no_recent_volume",
                "block",
                "疑似停牌或无成交",
                "最新交易日成交量为 0，默认不进入买入候选。",
                "等待恢复成交后再重新扫描。",
            )
        )
    if _looks_risk_warning(instrument_label or instrument_id):
        issues.append(
            _issue(
                "risk_warning_name",
                "block",
                "风险警示名称",
                "名称包含 ST、*ST 或退市风险特征，默认不纳入新开仓推荐。",
                "仅保留观察，不参与买入候选排序。",
            )
        )

    if trading_status and not trading_status.can_buy:
        issues.append(
            _issue(
                f"trading_status_{trading_status.status}",
                "block",
                trading_status.label,
                " ".join(trading_status.notes),
                "不要追买；等待交易状态恢复正常后再评估。",
            )
        )

    if tradability:
        for check in tradability.checks:
            if check.severity not in {"block", "warning"}:
                continue
            issues.append(
                _issue(
                    check.code,
                    check.severity,
                    check.title,
                    check.message,
                    "先排除该可交易性问题，再考虑开仓。",
                )
            )

    if not adjusted_price_ready and not _has_adjusted_price(ordered):
        issues.append(
            _issue(
                "missing_adjusted_price",
                "warning",
                "复权价格未确认",
                "当前行情没有明确复权字段，均线、涨幅和回测结果需要打折看。",
                "正式使用前接入前/后复权行情并重跑扫描。",
            )
        )

    if market_context is None:
        issues.append(
            _issue(
                "missing_market_context",
                "warning",
                "行业主题缺失",
                "缺少行业、主题或指数成分上下文，板块比较和组合分散度可信度下降。",
                "补齐行业分类、指数成分和主题映射。",
            )
        )
    elif not market_context.index_memberships and market_context.board != "ETF":
        issues.append(
            _issue(
                "missing_index_constituents",
                "warning",
                "指数成分未确认",
                "未确认所属主要指数或板块成分，和指数基准比较时需要谨慎。",
                "补齐指数成分数据后再做横向比较。",
            )
        )

    return _audit(issues)


def summarize_data_quality_audits(audits: Iterable[DataQualityAudit]) -> dict[str, str]:
    items = list(audits)
    if not items:
        return {
            "a_share_quality_audited": "0",
            "a_share_quality_ready": "0",
            "a_share_quality_watch": "0",
            "a_share_quality_blocked": "0",
            "a_share_quality_score": "0.00",
        }
    issue_counts: dict[str, int] = {}
    for audit in items:
        for issue in audit.issues:
            issue_counts[issue.code] = issue_counts.get(issue.code, 0) + 1
    top_issues = ",".join(
        code for code, _ in sorted(issue_counts.items(), key=lambda item: (-item[1], item[0]))[:5]
    )
    return {
        "a_share_quality_audited": str(len(items)),
        "a_share_quality_ready": str(sum(audit.status == "ready" for audit in items)),
        "a_share_quality_watch": str(sum(audit.status == "watch" for audit in items)),
        "a_share_quality_blocked": str(sum(audit.status == "blocked" for audit in items)),
        "a_share_quality_score": f"{sum(audit.score for audit in items) / len(items):.2f}",
        **({"a_share_quality_top_issues": top_issues} if top_issues else {}),
    }


def _audit(issues: list[DataQualityIssue]) -> DataQualityAudit:
    deduped = _dedupe_issues(issues)
    block_count = sum(issue.severity == "block" for issue in deduped)
    warning_count = sum(issue.severity == "warning" for issue in deduped)
    score = max(0.0, min(1.0, 1.0 - block_count * 0.25 - warning_count * 0.08))
    if block_count:
        status = "blocked"
        summary = f"数据质量阻断：{block_count} 个硬风险，暂不进入买入候选。"
    elif warning_count:
        status = "watch"
        summary = f"数据质量观察：{warning_count} 个待补齐项，推荐结果需要降权。"
    else:
        status = "ready"
        summary = "数据质量可用：未发现 A 股专项硬阻断。"
    return DataQualityAudit(
        status=status,
        score=round(score, 4),
        can_recommend=block_count == 0,
        issues=deduped,
        summary=summary,
    )


def _issue(
    code: str,
    severity: str,
    title: str,
    message: str,
    action: str,
) -> DataQualityIssue:
    return DataQualityIssue(
        code=code,
        severity="warning" if severity == "warn" else severity,
        title=title,
        message=message,
        action=action,
    )


def _dedupe_issues(issues: list[DataQualityIssue]) -> list[DataQualityIssue]:
    severity_rank = {"block": 0, "warning": 1, "info": 2}
    result: list[DataQualityIssue] = []
    seen: set[str] = set()
    for issue in sorted(issues, key=lambda item: severity_rank.get(item.severity, 3)):
        if issue.code in seen:
            continue
        seen.add(issue.code)
        result.append(issue)
    return result


def _looks_risk_warning(label: str) -> bool:
    normalized = label.upper().replace(" ", "")
    return (
        normalized.startswith("*ST")
        or normalized.startswith("ST")
        or "退" in normalized[:8]
        or "退市" in normalized
    )


def _numeric(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed


def _has_adjusted_price(bars) -> bool:
    adjusted_columns = {
        "adj_close",
        "adjusted_close",
        "adjust_factor",
        "复权收盘",
        "前复权收盘",
        "后复权收盘",
    }
    return any(column in bars.columns for column in adjusted_columns)
