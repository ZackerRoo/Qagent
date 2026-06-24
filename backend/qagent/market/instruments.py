CN_INSTRUMENT_NAMES = {
    "000001": "平安银行",
    "000063": "中兴通讯",
    "002230": "科大讯飞",
    "002415": "海康威视",
    "002475": "立讯精密",
    "300124": "汇川技术",
    "300750": "宁德时代",
    "300760": "迈瑞医疗",
    "600036": "招商银行",
    "600519": "贵州茅台",
    "601318": "中国平安",
    "601398": "工商银行",
}

US_INSTRUMENT_NAMES = {
    "AAPL": "Apple",
    "MSFT": "Microsoft",
    "NVDA": "NVIDIA",
    "TEST": "样例测试",
}


def format_instrument_label(instrument_id: str | None) -> str:
    symbol = market_symbol(instrument_id)
    if not symbol:
        return "-"
    market = _market(instrument_id)
    if market == "CN":
        name = CN_INSTRUMENT_NAMES.get(symbol)
        exchange_symbol = f"{symbol}.{_cn_exchange_suffix(symbol)}"
        return f"{name} {exchange_symbol}" if name else exchange_symbol
    if market == "US":
        name = US_INSTRUMENT_NAMES.get(symbol)
        return f"{name} {symbol}" if name else symbol
    return symbol


def market_symbol(instrument_id: str | None) -> str:
    if not instrument_id:
        return ""
    value = instrument_id.strip().upper()
    if ":" in value:
        return value.split(":", 1)[1]
    return value


def _market(instrument_id: str | None) -> str:
    if not instrument_id:
        return ""
    value = instrument_id.strip().upper()
    return value.split(":", 1)[0] if ":" in value else ""


def _cn_exchange_suffix(symbol: str) -> str:
    if symbol.startswith(("4", "8")):
        return "BJ"
    if symbol.startswith("6"):
        return "SH"
    return "SZ"
