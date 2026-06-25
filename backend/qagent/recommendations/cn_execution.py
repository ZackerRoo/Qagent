from qagent.domain.models import TradingConstraint, TradingConstraintProfile


def build_trading_constraints(
    instrument_id: str,
    instrument_label: str | None = None,
) -> TradingConstraintProfile | None:
    if not instrument_id.startswith("CN:"):
        return None

    symbol = instrument_id.split(":", 1)[1]
    board, price_limit_pct, permission_required = _board_rules(symbol)
    constraints = [
        TradingConstraint(
            code="t_plus_one",
            severity="info",
            title="A股T+1",
            message="A股普通股票通常当日买入、下一交易日起可卖出，盘中计划需要预留隔夜风险。",
        ),
        TradingConstraint(
            code="lot_size_100",
            severity="info",
            title="最小交易单位",
            message="普通A股买入通常按100股整数倍申报，仓位测算需要按手数取整。",
        ),
    ]
    if price_limit_pct is not None:
        constraints.append(
            TradingConstraint(
                code=f"price_limit_{price_limit_pct}",
                severity="info",
                title="涨跌幅约束",
                message=f"{board}常规涨跌幅约束按{price_limit_pct}%处理；新股上市初期、停复牌等特殊情形需单独确认。",
            )
        )
    if permission_required:
        constraints.append(_permission_constraint(board))
    if _looks_st(instrument_label):
        constraints.append(
            TradingConstraint(
                code="st_risk_warning",
                severity="block",
                title="风险警示",
                message="该标的疑似风险警示股票，建议默认排除或降低到观察级别。",
            )
        )

    return TradingConstraintProfile(
        board=board,
        price_limit_pct=price_limit_pct,
        permission_required=permission_required,
        t_plus_one=True,
        min_lot=100,
        constraints=constraints,
    )


def _board_rules(symbol: str) -> tuple[str, int | None, bool]:
    if _is_etf(symbol):
        return "ETF", 10, False
    if symbol.startswith("688"):
        return "科创板", 20, True
    if symbol.startswith(("300", "301")):
        return "创业板", 20, True
    if symbol.startswith(("4", "8", "920")):
        return "北交所", 30, True
    if symbol.startswith(("600", "601", "603", "605")):
        return "沪市主板", 10, False
    if symbol.startswith(("000", "001", "002", "003")):
        return "深市主板", 10, False
    return "A股", 10, False


def _permission_constraint(board: str) -> TradingConstraint:
    code = {
        "科创板": "star_market_permission",
        "创业板": "chinext_permission",
        "北交所": "bse_permission",
    }.get(board, "qualified_permission")
    return TradingConstraint(
        code=code,
        severity="warning",
        title="权限要求",
        message=f"{board}通常需要投资者开通对应交易权限；未开通时只能观察，不能直接买入。",
    )


def _is_etf(symbol: str) -> bool:
    return symbol.startswith(("15", "16", "51", "52", "56", "58"))


def _looks_st(label: str | None) -> bool:
    if not label:
        return False
    normalized = label.upper().replace("*", "").replace(" ", "")
    return normalized.startswith("ST") or "ST" in normalized[:8]
