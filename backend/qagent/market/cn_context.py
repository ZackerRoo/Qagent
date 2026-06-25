from qagent.domain.models import MarketContext
from qagent.recommendations.cn_execution import build_trading_constraints


KNOWN_CONTEXT: dict[str, dict[str, list[str] | str]] = {
    "000001": {
        "industry": "银行",
        "themes": ["大金融", "低估值红利"],
        "index_memberships": ["沪深300"],
    },
    "000063": {
        "industry": "通信设备",
        "themes": ["AI算力供应链", "5G", "国产替代"],
        "index_memberships": ["沪深300"],
    },
    "300033": {
        "industry": "金融科技",
        "themes": ["证券IT", "AI应用"],
        "index_memberships": ["创业板指"],
    },
    "300059": {
        "industry": "互联网券商",
        "themes": ["大金融", "金融科技"],
        "index_memberships": ["创业板50", "沪深300"],
    },
    "300750": {
        "industry": "电池",
        "themes": ["新能源车", "储能"],
        "index_memberships": ["创业板50", "沪深300"],
    },
    "600519": {
        "industry": "白酒",
        "themes": ["消费龙头", "核心资产"],
        "index_memberships": ["沪深300"],
    },
    "688981": {
        "industry": "半导体",
        "themes": ["AI算力供应链", "国产替代", "晶圆代工"],
        "index_memberships": ["科创50"],
    },
    "588000": {
        "industry": "指数ETF",
        "themes": ["科创板", "硬科技"],
        "index_memberships": ["科创50ETF"],
    },
}


def build_market_context(
    instrument_id: str,
    instrument_label: str | None = None,
) -> MarketContext | None:
    if not instrument_id.startswith("CN:"):
        return None

    symbol = instrument_id.split(":", 1)[1]
    constraints = build_trading_constraints(instrument_id, instrument_label)
    board = constraints.board if constraints else "A股"
    known = KNOWN_CONTEXT.get(symbol, {})
    industry = str(known.get("industry") or _infer_industry(symbol, instrument_label, board))
    themes = _as_list(known.get("themes")) or _infer_themes(symbol, instrument_label, industry, board)
    memberships = _as_list(known.get("index_memberships")) or _infer_index_memberships(symbol, board)

    parts = [industry]
    if themes:
        parts.append("、".join(themes[:3]))
    if memberships:
        parts.append("成分/跟踪：" + "、".join(memberships[:2]))
    return MarketContext(
        board=board,
        industry=industry,
        themes=themes,
        index_memberships=memberships,
        summary="；".join(parts),
    )


def _as_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return []


def _infer_industry(symbol: str, label: str | None, board: str) -> str:
    text = label or ""
    if "银行" in text:
        return "银行"
    if "证券" in text or "财富" in text:
        return "证券/金融服务"
    if "芯" in text or "半导体" in text:
        return "半导体"
    if "ETF" in text.upper() or board == "ETF":
        return "指数ETF"
    if symbol.startswith("688"):
        return "硬科技"
    if symbol.startswith(("300", "301")):
        return "成长制造"
    return "综合"


def _infer_themes(symbol: str, label: str | None, industry: str, board: str) -> list[str]:
    themes: list[str] = []
    text = label or ""
    if industry in {"半导体", "硬科技"}:
        themes.extend(["AI算力供应链", "国产替代"])
    if industry == "银行":
        themes.extend(["大金融", "低估值红利"])
    if board == "科创板":
        themes.append("科创板")
    if board == "创业板":
        themes.append("成长股")
    if board == "北交所":
        themes.append("专精特新")
    if "ETF" in text.upper():
        themes.append("指数工具")
    return _dedupe(themes)


def _infer_index_memberships(symbol: str, board: str) -> list[str]:
    if board == "科创板":
        return ["科创板"]
    if board == "创业板":
        return ["创业板"]
    if board == "ETF":
        return ["ETF"]
    return []


def _dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        if item and item not in result:
            result.append(item)
    return result
